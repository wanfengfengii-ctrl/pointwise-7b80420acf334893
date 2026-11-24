import argparse
from collections.abc import Callable, Iterable
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from archive.publish import Publisher
from archive.requests import ArchiveRequest, RequestBook
from archive.results import ResultSnapshot, ResultStore
from capture.intake import CaptureIntake
from capture.ledger import CaptureLedger
from capture.recovery import MaterialReader
from capture.retention import MaterialStore
from device.events import DeviceEvent, EventPosition, EventRejected
from device.recording import read_recording
from device.replay import Replay
from device.slots import OverwriteNotice
from device.source import DeviceSource
from handover.catalog import RegisterSnapshot, ShiftRegister
from handover.retrieve import HistoricalReader
from handover.shifts import ShiftBook
from quality.profile import validate_profile
from quality.scanbook import ScanBook, ScanStatus
from workdb import connect_workdb, transaction


class Workday:
    def __init__(self, connection: sqlite3.Connection, shift_id: str):
        self.connection = connection
        self.shift_id = shift_id
        self.shifts = ShiftBook(connection)
        self.scans = ScanBook(connection)
        self.ledger = CaptureLedger(connection)
        self.materials = MaterialStore(connection)
        self.material_reader = MaterialReader(self.materials)
        self.requests = RequestBook(connection, self.scans, self.material_reader)
        self.results = ResultStore(connection)
        self.publisher = Publisher(connection, self.requests, self.scans, self.results)
        self.register = ShiftRegister(self.shifts, self.scans, self.results)
        self.history = HistoricalReader(self.results)
        self.replay: Replay | None = None
        self.source: DeviceSource | None = None
        self._closed = False

    def initialize(self) -> None:
        with transaction(self.connection, write=True):
            self.shifts.initialize()
            self.scans.initialize()
            self.ledger.initialize()
            self.materials.initialize()
            self.requests.initialize()
            self.results.initialize()

    def record_event(self, payload: object, position: EventPosition) -> DeviceEvent:
        if self.replay is None:
            raise EventRejected(position, 'no online device is attached')
        return self.replay.record_event(payload, position)

    def scan_status(self, scan_id: str) -> ScanStatus | None:
        return self.scans.scan_status(self.shift_id, scan_id)

    def request_scan(self, request_id: str) -> ArchiveRequest | None:
        return self.requests.request_scan(self.shift_id, request_id)

    def fulfill_request(self, request_id: str) -> int | None:
        return self.publisher.fulfill_request(request_id)

    def shift_register(self, shift_id: str) -> RegisterSnapshot | None:
        return self.register.shift_register(shift_id)

    def read_result(self, result_id: int) -> ResultSnapshot | None:
        return self.history.read_result(result_id)

    def close_source(self) -> None:
        if self.source is not None:
            self.source.close()

    def close(self) -> None:
        if not self._closed:
            self.close_source()
            self.connection.close()
            self._closed = True

    def __enter__(self) -> 'Workday':
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()


def open_workday(
    path: str | Path,
    shift_id: str,
    slots: Iterable[str] | None = None,
    *,
    online: bool = True,
    observe_overwrite: Callable[[OverwriteNotice], None] | None = None,
) -> Workday:
    profile = validate_profile(slots) if slots is not None else None
    day = Workday(connect_workdb(path), shift_id)
    try:
        day.initialize()
        if online:
            shift = day.shifts.open_shift(shift_id, profile)
            intake = CaptureIntake(day.connection, shift_id, shift.profile, day.scans, day.ledger, day.materials)

            def before_overwrite(notice: OverwriteNotice) -> None:
                intake.before_overwrite(notice)
                if observe_overwrite is not None:
                    observe_overwrite(notice)

            day.source = DeviceSource(intake.accept_event, before_overwrite)
            day.replay = Replay(day.source, day.ledger.last_sequence(shift_id))
        else:
            shift = day.shifts.find_shift(shift_id)
            if shift is None:
                raise ValueError(f'unknown shift {shift_id!r}')
            if profile is not None and profile != shift.profile:
                raise ValueError(f'profile already fixed for shift {shift_id!r}')
        return day
    except BaseException:
        day.close()
        raise


def _sample_result(snapshot: ResultSnapshot | None) -> dict:
    if snapshot is None:
        raise RuntimeError('sample result is missing')
    return {
        'scan': snapshot.header.scan_id,
        'values': [block.value for block in snapshot.blocks],
        'origins': [
            [origin.scan_id, origin.slot, origin.event_id, origin.sequence, origin.position.line]
            for origin in (block.origin for block in snapshot.blocks)
        ],
        'spread': snapshot.header.spread,
    }


def run_workday(recording: str | Path) -> dict:
    """Play the J1 demonstration and return the compared business observations."""
    with TemporaryDirectory(prefix='line-shift-') as directory:
        database = Path(directory) / 'station.sqlite'
        pending_checked = False
        first_binding = None
        next_binding = None
        with open_workday(database, 'J1', ('L', 'R')) as day:
            for item in read_recording(recording):
                event = day.record_event(item.payload, item.position)
                if event.sequence == 2:
                    pending_checked = day.request_scan('Q0') is None
                if event.sequence == 6:
                    first_binding = day.request_scan('Q1')
                if event.sequence == 12:
                    next_binding = day.request_scan('Q2')
            if not pending_checked or first_binding is None or next_binding is None:
                raise RuntimeError('recording did not exercise the sample request cut points')
            next_result = day.fulfill_request('Q2')
            if next_result is None:
                raise RuntimeError('sample new request was not fulfilled')
            day.close_source()

        with open_workday(database, 'J1', online=False) as day:
            first_result = day.fulfill_request('Q1')
            register = day.shift_register('J1')
            if first_result is None or register is None:
                raise RuntimeError('sample handover is incomplete')
            actual = _sample_result(day.read_result(first_result))
            downstream_actual = {
                'bindings': [first_binding.scan_id, next_binding.scan_id],
                'new_result': _sample_result(day.read_result(next_result)),
                'register': [
                    [row.status.scan_id, row.status.state, list(row.result_ids)] for row in register.rows
                ],
            }

    expected = {
        'scan': 'S1', 'values': [7, 11], 'spread': 4,
        'origins': [['S1', 'L', 'E2', 2, 2], ['S1', 'R', 'E3', 3, 3]],
    }
    downstream_expected = {
        'bindings': ['S1', 'S3'],
        'new_result': {
            'scan': 'S3', 'values': [6, 6], 'spread': 0,
            'origins': [['S3', 'L', 'E10', 10, 10], ['S3', 'R', 'E11', 11, 11]],
        },
        'register': [['S1', 'complete', [2]], ['S2', 'void', []], ['S3', 'complete', [1]]],
    }
    if actual != expected or downstream_actual != downstream_expected:
        raise RuntimeError(f'sample readings, provenance or handover disagree: {actual!r}, {downstream_actual!r}')
    return {
        'case_id': 'v6',
        'input': {'recording': Path(recording).name, 'slots': ['L', 'R'], 'request_after_seq': {'Q1': 6, 'Q2': 12}},
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': ['capture/intake.py', 'capture/recovery.py', 'archive/bindings.py', 'archive/publish.py', 'handover/catalog.py'],
        'oracle': 'Read E2, E3, E10 and E11 directly from the recording. Subtract 7 from 11 and 6 from 6. Q2 commits before Q1 while scan rows follow the start events.',
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Play the line station handover demonstration')
    parser.add_argument('command', choices=['demo'])
    parser.add_argument('--recording', type=Path, default=Path('examples/shift.jsonl'))
    arguments = parser.parse_args(argv)
    try:
        evidence = run_workday(arguments.recording)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        parser.error(str(error))
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
