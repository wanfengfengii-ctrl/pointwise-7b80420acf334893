import sqlite3

from capture.retention import MaterialUnavailable
from quality.spread import integer_spread
from tests.support import WorkdayCase


class QualityTests(WorkdayCase):
    def test_negative_spread_and_integer_precision(self):
        day = self.station(slots=('L', 'R'))
        self.complete(day, 'negative', -9, -4)
        self.assertEqual(day.scan_status('negative').spread, 5)
        large = 10 ** 5000
        self.complete(day, 'wide', -large, large + 3)
        self.assertEqual(day.scan_status('wide').spread, 2 * large + 3)
        day.request_scan('wide')
        result_id = day.fulfill_request('wide')
        day.close()
        offline = self.station(online=False)
        stored = offline.read_result(result_id)
        self.assertEqual(tuple(block.value for block in stored.blocks), (-large, large + 3))
        self.assertEqual(stored.header.spread, 2 * large + 3)
        with self.assertRaisesRegex(ValueError, 'empty'):
            integer_spread([])
        with self.assertRaisesRegex(ValueError, 'integer'):
            integer_spread([1, True])

    def test_distinct_origins_for_equal_readings(self):
        day = self.station(slots=('L', 'R'))
        self.send(day, 'start', 'same')
        self.send(day, 'write', 'same', slot='R', read=6)
        self.send(day, 'write', 'same', slot='L', read=6)
        self.send(day, 'end', 'same')
        self.assertEqual((day.scan_status('same').state, day.scan_status('same').spread), ('complete', 0))
        day.request_scan('Q1')
        snapshot = day.read_result(day.fulfill_request('Q1'))
        self.assertEqual(self.original_rows(snapshot), [
            ('L', 6, 'same', 'E3', 3, 'bench', 3), ('R', 6, 'same', 'E2', 2, 'bench', 2),
        ])

    def test_bad_tail_keeps_latest(self):
        day = self.station(slots=('L', 'R'))
        self.complete(day, 'S1', 7, 11)
        self.send(day, 'start', 'S2')
        self.send(day, 'write', 'S2', slot='L', read=19)
        self.send(day, 'write', 'S2', slot='R', read=22, bad='tail saturated')
        receipts = day.ledger.read_receipts('J1', 'S2')
        self.assertEqual([receipt.origin.slot for receipt in receipts], ['L', 'R'])
        self.assertEqual(receipts[1].reason, 'device bad block: tail saturated')
        self.send(day, 'end', 'S2')
        status = day.scan_status('S2')
        self.assertEqual((status.state, status.spread), ('void', None))
        self.assertIn('R@7: device bad block: tail saturated', status.reason)
        self.assertEqual(day.scans.latest_complete('J1').scan_id, 'S1')
        self.assertEqual(day.request_scan('after-bad').scan_id, 'S1')
        self.assertEqual(day.ledger.read_receipts('J1', 'S2'), ())
        self.assertEqual(day.materials.staged_blocks('J1', 'S2'), ())
        with self.assertRaises(MaterialUnavailable):
            day.material_reader.reference_for_scan('J1', 'S2')

    def test_empty_missing_duplicate_and_invalid_read_are_void(self):
        day = self.station(slots=('L', 'R'))
        episodes = (
            ('empty', [], 'missing slots: L, R'),
            ('missing', [('L', 1)], 'missing slots: R'),
            ('duplicate', [('L', 3), ('L', 4), ('R', 5)], 'duplicate slots: L'),
            ('bool', [('L', True), ('R', 4)], 'raw read must be an integer'),
            ('outside', [('L', 1), ('R', 2), ('X', 3)], 'outside the scan profile'),
        )
        for scan, reads, expected in episodes:
            with self.subTest(scan=scan):
                self.send(day, 'start', scan)
                for slot, value in reads:
                    self.send(day, 'write', scan, slot=slot, read=value)
                self.send(day, 'end', scan)
                status = day.scan_status(scan)
                self.assertEqual((status.state, status.spread), ('void', None))
                self.assertIn(expected, status.reason)
                self.assertIsNone(day.request_scan('pending'))

    def test_closeout_storage_failure_leaves_scan_in_progress(self):
        day = self.station(slots=('L', 'R'))
        self.send(day, 'start', 'S1')
        self.send(day, 'write', 'S1', slot='L', read=7)
        self.send(day, 'write', 'S1', slot='R', read=11)
        day.connection.execute('''
            CREATE TRIGGER block_closeout BEFORE UPDATE OF state ON scans
            WHEN NEW.state = 'complete'
            BEGIN SELECT RAISE(ABORT, 'closeout blocked'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'closeout blocked'):
            self.send(day, 'end', 'S1')
        self.assertEqual(day.scan_status('S1').state, 'in_progress')
        self.assertIsNone(day.materials.material_id('J1', 'S1'))
        self.assertEqual(day.ledger.last_sequence('J1'), 3)
        self.assertEqual(len(day.materials.staged_blocks('J1', 'S1')), 2)
        self.assertIsNone(day.request_scan('retry'))
        day.connection.execute('DROP TRIGGER block_closeout')
        self.send(day, 'end', 'S1')
        self.assertEqual(day.request_scan('retry').scan_id, 'S1')
