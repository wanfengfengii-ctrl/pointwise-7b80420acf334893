from dataclasses import dataclass
import sqlite3

from archive.assemble import ResultDraft
from capture.ledger import origin_from_row
from capture.origin import CapturedBlock
from quality.profile import ScanProfile, decode_profile, encode_profile
from quality.spread import integer_from_text, integer_text


class ResultIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResultHeader:
    result_id: int
    request_id: str
    shift_id: str
    scan_id: str
    profile: ScanProfile
    spread: int


@dataclass(frozen=True)
class ResultSnapshot:
    header: ResultHeader
    blocks: tuple[CapturedBlock, ...]


def _header(row: sqlite3.Row) -> ResultHeader:
    try:
        return ResultHeader(
            row['result_id'], row['request_id'], row['shift_id'], row['scan_id'],
            decode_profile(row['profile']), integer_from_text(row['spread_text']),
        )
    except ValueError as error:
        raise ResultIntegrityError('invalid result header') from error


class ResultStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS archive_results (
                result_id INTEGER PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                shift_id TEXT NOT NULL,
                scan_id TEXT NOT NULL,
                profile TEXT NOT NULL,
                spread_text TEXT NOT NULL,
                UNIQUE (result_id, request_id),
                FOREIGN KEY (request_id, shift_id, scan_id)
                    REFERENCES archive_requests(request_id, shift_id, scan_id)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS result_blocks (
                result_id INTEGER NOT NULL REFERENCES archive_results(result_id),
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                shift_id TEXT NOT NULL,
                scan_id TEXT NOT NULL,
                slot TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_seq INTEGER NOT NULL CHECK (event_seq > 0),
                recording TEXT NOT NULL,
                recording_line INTEGER NOT NULL CHECK (recording_line > 0),
                read_text TEXT NOT NULL,
                PRIMARY KEY (result_id, ordinal),
                UNIQUE (result_id, slot)
            )
        ''')
        self.connection.execute('''
            CREATE INDEX IF NOT EXISTS results_by_shift ON archive_results(shift_id, result_id)
        ''')

    def insert_result(self, draft: ResultDraft) -> int:
        cursor = self.connection.execute('''
            INSERT INTO archive_results (request_id, shift_id, scan_id, profile, spread_text)
            VALUES (?, ?, ?, ?, ?)
        ''', (draft.request_id, draft.shift_id, draft.scan_id, encode_profile(draft.profile), integer_text(draft.spread)))
        result_id = cursor.lastrowid
        rows = []
        for ordinal, block in enumerate(draft.blocks):
            origin = block.origin
            rows.append((
                result_id, ordinal, origin.shift_id, origin.scan_id, origin.slot, origin.event_id,
                origin.sequence, origin.position.recording, origin.position.line, integer_text(block.value),
            ))
        self.connection.executemany('''
            INSERT INTO result_blocks
              (result_id, ordinal, shift_id, scan_id, slot, event_id, event_seq, recording, recording_line, read_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)
        return result_id

    def read_result_snapshot(self, result_id: int) -> ResultSnapshot | None:
        row = self.connection.execute('''
            SELECT * FROM archive_results WHERE result_id = ?
        ''', (result_id,)).fetchone()
        if row is None:
            return None
        header = _header(row)
        rows = self.connection.execute('SELECT * FROM result_blocks WHERE result_id = ? ORDER BY ordinal', (result_id,))
        blocks = []
        for expected_ordinal, block in enumerate(rows):
            if expected_ordinal != block['ordinal']:
                raise ResultIntegrityError('noncontiguous result block order')
            try:
                blocks.append(CapturedBlock(origin_from_row(block), integer_from_text(block['read_text'])))
            except ValueError as error:
                raise ResultIntegrityError('invalid result original') from error
        return ResultSnapshot(header, tuple(blocks))

    def list_result_headers(self, shift_id: str) -> tuple[ResultHeader, ...]:
        rows = self.connection.execute('''
            SELECT * FROM archive_results WHERE shift_id = ? ORDER BY result_id
        ''', (shift_id,))
        return tuple(_header(row) for row in rows)
