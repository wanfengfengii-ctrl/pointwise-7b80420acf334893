import sqlite3

from capture.ledger import origin_from_row
from capture.origin import CapturedBlock
from quality.spread import integer_from_text, integer_text


class MaterialUnavailable(RuntimeError):
    pass


class MaterialStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS material_blocks (
                shift_id TEXT NOT NULL,
                scan_id TEXT NOT NULL,
                event_seq INTEGER NOT NULL,
                read_text TEXT NOT NULL,
                PRIMARY KEY (shift_id, scan_id, event_seq),
                FOREIGN KEY (shift_id, scan_id, event_seq)
                    REFERENCES capture_receipts(shift_id, scan_id, event_seq) ON DELETE CASCADE
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS sealed_material (
                material_id INTEGER PRIMARY KEY,
                shift_id TEXT NOT NULL,
                scan_id TEXT NOT NULL,
                block_count INTEGER NOT NULL CHECK (block_count > 0),
                UNIQUE (shift_id, scan_id),
                UNIQUE (material_id, shift_id, scan_id),
                FOREIGN KEY (shift_id, scan_id) REFERENCES scans(shift_id, scan_id)
            )
        ''')

    def material_id(self, shift_id: str, scan_id: str) -> int | None:
        row = self.connection.execute('''
            SELECT material_id FROM sealed_material WHERE shift_id = ? AND scan_id = ?
        ''', (shift_id, scan_id)).fetchone()
        return row['material_id'] if row is not None else None

    def stage_block(self, block: CapturedBlock) -> None:
        origin = block.origin
        if self.material_id(origin.shift_id, origin.scan_id) is not None:
            raise MaterialUnavailable('cannot stage a block for sealed material')
        receipt = self.connection.execute('''
            SELECT * FROM capture_receipts WHERE shift_id = ? AND scan_id = ? AND event_seq = ?
        ''', (origin.shift_id, origin.scan_id, origin.sequence)).fetchone()
        if receipt is None or receipt['reason'] is not None or origin_from_row(receipt) != origin:
            raise MaterialUnavailable('block does not match a qualified receipt')
        self.connection.execute('''
            INSERT INTO material_blocks (shift_id, scan_id, event_seq, read_text) VALUES (?, ?, ?, ?)
        ''', (origin.shift_id, origin.scan_id, origin.sequence, integer_text(block.value)))

    def _read_blocks(self, shift_id: str, scan_id: str) -> tuple[CapturedBlock, ...]:
        rows = self.connection.execute('''
            SELECT r.*, b.read_text
            FROM capture_receipts AS r
            JOIN material_blocks AS b USING (shift_id, scan_id, event_seq)
            WHERE r.shift_id = ? AND r.scan_id = ? AND r.reason IS NULL
            ORDER BY r.event_seq
        ''', (shift_id, scan_id))
        try:
            return tuple(CapturedBlock(origin_from_row(row), integer_from_text(row['read_text'])) for row in rows)
        except ValueError as error:
            raise MaterialUnavailable('invalid stored original') from error

    def staged_blocks(self, shift_id: str, scan_id: str) -> tuple[CapturedBlock, ...]:
        if self.material_id(shift_id, scan_id) is not None:
            return ()
        return self._read_blocks(shift_id, scan_id)

    def _receipt_count(self, shift_id: str, scan_id: str) -> int:
        return self.connection.execute('''
            SELECT COUNT(*) FROM capture_receipts WHERE shift_id = ? AND scan_id = ?
        ''', (shift_id, scan_id)).fetchone()[0]

    def seal_scan(self, shift_id: str, scan_id: str, block_count: int) -> int:
        if block_count <= 0 or self._receipt_count(shift_id, scan_id) != block_count:
            raise MaterialUnavailable('scan originals are incomplete')
        if len(self.staged_blocks(shift_id, scan_id)) != block_count:
            raise MaterialUnavailable('scan originals cannot be sealed')
        cursor = self.connection.execute('''
            INSERT INTO sealed_material (shift_id, scan_id, block_count) VALUES (?, ?, ?)
        ''', (shift_id, scan_id, block_count))
        return cursor.lastrowid

    def discard_staging(self, shift_id: str, scan_id: str) -> None:
        if self.material_id(shift_id, scan_id) is not None:
            raise MaterialUnavailable('cannot discard sealed material')
        self.connection.execute('DELETE FROM material_blocks WHERE shift_id = ? AND scan_id = ?', (shift_id, scan_id))

    def read_sealed(self, material_id: int, shift_id: str, scan_id: str) -> tuple[CapturedBlock, ...]:
        row = self.connection.execute('''
            SELECT block_count FROM sealed_material
            WHERE material_id = ? AND shift_id = ? AND scan_id = ?
        ''', (material_id, shift_id, scan_id)).fetchone()
        if row is None:
            raise MaterialUnavailable('sealed material identity was not found')
        blocks = self._read_blocks(shift_id, scan_id)
        expected_count = row['block_count']
        if len(blocks) != expected_count or self._receipt_count(shift_id, scan_id) != expected_count:
            raise MaterialUnavailable('sealed material is missing one or more qualified originals')
        return blocks
