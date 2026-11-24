import sqlite3

from archive.assemble import assemble_result
from archive.requests import RequestBook
from archive.results import ResultStore
from capture.retention import MaterialUnavailable
from quality.scanbook import ScanBook
from workdb import transaction


class Publisher:
    def __init__(self, connection: sqlite3.Connection, requests: RequestBook, scans: ScanBook, results: ResultStore):
        self.connection = connection
        self.requests = requests
        self.scans = scans
        self.results = results

    def fulfill_request(self, request_id: str) -> int | None:
        with transaction(self.connection, write=True):
            request = self.requests.get_request(request_id)
            if request is None:
                return None
            if request.result_id is not None:
                return request.result_id
            status = self.scans.scan_status(request.shift_id, request.scan_id)
            if status is None:
                raise MaterialUnavailable('bound scan is missing')
            draft = assemble_result(request_id, request.binding, status, self.requests.reader)
            result_id = self.results.insert_result(draft)
            self.requests.mark_fulfilled(request_id, result_id)
            return result_id
