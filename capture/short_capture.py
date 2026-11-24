"""Station-side receipt of recycled short-tag scans.

The device (``device.source.ShortSource``) keeps ownership of each original
block; the station only learns a scan belongs to a short-tag shift when the
control records and blocks are delivered through :meth:`Workday.accept_short`.
Receipts commit inside the station's existing transaction boundaries, so a
failed COMMIT leaves nothing on disk and the same original can be redelivered.

Synthetic event sequences are drawn from one shift-wide counter, which keeps
every block strictly between its scan's start and end sequences even when the
end record is handed over before delayed blocks arrive.
"""
import sqlite3

from capture.ledger import CaptureLedger
from capture.origin import BlockOrigin, CapturedBlock
from capture.retention import MaterialStore
from device.events import EventPosition, EventRejected
from device.source import ShortDelivery
from quality.blocks import check_block
from quality.closeout import evaluate_closeout
from quality.profile import ScanProfile
from quality.scanbook import ScanBook, ScanStateError
from workdb import transaction
def is_short_shift(connection: sqlite3.Connection, shift_id: str) -> bool:
    """Return whether a shift was opened for recycled short-tag collection."""
    names = {row['name'] for row in connection.execute('PRAGMA table_info(short_shifts)')}
    if not names:
        return False
    row = connection.execute(
        'SELECT 1 FROM short_shifts WHERE shift_id = ?', (shift_id,)
    ).fetchone()
    return row is not None


class ShortIntake:
    def __init__(
        self,
        connection: sqlite3.Connection,
        shift_id: str,
        profile: ScanProfile,
        scans: ScanBook,
        ledger: CaptureLedger,
        materials: MaterialStore,
    ):
        self.connection = connection
        self.shift_id = shift_id
        self.profile = profile
        self.scans = scans
        self.ledger = ledger
        self.materials = materials

    def initialize(self) -> None:
        with transaction(self.connection, write=True):
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS short_shifts (
                    shift_id TEXT PRIMARY KEY REFERENCES shifts(shift_id),
                    next_event_seq INTEGER NOT NULL CHECK (next_event_seq > 0),
                    next_end_order INTEGER NOT NULL CHECK (next_end_order > 0)
                )
            ''')
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS short_scans (
                    shift_id TEXT NOT NULL,
                    scan_id TEXT NOT NULL,
                    tag INTEGER NOT NULL CHECK (tag >= 0 AND tag < 256),
                    start_recording TEXT NOT NULL,
                    start_line INTEGER NOT NULL CHECK (start_line > 0),
                    end_recording TEXT,
                    end_line INTEGER,
                    end_order INTEGER,
                    expected_blocks INTEGER NOT NULL CHECK (expected_blocks >= 0),
                    received_blocks INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (shift_id, scan_id),
                    UNIQUE (shift_id, start_recording, start_line),
                    UNIQUE (shift_id, end_recording, end_line),
                    FOREIGN KEY (shift_id, scan_id) REFERENCES scans(shift_id, scan_id)
                )
            ''')
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS short_receipts (
                    shift_id TEXT NOT NULL,
                    scan_id TEXT NOT NULL,
                    recording TEXT NOT NULL,
                    recording_line INTEGER NOT NULL CHECK (recording_line > 0),
                    block_order INTEGER NOT NULL CHECK (block_order > 0),
                    PRIMARY KEY (shift_id, scan_id, recording, recording_line),
                    FOREIGN KEY (shift_id, scan_id) REFERENCES short_scans(shift_id, scan_id)
                )
            ''')
            self.connection.execute('''
                INSERT OR IGNORE INTO short_shifts (shift_id, next_event_seq, next_end_order)
                VALUES (?, 1, 1)
            ''', (self.shift_id,))

    def _next_event_seq(self) -> int:
        row = self.connection.execute('''
            SELECT next_event_seq FROM short_shifts WHERE shift_id = ?
        ''', (self.shift_id,)).fetchone()
        sequence = row['next_event_seq']
        self.connection.execute('''
            UPDATE short_shifts SET next_event_seq = ? WHERE shift_id = ?
        ''', (sequence + 1, self.shift_id))
        return sequence

    def _next_end_order(self) -> int:
        row = self.connection.execute('''
            SELECT next_end_order FROM short_shifts WHERE shift_id = ?
        ''', (self.shift_id,)).fetchone()
        order = row['next_end_order']
        self.connection.execute('''
            UPDATE short_shifts SET next_end_order = ? WHERE shift_id = ?
        ''', (order + 1, self.shift_id))
        return order

    def _short_row(self, scan_id: str) -> sqlite3.Row | None:
        return self.connection.execute('''
            SELECT * FROM short_scans WHERE shift_id = ? AND scan_id = ?
        ''', (self.shift_id, scan_id)).fetchone()

    def _position_known(self, position: EventPosition) -> bool:
        row = self.connection.execute('''
            SELECT 1 FROM short_scans
            WHERE shift_id = ?
              AND (start_recording = ? AND start_line = ?
                   OR (end_recording = ? AND end_line = ?))
            UNION
            SELECT 1 FROM short_receipts
            WHERE shift_id = ? AND recording = ? AND recording_line = ?
            LIMIT 1
        ''', (
            self.shift_id, position.recording, position.line,
            position.recording, position.line,
            self.shift_id, position.recording, position.line,
        )).fetchone()
        return row is not None

    def accept(self, delivery: ShortDelivery) -> None:
        if not isinstance(delivery, ShortDelivery):
            raise ValueError('short-tag handover requires a ShortDelivery record')
        position = delivery.position
        with transaction(self.connection, write=True):
            if delivery.kind == 'start':
                self._accept_start(delivery)
            elif delivery.kind == 'end':
                self._accept_end(delivery, position)
            elif delivery.kind == 'block':
                self._accept_block(delivery, position)
            else:
                raise EventRejected(position, f'unknown short-tag delivery kind {delivery.kind!r}')

    def _accept_start(self, delivery: ShortDelivery) -> None:
        if not delivery.scan_id.strip():
            raise EventRejected(delivery.position, 'short-tag start requires a scan id')
        if type(delivery.tag) is not int or not 0 <= delivery.tag < 256:
            raise EventRejected(delivery.position, 'short tag must be an unsigned byte')
        existing = self._short_row(delivery.scan_id)
        if existing is not None:
            # Same position redelivery of an already handed-over start is a no-op.
            if (existing['start_recording'], existing['start_line']) == (
                delivery.position.recording, delivery.position.line
            ):
                return
            raise EventRejected(delivery.position, f'scan {delivery.scan_id!r} is already registered')
        if self._position_known(delivery.position):
            raise EventRejected(delivery.position, 'recording position already belongs to another record')
        open_seq = self._next_event_seq()
        try:
            self.scans.open_short_scan(self.shift_id, delivery.scan_id, open_seq, self.profile)
        except ScanStateError as error:
            raise EventRejected(delivery.position, str(error)) from error
        self.connection.execute('''
            INSERT INTO short_scans
              (shift_id, scan_id, tag, start_recording, start_line, expected_blocks)
            VALUES (?, ?, ?, ?, ?, 0)
        ''', (
            self.shift_id, delivery.scan_id, delivery.tag,
            delivery.position.recording, delivery.position.line,
        ))

    def _accept_end(self, delivery: ShortDelivery, position: EventPosition) -> None:
        row = self._short_row(delivery.scan_id)
        if row is None:
            raise EventRejected(position, f'scan {delivery.scan_id!r} has no handed-over start')
        if row['end_recording'] is not None:
            if (row['end_recording'], row['end_line']) == (position.recording, position.line):
                return
            raise EventRejected(position, f'scan {delivery.scan_id!r} already has an end record')
        if self._position_known(position):
            raise EventRejected(position, 'recording position already belongs to another record')
        if type(delivery.expected_blocks) is not int or delivery.expected_blocks < 0:
            raise EventRejected(position, 'short-tag end requires a nonnegative block count')
        # Device end order is fixed when the end record itself is handed over,
        # independent of when the delayed originals let the scan close.
        end_order = self._next_end_order()
        self.connection.execute('''
            UPDATE short_scans
            SET end_recording = ?, end_line = ?, end_order = ?, expected_blocks = ?
            WHERE shift_id = ? AND scan_id = ?
        ''', (
            position.recording, position.line, end_order, delivery.expected_blocks,
            self.shift_id, delivery.scan_id,
        ))
        self._maybe_close(delivery.scan_id)

    def _accept_block(self, delivery: ShortDelivery, position: EventPosition) -> None:
        row = self._short_row(delivery.scan_id)
        if row is None:
            raise EventRejected(position, f'scan {delivery.scan_id!r} has no handed-over start')
        duplicate = self.connection.execute('''
            SELECT block_order FROM short_receipts
            WHERE shift_id = ? AND scan_id = ? AND recording = ? AND recording_line = ?
        ''', (
            self.shift_id, delivery.scan_id, position.recording, position.line,
        )).fetchone()
        if duplicate is not None:
            # Same original position redelivered: count it once, keep ownership.
            return
        if self._position_known(position):
            raise EventRejected(position, 'recording position already belongs to another record')
        block_order = row['received_blocks'] + 1
        sequence = self._next_event_seq()
        event_id = f'{delivery.scan_id}:{block_order}'
        origin = BlockOrigin(
            self.shift_id, delivery.scan_id, delivery.slot or '', event_id,
            sequence, position,
        )
        check = check_block(self.profile, origin.slot, delivery.value)
        self.ledger.record_receipt(origin, check.reason)
        if check.value is not None:
            self.materials.stage_block(CapturedBlock(origin, check.value))
        self.connection.execute('''
            INSERT INTO short_receipts (shift_id, scan_id, recording, recording_line, block_order)
            VALUES (?, ?, ?, ?, ?)
        ''', (self.shift_id, delivery.scan_id, position.recording, position.line, block_order))
        self.connection.execute('''
            UPDATE short_scans SET received_blocks = ?
            WHERE shift_id = ? AND scan_id = ?
        ''', (block_order, self.shift_id, delivery.scan_id))
        self._maybe_close(delivery.scan_id)

    def _maybe_close(self, scan_id: str) -> None:
        status = self.scans.scan_status(self.shift_id, scan_id)
        if status is None or status.state != 'in_progress':
            return
        row = self._short_row(scan_id)
        if row['end_recording'] is None:
            return
        expected = row['expected_blocks']
        received = row['received_blocks']
        if received < expected:
            # The device has ended but its originals are still in transit.
            return
        receipts = self.ledger.read_receipts(self.shift_id, scan_id)
        staged = self.materials.staged_blocks(self.shift_id, scan_id)
        decision = evaluate_closeout(status.profile, receipts, staged)
        if decision.complete:
            self.materials.seal_scan(self.shift_id, scan_id, len(status.profile.slots))
        else:
            self.materials.discard_staging(self.shift_id, scan_id)
            self.ledger.discard_receipts(self.shift_id, scan_id)
        end_sequence = self._next_event_seq()
        self.scans.save_closeout(
            self.shift_id, scan_id, end_sequence, decision.spread, decision.reason, row['end_order'],
        )
