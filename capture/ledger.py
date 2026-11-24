from dataclasses import dataclass
import sqlite3

from capture.origin import BlockOrigin
from device.events import EventPosition


@dataclass(frozen=True)
class CaptureReceipt:
    origin: BlockOrigin
    reason: str | None


def origin_from_row(row: sqlite3.Row) -> BlockOrigin:
    return BlockOrigin(
        row['shift_id'], row['scan_id'], row['slot'], row['event_id'], row['event_seq'],
        EventPosition(row['recording'], row['recording_line']),
    )


class CaptureLedger:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS capture_sequence (
                shift_id TEXT PRIMARY KEY REFERENCES shifts(shift_id),
                last_seq INTEGER NOT NULL CHECK (last_seq > 0)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS capture_receipts (
                shift_id TEXT NOT NULL,
                scan_id TEXT NOT NULL,
                event_seq INTEGER NOT NULL CHECK (event_seq > 0),
                slot TEXT NOT NULL,
                event_id TEXT NOT NULL,
                recording TEXT NOT NULL,
                recording_line INTEGER NOT NULL CHECK (recording_line > 0),
                reason TEXT,
                PRIMARY KEY (shift_id, scan_id, event_seq),
                FOREIGN KEY (shift_id, scan_id) REFERENCES scans(shift_id, scan_id)
            )
        ''')

    def last_sequence(self, shift_id: str) -> int:
        row = self.connection.execute(
            'SELECT last_seq FROM capture_sequence WHERE shift_id = ?', (shift_id,)
        ).fetchone()
        return row['last_seq'] if row is not None else 0

    def record_sequence(self, shift_id: str, sequence: int) -> None:
        self.connection.execute('''
            INSERT INTO capture_sequence (shift_id, last_seq) VALUES (?, ?)
            ON CONFLICT (shift_id) DO UPDATE SET last_seq = excluded.last_seq
        ''', (shift_id, sequence))

    def record_receipt(self, origin: BlockOrigin, reason: str | None) -> None:
        self.connection.execute('''
            INSERT INTO capture_receipts
              (shift_id, scan_id, event_seq, slot, event_id, recording, recording_line, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            origin.shift_id, origin.scan_id, origin.sequence, origin.slot, origin.event_id,
            origin.position.recording, origin.position.line, reason,
        ))

    def find_receipt(self, origin: BlockOrigin) -> CaptureReceipt | None:
        row = self.connection.execute('''
            SELECT * FROM capture_receipts WHERE shift_id = ? AND scan_id = ? AND event_seq = ?
        ''', (origin.shift_id, origin.scan_id, origin.sequence)).fetchone()
        return CaptureReceipt(origin_from_row(row), row['reason']) if row is not None else None

    def read_receipts(self, shift_id: str, scan_id: str) -> tuple[CaptureReceipt, ...]:
        rows = self.connection.execute('''
            SELECT * FROM capture_receipts WHERE shift_id = ? AND scan_id = ? ORDER BY event_seq
        ''', (shift_id, scan_id))
        return tuple(CaptureReceipt(origin_from_row(row), row['reason']) for row in rows)

    def discard_receipts(self, shift_id: str, scan_id: str) -> None:
        self.connection.execute('DELETE FROM capture_receipts WHERE shift_id = ? AND scan_id = ?', (shift_id, scan_id))
