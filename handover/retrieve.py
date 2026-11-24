from archive.results import ResultSnapshot, ResultStore
from handover.reconcile import verify_result_snapshot
from workdb import transaction


class HistoricalReader:
    def __init__(self, results: ResultStore):
        self.results = results

    def read_result(self, result_id: int) -> ResultSnapshot | None:
        if type(result_id) is not int or not 1 <= result_id <= (1 << 63) - 1:
            return None
        with transaction(self.results.connection):
            snapshot = self.results.read_result_snapshot(result_id)
            return verify_result_snapshot(snapshot) if snapshot is not None else None
