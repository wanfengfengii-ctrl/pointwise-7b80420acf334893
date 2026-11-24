from dataclasses import dataclass
import sqlite3

from archive.bindings import RequestBinding, bind_scan
from capture.recovery import MaterialReader, ScanMaterialRef
from capture.short_capture import is_short_shift
from quality.scanbook import ScanBook
from workdb import transaction


@dataclass(frozen=True)
class ArchiveRequest:
    request_id: str
    binding: RequestBinding
    result_id: int | None

    @property
    def scan_id(self) -> str:
        return self.binding.scan_id

    @property
    def shift_id(self) -> str:
        return self.binding.shift_id


class RequestBook:
    def __init__(self, connection: sqlite3.Connection, scans: ScanBook, reader: MaterialReader):
        self.connection = connection
        self.scans = scans
        self.reader = reader

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS archive_requests (
                request_id TEXT PRIMARY KEY NOT NULL,
                shift_id TEXT NOT NULL,
                scan_id TEXT NOT NULL,
                material_id INTEGER NOT NULL,
                result_id INTEGER,
                UNIQUE (request_id, shift_id, scan_id),
                FOREIGN KEY (material_id, shift_id, scan_id)
                    REFERENCES sealed_material(material_id, shift_id, scan_id),
                FOREIGN KEY (result_id, request_id) REFERENCES archive_results(result_id, request_id)
            )
        ''')

    def request_scan(self, shift_id: str, request_id: str) -> ArchiveRequest | None:
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError('request_id must be nonempty text')
        with transaction(self.connection, write=True):
            prior = self.get_request(request_id)
            if prior is not None:
                if prior.shift_id != shift_id:
                    raise ValueError(f'request {request_id!r} belongs to another shift')
                return prior
            if is_short_shift(self.connection, shift_id):
                status = self.scans.latest_complete_short(shift_id)
            else:
                status = self.scans.latest_complete(shift_id)
            if status is None:
                return None
            binding = bind_scan(status, self.reader)
            self.connection.execute('''
                INSERT INTO archive_requests (request_id, shift_id, scan_id, material_id)
                VALUES (?, ?, ?, ?)
            ''', (request_id, binding.shift_id, binding.scan_id, binding.material.material_id))
            return ArchiveRequest(request_id, binding, None)

    def get_request(self, request_id: str) -> ArchiveRequest | None:
        row = self.connection.execute('SELECT * FROM archive_requests WHERE request_id = ?', (request_id,)).fetchone()
        if row is None:
            return None
        reference = ScanMaterialRef(row['shift_id'], row['scan_id'], row['material_id'])
        binding = RequestBinding(row['shift_id'], row['scan_id'], reference)
        return ArchiveRequest(row['request_id'], binding, row['result_id'])

    def mark_fulfilled(self, request_id: str, result_id: int) -> None:
        cursor = self.connection.execute('''
            UPDATE archive_requests SET result_id = ? WHERE request_id = ? AND result_id IS NULL
        ''', (result_id, request_id))
        if cursor.rowcount != 1:
            raise RuntimeError(f'request {request_id!r} is unavailable for publication')
