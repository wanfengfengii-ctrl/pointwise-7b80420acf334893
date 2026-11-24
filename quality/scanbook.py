from dataclasses import dataclass
import sqlite3

from quality.profile import ScanProfile, decode_profile, encode_profile
from quality.spread import integer_from_text, integer_text


class ScanStateError(ValueError):
    pass


@dataclass(frozen=True)
class ScanStatus:
    shift_id: str
    scan_id: str
    open_sequence: int
    profile: ScanProfile
    state: str
    end_sequence: int | None
    spread: int | None
    reason: str | None
    mode: str = 'device'


def _status(row: sqlite3.Row) -> ScanStatus:
    spread = row['spread_text']
    return ScanStatus(
        row['shift_id'], row['scan_id'], row['open_seq'], decode_profile(row['profile']),
        row['state'], row['end_seq'], integer_from_text(spread) if spread is not None else None,
        row['reason'], row['mode'],
    )


class ScanBook:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS scans (
                shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
                scan_id TEXT NOT NULL,
                open_seq INTEGER NOT NULL CHECK (open_seq > 0),
                profile TEXT NOT NULL,
                state TEXT NOT NULL,
                end_seq INTEGER CHECK (end_seq > open_seq),
                spread_text TEXT,
                reason TEXT,
                mode TEXT NOT NULL DEFAULT 'device',
                end_order INTEGER,
                PRIMARY KEY (shift_id, scan_id),
                UNIQUE (shift_id, open_seq),
                CHECK (
                    (state = 'in_progress' AND end_seq IS NULL AND spread_text IS NULL AND reason IS NULL)
                    OR (state = 'complete' AND end_seq IS NOT NULL AND spread_text IS NOT NULL AND reason IS NULL)
                    OR (state = 'void' AND end_seq IS NOT NULL AND spread_text IS NULL AND reason IS NOT NULL)
                ),
                CHECK (mode IN ('device', 'short'))
            )
        ''')
        columns = {row['name'] for row in self.connection.execute('PRAGMA table_info(scans)')}
        if 'mode' not in columns:
            self.connection.execute("ALTER TABLE scans ADD COLUMN mode TEXT NOT NULL DEFAULT 'device'")
        if 'end_order' not in columns:
            self.connection.execute('ALTER TABLE scans ADD COLUMN end_order INTEGER')
        self.connection.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_device_scan_per_shift
            ON scans(shift_id) WHERE state = 'in_progress' AND mode = 'device'
        ''')
        self.connection.execute('''
            CREATE INDEX IF NOT EXISTS latest_complete_scan
            ON scans(shift_id, end_seq DESC) WHERE state = 'complete'
        ''')
        self.connection.execute('''
            CREATE INDEX IF NOT EXISTS latest_complete_short_scan
            ON scans(shift_id, end_order DESC) WHERE state = 'complete' AND mode = 'short'
        ''')
        self.connection.execute('''
            CREATE INDEX IF NOT EXISTS terminal_short_scans
            ON scans(shift_id, open_seq) WHERE mode = 'short'
        ''')

    def open_scan(self, shift_id: str, scan_id: str, sequence: int, profile: ScanProfile) -> None:
        if self.scan_status(shift_id, scan_id) is not None:
            raise ScanStateError(f'scan {scan_id!r} is already registered')
        active = self.connection.execute('''
            SELECT scan_id FROM scans
            WHERE shift_id = ? AND state = 'in_progress' AND mode = 'device'
        ''', (shift_id,)).fetchone()
        if active is not None:
            raise ScanStateError(f'scan {active[0]!r} is already in progress')
        self.connection.execute('''
            INSERT INTO scans (shift_id, scan_id, open_seq, profile, state, mode)
            VALUES (?, ?, ?, ?, 'in_progress', 'device')
        ''', (shift_id, scan_id, sequence, encode_profile(profile)))

    def open_short_scan(self, shift_id: str, scan_id: str, sequence: int, profile: ScanProfile) -> None:
        if self.scan_status(shift_id, scan_id) is not None:
            raise ScanStateError(f'scan {scan_id!r} is already registered')
        self.connection.execute('''
            INSERT INTO scans (shift_id, scan_id, open_seq, profile, state, mode)
            VALUES (?, ?, ?, ?, 'in_progress', 'short')
        ''', (shift_id, scan_id, sequence, encode_profile(profile)))

    def save_closeout(
        self,
        shift_id: str,
        scan_id: str,
        sequence: int,
        spread: int | None,
        reason: str | None,
        end_order: int | None = None,
    ) -> ScanStatus:
        state = 'complete' if reason is None else 'void'
        spread_text = integer_text(spread) if spread is not None else None
        if end_order is None:
            cursor = self.connection.execute('''
                UPDATE scans SET state = ?, end_seq = ?, spread_text = ?, reason = ?
                WHERE shift_id = ? AND scan_id = ? AND state = 'in_progress'
                RETURNING *
            ''', (state, sequence, spread_text, reason, shift_id, scan_id))
        else:
            cursor = self.connection.execute('''
                UPDATE scans
                SET state = ?, end_seq = ?, spread_text = ?, reason = ?, end_order = ?
                WHERE shift_id = ? AND scan_id = ? AND state = 'in_progress'
                RETURNING *
            ''', (state, sequence, spread_text, reason, end_order, shift_id, scan_id))
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            raise ScanStateError(f'scan {scan_id!r} is not in progress')
        return _status(row)

    def scan_status(self, shift_id: str, scan_id: str) -> ScanStatus | None:
        row = self.connection.execute('SELECT * FROM scans WHERE shift_id = ? AND scan_id = ?', (shift_id, scan_id)).fetchone()
        return _status(row) if row is not None else None

    def latest_complete(self, shift_id: str) -> ScanStatus | None:
        row = self.connection.execute('''
            SELECT * FROM scans
            WHERE shift_id = ? AND state = 'complete' AND mode = 'device'
            ORDER BY end_seq DESC LIMIT 1
        ''', (shift_id,)).fetchone()
        return _status(row) if row is not None else None

    def latest_complete_short(self, shift_id: str) -> ScanStatus | None:
        row = self.connection.execute('''
            SELECT * FROM scans
            WHERE shift_id = ? AND state = 'complete' AND mode = 'short'
            ORDER BY end_order DESC LIMIT 1
        ''', (shift_id,)).fetchone()
        return _status(row) if row is not None else None

    def list_terminal_scans(self, shift_id: str) -> tuple[ScanStatus, ...]:
        rows = self.connection.execute('''
            SELECT * FROM scans WHERE shift_id = ? AND state IN ('complete', 'void') ORDER BY open_seq
        ''', (shift_id,))
        return tuple(_status(row) for row in rows)
