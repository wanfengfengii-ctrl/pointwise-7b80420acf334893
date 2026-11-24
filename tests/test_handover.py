from archive.results import ResultIntegrityError
from device.events import EventPosition, EventRejected
from tests.support import WorkdayCase


class HandoverTests(WorkdayCase):
    def test_scan_order_and_offline_result_by_number(self):
        day = self.station(slots=('L', 'R'))
        self.complete(day, 'S9', 7, 11)
        old = day.request_scan('old')
        self.send(day, 'start', 'S2')
        self.send(day, 'write', 'S2', slot='L', read=23)
        self.send(day, 'write', 'S2', slot='R', read=24, bad='tail damaged')
        self.send(day, 'end', 'S2')
        self.complete(day, 'S10', -2, 8)
        day.request_scan('new')
        new_id = day.fulfill_request('new')
        old_id = day.fulfill_request(old.request_id)
        self.assertLess(new_id, old_id)
        day.close_source()
        day.close()

        offline = self.station(online=False)
        register = offline.shift_register('J1')
        self.assertEqual([(row.status.scan_id, row.status.state, row.result_ids) for row in register.rows], [
            ('S9', 'complete', (old_id,)), ('S2', 'void', ()), ('S10', 'complete', (new_id,)),
        ])
        self.assertIn('tail damaged', register.rows[1].status.reason)
        self.assertIsNone(offline.shift_register('other-shift'))
        older = offline.read_result(old_id)
        newer = offline.read_result(new_id)
        self.assertIsNone(offline.read_result(old_id + new_id + 10))
        self.assertEqual(offline.read_result(old_id), older)
        self.assertEqual((older.header.scan_id, older.header.spread), ('S9', 4))
        self.assertEqual((newer.header.scan_id, newer.header.spread), ('S10', 10))
        self.assertEqual(self.original_rows(older), [
            ('L', 7, 'S9', 'E2', 2, 'bench', 2), ('R', 11, 'S9', 'E3', 3, 'bench', 3),
        ])
        self.assertEqual(self.original_rows(newer), [
            ('L', -2, 'S10', 'E10', 10, 'bench', 10), ('R', 8, 'S10', 'E11', 11, 'bench', 11),
        ])

    def test_reinitialize_keeps_data_and_preserves_shift_profile(self):
        day = self.station(slots=('L', 'R'))
        self.complete(day, 'S1', 4, 9)
        first = day.request_scan('Q1')
        result_id = day.fulfill_request('Q1')
        snapshot = day.read_result(result_id)
        saved_request = day.requests.get_request('Q1')
        pending = day.request_scan('Q2')
        receipts = day.ledger.read_receipts('J1', 'S1')
        day.close()

        with self.assertRaisesRegex(ValueError, 'profile already fixed'):
            self.station(slots=('R', 'L'))
        reopened = self.station(slots=('L', 'R'))
        reopened.initialize()
        self.assertEqual(reopened.shifts.find_shift('J1').profile.slots, ('L', 'R'))
        self.assertEqual(reopened.ledger.read_receipts('J1', 'S1'), receipts)
        self.assertEqual(reopened.requests.get_request('Q1'), saved_request)
        self.assertEqual(reopened.requests.get_request('Q2'), pending)
        self.assertEqual(reopened.requests.get_request('Q1').binding, first.binding)
        self.assertEqual(reopened.read_result(result_id), snapshot)
        self.assertEqual(reopened.shift_register('J1').rows[0].result_ids, (result_id,))
        with self.assertRaisesRegex(EventRejected, 'seq must advance'):
            reopened.record_event({'kind': 'start', 'scan': 'stale', 'seq': 4, 'event_id': 'stale'}, EventPosition('bench', 5))
        self.complete(reopened, 'S2', 16, 20)
        self.assertEqual(reopened.scan_status('S2').open_sequence, 5)
        self.assertEqual(reopened.request_scan('Q3').scan_id, 'S2')
        self.assertEqual(reopened.read_result(result_id), snapshot)

    def test_history_rejects_wrong_slot_order(self):
        day = self.station(slots=('L', 'R'))
        self.complete(day, 'S1', 7, 11)
        day.request_scan('Q1')
        result_id = day.fulfill_request('Q1')
        day.connection.execute('UPDATE result_blocks SET ordinal = ordinal + 2 WHERE result_id = ?', (result_id,))
        day.connection.execute('UPDATE result_blocks SET ordinal = 3 - ordinal WHERE result_id = ?', (result_id,))
        day.close()
        offline = self.station(online=False)
        with self.assertRaisesRegex(ResultIntegrityError, 'stored profile'):
            offline.read_result(result_id)
