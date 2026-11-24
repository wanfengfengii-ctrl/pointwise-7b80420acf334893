import sqlite3

from capture.retention import MaterialUnavailable
from tests.support import WorkdayCase


class ArchiveTests(WorkdayCase):
    def test_pending_first_scan(self):
        day = self.station(slots=('L', 'R'))
        self.send(day, 'start', 'S1')
        self.send(day, 'write', 'S1', slot='L', read=7)
        self.assertIsNone(day.request_scan('Q1'))
        self.assertIsNone(day.requests.get_request('Q1'))
        self.assertIsNone(day.fulfill_request('Q1'))
        self.assertEqual(day.results.list_result_headers('J1'), ())
        self.assertEqual(day.shift_register('J1').rows, ())
        self.send(day, 'write', 'S1', slot='R', read=11)
        self.send(day, 'end', 'S1')
        self.assertEqual(day.request_scan('Q1').scan_id, 'S1')

    def test_late_first_request_and_offline_fulfillment(self):
        day = self.station(slots=('L', 'R'))
        self.complete(day, 'S1', 7, 11)
        self.send(day, 'start', 'S2')
        self.send(day, 'write', 'S2', slot='L', read=19)
        self.assertEqual(day.source.slots.read_slot('L').event.raw_read, 19)
        self.assertEqual(day.source.slots.read_slot('R').event.raw_read, 11)
        self.assertIsNone(day.requests.get_request('Q1'))
        first = day.request_scan('Q1')
        self.assertEqual(first.scan_id, 'S1')
        self.assertEqual(first.binding.material.scan_id, 'S1')
        originals = day.material_reader.read_scan_material(first.binding.material)
        self.assertEqual([(block.value, block.origin.event_id) for block in originals], [(7, 'E2'), (11, 'E3')])

        self.send(day, 'write', 'S2', slot='R', read=22, bad='tail damaged')
        self.send(day, 'end', 'S2')
        self.assertEqual(day.scan_status('S2').state, 'void')
        self.assertEqual(day.request_scan('after-void').scan_id, 'S1')
        self.complete(day, 'S3', 6, 6)
        latest = day.request_scan('Q2')
        self.assertEqual(latest.scan_id, 'S3')
        self.assertEqual(day.request_scan('Q1'), first)
        day.close_source()
        day.close()

        offline = self.station(online=False)
        self.assertEqual(offline.requests.get_request('Q1'), first)
        self.assertEqual(offline.requests.get_request('Q2'), latest)
        old_result = offline.read_result(offline.fulfill_request('Q1'))
        new_result = offline.read_result(offline.fulfill_request('Q2'))
        self.assertEqual((old_result.header.scan_id, old_result.header.spread), ('S1', 4))
        self.assertEqual(self.original_rows(old_result), [
            ('L', 7, 'S1', 'E2', 2, 'bench', 2), ('R', 11, 'S1', 'E3', 3, 'bench', 3),
        ])
        self.assertEqual((new_result.header.scan_id, new_result.header.spread), ('S3', 0))
        self.assertEqual(self.original_rows(new_result), [
            ('L', 6, 'S3', 'E10', 10, 'bench', 10), ('R', 6, 'S3', 'E11', 11, 'bench', 11),
        ])

    def test_commit_failure_is_invisible_and_retryable(self):
        day = self.station(slots=('L', 'R'))
        self.complete(day, 'S1', 7, 11)
        self.send(day, 'start', 'S2')
        self.send(day, 'write', 'S2', slot='L', read=19)
        original = day.request_scan('Q1')
        day.connection.executescript('''
            CREATE TABLE commit_parent (parent_id INTEGER PRIMARY KEY);
            CREATE TABLE commit_child (
                parent_id INTEGER REFERENCES commit_parent(parent_id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_q1_at_commit AFTER UPDATE OF result_id ON archive_requests
            WHEN NEW.request_id = 'Q1'
            BEGIN INSERT INTO commit_child VALUES (1); END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
            day.fulfill_request('Q1')
        self.assertFalse(day.connection.in_transaction)
        self.assertEqual(day.requests.get_request('Q1'), original)
        self.send(day, 'write', 'S2', slot='R', read=22, bad='tail damaged')
        self.send(day, 'end', 'S2')
        self.complete(day, 'S3', 6, 6)
        day.close()

        offline = self.station(online=False)
        self.assertEqual(offline.requests.get_request('Q1'), original)
        self.assertIsNone(offline.read_result(1))
        self.assertEqual(offline.results.list_result_headers('J1'), ())
        self.assertEqual([row.result_ids for row in offline.shift_register('J1').rows], [(), (), ()])
        self.assertEqual(offline.connection.execute('SELECT COUNT(*) FROM result_blocks').fetchone()[0], 0)
        self.assertEqual(offline.connection.execute('SELECT COUNT(*) FROM commit_child').fetchone()[0], 0)
        offline.connection.execute('DROP TRIGGER fail_q1_at_commit')
        result_id = offline.fulfill_request('Q1')
        self.assertEqual(offline.fulfill_request('Q1'), result_id)
        self.assertEqual(offline.requests.get_request('Q1').result_id, result_id)
        self.assertEqual([header.result_id for header in offline.results.list_result_headers('J1')], [result_id])
        self.assertEqual([row.result_ids for row in offline.shift_register('J1').rows], [(result_id,), (), ()])
        self.assertEqual(self.original_rows(offline.read_result(result_id)), [
            ('L', 7, 'S1', 'E2', 2, 'bench', 2), ('R', 11, 'S1', 'E3', 3, 'bench', 3),
        ])

    def test_missing_original_or_spread_disagreement_prevents_publication(self):
        day = self.station(slots=('L', 'R'))
        self.complete(day, 'S1', 7, 11)
        request = day.request_scan('Q1')
        day.connection.execute('DELETE FROM material_blocks WHERE shift_id = ? AND scan_id = ? AND event_seq = ?', ('J1', 'S1', 2))
        with self.assertRaisesRegex(MaterialUnavailable, 'missing'):
            day.fulfill_request('Q1')
        day.connection.execute('INSERT INTO material_blocks VALUES (?, ?, ?, ?)', ('J1', 'S1', 2, '1'))
        with self.assertRaisesRegex(MaterialUnavailable, 'spread'):
            day.fulfill_request('Q1')
        self.assertEqual(day.results.list_result_headers('J1'), ())
        self.assertEqual(day.requests.get_request('Q1'), request)
        self.assertEqual(day.shift_register('J1').rows[0].result_ids, ())

    def test_request_number_cannot_be_rebound_to_another_shift(self):
        first = self.station(slots=('L', 'R'))
        self.complete(first, 'S1', 7, 11)
        old_request = first.request_scan('Q1')
        first.close()
        next_shift = self.station(shift_id='J2', slots=('L', 'R'))
        self.complete(next_shift, 'S1', 21, 37)
        with self.assertRaisesRegex(ValueError, 'another shift'):
            next_shift.request_scan('Q1')
        self.assertEqual(next_shift.requests.get_request('Q1'), old_request)
        self.assertEqual(next_shift.request_scan('Q2').shift_id, 'J2')
