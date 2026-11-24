from device.events import EventPosition, EventRejected
from device.recording import read_recording
from tests.support import WorkdayCase


class CaptureTests(WorkdayCase):
    def test_complete_scan_in_specification_order(self):
        day = self.station(slots=('L', 'R'))
        self.assertIsNone(day.scan_status('S1'))
        self.send(day, 'start', 'S1')
        self.assertEqual(day.scan_status('S1').state, 'in_progress')
        self.send(day, 'write', 'S1', slot='L', read=4)
        self.send(day, 'write', 'S1', slot='R', read=9)
        self.send(day, 'end', 'S1')
        status = day.scan_status('S1')
        self.assertEqual((status.state, status.open_sequence, status.end_sequence, status.spread), ('complete', 1, 4, 5))
        self.assertEqual(status.profile.slots, ('L', 'R'))
        reference = day.material_reader.reference_for_scan('J1', 'S1')
        self.assertEqual([block.value for block in day.material_reader.read_scan_material(reference)], [4, 9])

    def test_reject_missing_slot_without_mutation(self):
        notices = []
        day = self.station(slots=('L', 'R'), observe_overwrite=notices.append)
        self.send(day, 'start', 'S1')
        self.send(day, 'write', 'S1', slot='L', read=4)
        receipts = day.ledger.read_receipts('J1', 'S1')
        staged = day.materials.staged_blocks('J1', 'S1')
        prior = day.source.slots.read_slot('L')
        where = EventPosition('defective-recording', 47)
        with self.assertRaises(EventRejected) as rejected:
            day.record_event({'kind': 'write', 'scan': 'S1', 'seq': 3, 'event_id': 'E3', 'read': 99}, where)
        self.assertEqual(rejected.exception.position, where)
        self.assertIn('slot', rejected.exception.reason)
        self.assertEqual(day.source.slots.read_slot('L'), prior)
        self.assertIsNone(day.source.slots.read_slot('R'))
        self.assertEqual(day.ledger.read_receipts('J1', 'S1'), receipts)
        self.assertEqual(day.materials.staged_blocks('J1', 'S1'), staged)
        self.assertEqual(day.ledger.last_sequence('J1'), 2)
        self.assertEqual(notices, [])
        self.send(day, 'write', 'S1', slot='R', read=9)
        self.send(day, 'end', 'S1')
        self.assertEqual(day.scan_status('S1').spread, 5)

    def test_overwrite_notice_expires_and_source_closes(self):
        observations = []

        def observe(notice):
            old = notice.read_old_block()
            observations.append((notice, old, day.source.slots.read_slot(old.slot)))

        day = self.station(slots=('L', 'R'), observe_overwrite=observe)
        self.complete(day, 'S1', 7, 11)
        self.send(day, 'start', 'S2')
        self.send(day, 'write', 'S2', slot='L', read=19)
        self.assertEqual(len(observations), 1)
        notice, old, while_notified = observations[0]
        self.assertEqual(old, while_notified)
        self.assertEqual((old.event.scan_id, old.event.event_id, old.event.raw_read), ('S1', 'E2', 7))
        self.assertEqual(notice.replacement.event.raw_read, 19)
        self.assertEqual(day.source.slots.read_slot('L').event.raw_read, 19)
        self.assertEqual(day.source.slots.read_slot('R').event.raw_read, 11)
        with self.assertRaisesRegex(RuntimeError, 'window has ended'):
            notice.read_old_block()
        day.close_source()
        with self.assertRaisesRegex(RuntimeError, 'offline'):
            day.source.slots.read_slot('L')
        with self.assertRaisesRegex(EventRejected, 'offline'):
            self.send(day, 'write', 'S2', slot='R', read=22)

    def test_recording_has_physical_lines_and_reports_malformed_json(self):
        recording = self.directory / 'rejected.jsonl'
        recording.write_text(
            '{"kind":"start","scan":"S1","seq":1,"event_id":"E1"}\n'
            '{"kind":"write","kind":"end"}\n', encoding='utf-8',
        )
        day = self.station(slots=('L', 'R'))
        stream = read_recording(recording)
        first = next(stream)
        self.assertEqual(first.position, EventPosition('rejected.jsonl', 1))
        day.record_event(first.payload, first.position)
        with self.assertRaises(EventRejected) as rejected:
            next(stream)
        self.assertEqual(rejected.exception.position, EventPosition('rejected.jsonl', 2))
        self.assertIn('duplicate field', rejected.exception.reason)
        self.assertEqual(day.scan_status('S1').state, 'in_progress')
        self.assertEqual(day.ledger.read_receipts('J1', 'S1'), ())

    def test_incompatible_scan_events_leave_order_untouched(self):
        notices = []
        day = self.station(slots=('L', 'R'), observe_overwrite=notices.append)
        self.complete(day, 'S1', 4, 9)
        with self.assertRaisesRegex(EventRejected, 'already registered'):
            self.send(day, 'start', 'S1')
        with self.assertRaisesRegex(EventRejected, 'not in progress'):
            self.send(day, 'end', 'S1')
        with self.assertRaisesRegex(EventRejected, 'not in progress'):
            self.send(day, 'write', 'S1', slot='L', read=100)
        self.assertEqual(day.ledger.last_sequence('J1'), 4)
        self.assertEqual(day.source.slots.read_slot('L').event.raw_read, 4)
        self.assertEqual(notices, [])
        self.send(day, 'start', 'S2')
        with self.assertRaisesRegex(EventRejected, 'already in progress'):
            self.send(day, 'start', 'S3')
        self.assertIsNone(day.scan_status('S3'))
        self.assertEqual(day.ledger.last_sequence('J1'), 5)
