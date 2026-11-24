from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from device.events import EventPosition
from handover.workday import open_workday


class WorkdayCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='line-station-test-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.database = self.directory / 'station.sqlite'

    def station(self, *, online=True, shift_id='J1', slots=None, observe_overwrite=None):
        day = open_workday(
            self.database, shift_id, slots, online=online, observe_overwrite=observe_overwrite,
        )
        self.addCleanup(day.close)
        return day

    def send(self, day, kind, scan, **fields):
        sequence = day.replay.last_sequence + 1
        position = EventPosition('bench', sequence)
        payload = {'kind': kind, 'scan': scan, 'seq': sequence, 'event_id': f'E{sequence}', **fields}
        return day.record_event(payload, position)

    def complete(self, day, scan, left, right):
        self.send(day, 'start', scan)
        self.send(day, 'write', scan, slot='L', read=left)
        self.send(day, 'write', scan, slot='R', read=right)
        self.send(day, 'end', scan)

    def original_rows(self, snapshot):
        return [
            (block.origin.slot, block.value, block.origin.scan_id, block.origin.event_id,
             block.origin.sequence, block.origin.position.recording, block.origin.position.line)
            for block in snapshot.blocks
        ]
