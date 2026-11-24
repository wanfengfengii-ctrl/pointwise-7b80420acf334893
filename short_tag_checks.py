import argparse
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from device.events import EventPosition
from device.short_port import ShortPort
from handover.workday import open_workday


SAMPLE = Path(__file__).resolve().parent / 'examples' / 'short_tags.json'


class PortTests(unittest.TestCase):
    def test_emit_and_reuse_keep_two_distinct_raw_records(self):
        port = ShortPort()
        during_emit = []
        port.observe(during_emit.append)
        first, second = EventPosition('tape', 81), EventPosition('tape', 19)
        port.begin(0)
        port.emit('L', 7, first)
        self.assertEqual([record.position for record in during_emit], [first])
        self.assertEqual(port.end(), 1)
        port.begin(0)
        port.emit('L', 7, second)
        self.assertEqual(port.read(second), during_emit[1])
        self.assertEqual(port.read(first), during_emit[0])
        port.release(first)
        self.assertIsNone(port.read(first))
        self.assertEqual(port.end(), 1)
        self.assertIsNotNone(port.read(second))

    def test_cut_failure_leaves_records_with_the_controller(self):
        port = ShortPort()
        first, second = EventPosition('tape', 81), EventPosition('tape', 19)
        port.begin(0)
        port.emit('L', 7, first)
        port.emit('R', 11, second)
        moved = []
        with self.assertRaises(RuntimeError):
            port.cut(0, moved.extend)
        self.assertEqual(port.end(), 2)
        port.fail_next_cut = True
        with self.assertRaises(OSError):
            port.cut(0, moved.extend)
        self.assertEqual(moved, [])
        self.assertEqual(port.read(first).value, 7)

        def reject(records):
            self.assertEqual({record.position for record in records}, {first, second})
            raise OSError('receiver refused the cut')

        with self.assertRaises(OSError):
            port.cut(0, reject)
        self.assertEqual(port.read(second).value, 11)
        port.cut(0, moved.extend)
        self.assertEqual({record.position for record in moved}, {first, second})
        self.assertIsNone(port.read(first))
        self.assertIsNone(port.read(second))


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def on_failed_commit(day, deliver):
    attempts = []

    def authorize(action, argument, _other, _database, _trigger):
        if action == sqlite3.SQLITE_TRANSACTION and argument == 'COMMIT':
            attempts.append(argument)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    day.connection.set_authorizer(authorize)
    try:
        try:
            deliver()
        except sqlite3.DatabaseError:
            check(bool(attempts), 'delivery raised before attempting to commit')
        else:
            raise AssertionError('the denied commit returned success')
    finally:
        day.connection.set_authorizer(None)
    check(not day.connection.in_transaction, 'failed receipt still holds a transaction')


def result(day, request):
    result_id = day.fulfill_request(request)
    snapshot = day.read_result(result_id)
    check(snapshot is not None, 'offline fulfillment did not store originals')
    return {
        'scan': snapshot.header.scan_id,
        'values': [block.value for block in snapshot.blocks],
        'origins': [[block.origin.position.recording, block.origin.position.line]
                    for block in snapshot.blocks],
        'spread': snapshot.header.spread,
    }


def run_capture(database, *, fail=False, newer_first=False):
    from device.source import ShortSource

    sample = json.loads(SAMPLE.read_text(encoding='utf-8'))
    port = ShortPort()
    source = ShortSource(port)

    def position(line):
        return EventPosition(sample['recording'], line)

    def deliver(day, line):
        source.deliver(position(line), day.accept_short)

    with open_workday(database, 'short-demo', ('L', 'R')):
        pass
    for scan in sample['scans']:
        source.start(scan['scan'], scan['tag'], position(scan['start']))
        for slot, value, line in scan['blocks']:
            port.emit(slot, value, position(line))
        source.end(position(scan['end']))

    with open_workday(database, 'short-demo') as day:
        for scan in sample['scans']:
            deliver(day, scan['start'])
            deliver(day, scan['end'])
        deliver(day, 14)
        deliver(day, 41)
        check(day.request_scan('before') is None, 'an ended scan with delayed originals is eligible')
        if fail:
            on_failed_commit(day, lambda: deliver(day, 93))
            check(day.scan_status('X').state == 'in_progress', 'a failed receipt closed X')
            check(day.request_scan('after-failure') is None, 'a failed receipt became eligible')

    with open_workday(database, 'short-demo') as day:
        if newer_first:
            deliver(day, 32)
            check(day.request_scan('QY').scan_id == 'Y', 'Y waited for the delayed X block')
        deliver(day, 93)
        deliver(day, 93)
        check(day.scan_status('X').spread == 4, 'X did not close with its actual originals')
        if newer_first:
            check(day.request_scan('Q-late').scan_id == 'Y', 'a late close changed source recency')
        else:
            check(day.request_scan('QX').scan_id == 'X', 'the older scan could not be selected')
            deliver(day, 32)
            check(day.request_scan('QY').scan_id == 'Y', 'the newer scan could not be selected')

    port.close()
    with open_workday(database, 'short-demo', online=False) as day:
        check([row.status.scan_id for row in day.shift_register('short-demo').rows] == ['X', 'Y'],
              'handover lost source scan order')
        requests = ('QY',) if newer_first else ('QX', 'QY')
        actual = [result(day, request) for request in requests]
    expected = [
        {'scan': 'X', 'values': [7, 11], 'origins': [['line-reel', 41], ['line-reel', 93]], 'spread': 4},
        {'scan': 'Y', 'values': [7, 19], 'origins': [['line-reel', 14], ['line-reel', 32]], 'spread': 12},
    ]
    check(actual == (expected[1:] if newer_first else expected), f'wrong original material: {actual!r}')
    return actual


def main():
    parser = argparse.ArgumentParser(description='Check short-tag collection with the controller fixture')
    parser.add_argument('command', choices=('port', 'verify', 'demo'))
    args = parser.parse_args()
    if args.command == 'port':
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(PortTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.command == 'verify':
        from check_project import main as check_original

        if check_original():
            return 1
    with TemporaryDirectory(prefix='short-tag-') as directory:
        actual = run_capture(Path(directory) / 'station.sqlite', fail=args.command == 'verify')
        if args.command == 'verify':
            run_capture(Path(directory) / 'newer-first.sqlite', newer_first=True)
        print(json.dumps(actual, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
