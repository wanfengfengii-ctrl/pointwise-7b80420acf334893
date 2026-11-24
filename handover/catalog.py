from dataclasses import dataclass

from archive.results import ResultStore
from handover.reconcile import RegisterRow, link_published_results
from handover.shifts import ShiftBook
from quality.scanbook import ScanBook
from workdb import transaction


@dataclass(frozen=True)
class RegisterSnapshot:
    shift_id: str
    rows: tuple[RegisterRow, ...]


class ShiftRegister:
    def __init__(self, shifts: ShiftBook, scans: ScanBook, results: ResultStore):
        self.shifts = shifts
        self.scans = scans
        self.results = results

    def shift_register(self, shift_id: str) -> RegisterSnapshot | None:
        with transaction(self.shifts.connection):
            if self.shifts.find_shift(shift_id) is None:
                return None
            scans = self.scans.list_terminal_scans(shift_id)
            results = self.results.list_result_headers(shift_id)
            return RegisterSnapshot(shift_id, link_published_results(scans, results))
