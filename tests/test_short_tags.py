import sqlite3

from device.events import EventPosition, EventRejected
from device.short_port import ShortPort
from device.source import ShortDelivery, ShortSource
from tests.support import WorkdayCase


def position(line, recording='line-reel'):
    return EventPosition(recording, line)


class ShortSourceTests(WorkdayCase):
    def source(self):
        port = ShortPort()
        self.addCleanup(port.close)
        return ShortSource(port), port

    def test_recycled_tag_keeps_ownership_of_undelivered_blocks(self):
        source, port = self.source()
        source.start('X', 0, position(1))
        port.emit('L', 7, position(41))
        port.emit('R', 11, position(93))
        self.assertEqual(source.end(position(2)), 2)
        # Hardware immediately recycles tag 0 for the next belt.
        source.start('Y', 0, position(3))
        port.emit('L', 7, position(14))
        port.emit('R', 19, position(32))
        self.assertEqual(source.end(position(4)), 2)

        owners = {}
        source.deliver(position(14), lambda d: owners.__setitem__(14, d.scan_id))
        source.deliver(position(41), lambda d: owners.__setitem__(41, d.scan_id))
        source.deliver(position(93), lambda d: owners.__setitem__(93, d.scan_id))
        source.deliver(position(32), lambda d: owners.__setitem__(32, d.scan_id))
        self.assertEqual(owners, {14: 'Y', 41: 'X', 93: 'X', 32: 'Y'})

    def test_unknown_position_and_duplicate_position_are_rejected(self):
        source, port = self.source()
        source.start('X', 0, position(1))
        port.emit('L', 7, position(41))
        source.end(position(2))
        with self.assertRaises(KeyError):
            source.deliver(position(99), lambda _delivery: None)
        with self.assertRaises(ValueError):
            source.start('Z', 0, position(1))
        with self.assertRaises(ValueError):
            source.end(position(2))
        # A position used by an earlier block cannot be emitted again.
        source.start('Y', 0, position(3))
        with self.assertRaises(ValueError):
            port.emit('R', 8, position(41))
        port.emit('R', 8, position(55))
        source.end(position(4))

    def test_start_validates_tag_and_scan_identity(self):
        source, _port = self.source()
        with self.assertRaises(ValueError):
            source.start('X', 256, position(1))
        with self.assertRaises(ValueError):
            source.start(' ', 0, position(2))
        source.start('X', 0, position(3))
        with self.assertRaises(ValueError):
            source.start('X', 0, position(4))


class ShortIntakeTests(WorkdayCase):
    def _open(self, *, online=True, shift_id='short'):
        from handover.workday import open_workday
        day = open_workday(
            self.database, shift_id, ('L', 'R'), online=online, short_tags=True,
        )
        self.addCleanup(day.close)
        return day

    def _deliver(self, day, kind, scan, line, **fields):
        tag = fields.pop('tag', 0)
        delivery = ShortDelivery(kind, position(line), scan, tag, **fields)
        day.accept_short(delivery)

    def _complete_belt(self, source, port, scan, start_line, end_line, blocks):
        source.start(scan, 0, position(start_line))
        for slot, value, line in blocks:
            port.emit(slot, value, position(line))
        produced = source.end(position(end_line))
        self.assertEqual(produced, len(blocks))

    def test_two_belts_share_tag_zero_and_keep_distinct_originals(self):
        port = ShortPort()
        self.addCleanup(port.close)
        source = ShortSource(port)
        self._complete_belt(source, port, 'X', 1, 2, [('L', 7, 41), ('R', 11, 93)])
        self._complete_belt(source, port, 'Y', 3, 4, [('L', 7, 14), ('R', 19, 32)])
        with self._open() as day:
            for line in (1, 2, 3, 4):
                source.deliver(position(line), day.accept_short)
            self.assertIsNone(day.request_scan('pending'))
            for line in (14, 41):
                source.deliver(position(line), day.accept_short)
            self.assertIsNone(day.request_scan('still-pending'))
            source.deliver(position(93), day.accept_short)
            self.assertEqual(day.scan_status('X').spread, 4)
            request_x = day.request_scan('QX')
            self.assertEqual(request_x.scan_id, 'X')
            source.deliver(position(32), day.accept_short)
            self.assertEqual(day.scan_status('Y').spread, 12)
            request_y = day.request_scan('QY')
            self.assertEqual(request_y.scan_id, 'Y')
        with self._open(online=False) as day:
            rows = day.shift_register('short').rows
            self.assertEqual([row.status.scan_id for row in rows], ['X', 'Y'])
            result_id = day.fulfill_request('QY')
            snapshot = day.read_result(result_id)
            self.assertEqual([block.value for block in snapshot.blocks], [7, 19])
            self.assertEqual(
                [block.origin.position.line for block in snapshot.blocks], [14, 32],
            )

    def test_duplicate_position_redelivers_once_and_commit_failure_rolls_back(self):
        port = ShortPort()
        self.addCleanup(port.close)
        source = ShortSource(port)
        self._complete_belt(source, port, 'X', 1, 2, [('L', 7, 41), ('R', 11, 93)])
        with self._open() as day:
            source.deliver(position(1), day.accept_short)
            source.deliver(position(2), day.accept_short)
            source.deliver(position(41), day.accept_short)
            attempts = []

            def authorizer(action, argument, _other, _database, _trigger):
                if action == sqlite3.SQLITE_TRANSACTION and argument == 'COMMIT':
                    attempts.append(argument)
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            day.connection.set_authorizer(authorizer)
            with self.assertRaises(sqlite3.DatabaseError):
                source.deliver(position(93), day.accept_short)
            day.connection.set_authorizer(None)
            self.assertFalse(day.connection.in_transaction)
            self.assertEqual(day.scan_status('X').state, 'in_progress')
            # Same original can be handed over again after the failure.
            source.deliver(position(93), day.accept_short)
            self.assertEqual(day.scan_status('X').state, 'complete')
            # Redelivery of an already-received position changes nothing.
            source.deliver(position(93), day.accept_short)
            source.deliver(position(41), day.accept_short)
            receipts = day.ledger.read_receipts('short', 'X')
            self.assertEqual([r.origin.position.line for r in receipts], [41, 93])

    def test_duplicate_slot_and_missing_slot_void_but_later_belt_can_close(self):
        with self._open() as day:
            self._deliver(day, 'start', 'X', 1)
            self._deliver(day, 'end', 'X', 2, expected_blocks=3)
            self._deliver(day, 'block', 'X', 41, slot='L', value=7)
            self._deliver(day, 'block', 'X', 42, slot='L', value=9)
            self._deliver(day, 'block', 'X', 43, slot='L', value=11)
            status = day.scan_status('X')
            self.assertEqual(status.state, 'void')
            self.assertIn('duplicate slots', status.reason)
            self.assertIsNone(day.request_scan('QX'))

            self._deliver(day, 'start', 'Y', 3)
            self._deliver(day, 'end', 'Y', 4, expected_blocks=2)
            self._deliver(day, 'block', 'Y', 14, slot='L', value=7)
            self._deliver(day, 'block', 'Y', 32, slot='R', value=19)
            self.assertEqual(day.scan_status('Y').state, 'complete')
            self.assertEqual(day.request_scan('QY').scan_id, 'Y')
            rows = day.shift_register('short').rows
            self.assertEqual(
                [(row.status.scan_id, row.status.state) for row in rows],
                [('X', 'void'), ('Y', 'complete')],
            )

    def test_redelivered_controls_are_idempotent_and_modes_do_not_mix(self):
        with self._open() as day:
            self._deliver(day, 'start', 'X', 1)
            self._deliver(day, 'start', 'X', 1)
            self._deliver(day, 'end', 'X', 2, expected_blocks=0)
            self._deliver(day, 'end', 'X', 2, expected_blocks=0)
            self.assertEqual(day.scan_status('X').state, 'void')
            with self.assertRaises(EventRejected):
                day.record_event(
                    {'kind': 'start', 'scan': 'Z', 'seq': 1, 'event_id': 'E1'}, position(9),
                )
        from handover.workday import open_workday
        ordinary = open_workday(self.database, 'ordinary', ('L', 'R'))
        self.addCleanup(ordinary.close)
        with self.assertRaises(EventRejected):
            ordinary.accept_short(ShortDelivery('start', position(1), 'Z', 0))

    def test_synthetic_sequences_keep_blocks_inside_scan_interval(self):
        with self._open() as day:
            self._deliver(day, 'start', 'X', 1)
            self._deliver(day, 'end', 'X', 2, expected_blocks=2)
            self._deliver(day, 'start', 'Y', 3)
            self._deliver(day, 'end', 'Y', 4, expected_blocks=2)
            self._deliver(day, 'block', 'Y', 14, slot='L', value=7)
            self._deliver(day, 'block', 'Y', 32, slot='R', value=8)
            self._deliver(day, 'block', 'X', 41, slot='L', value=5)
            self._deliver(day, 'block', 'X', 93, slot='R', value=6)
            x_status = day.scan_status('X')
            y_status = day.scan_status('Y')
            self.assertEqual(x_status.state, 'complete')
            self.assertEqual(y_status.state, 'complete')
            x_originals = day.ledger.read_receipts('short', 'X')
            self.assertTrue(all(
                x_status.open_sequence < r.origin.sequence < x_status.end_sequence
                for r in x_originals
            ))
            # Device end order: X ended first, so Y's later completion is most recent.
            self.assertEqual(day.request_scan('Q').scan_id, 'Y')
