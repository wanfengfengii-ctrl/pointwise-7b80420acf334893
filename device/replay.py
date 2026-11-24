from device.events import DeviceEvent, EventPosition, EventRejected, parse_event
from device.source import DeviceSource


class Replay:
    def __init__(self, source: DeviceSource, last_sequence: int = 0):
        self.source = source
        self.last_sequence = last_sequence

    def record_event(self, payload: object, position: EventPosition) -> DeviceEvent:
        event = parse_event(payload, position)
        if event.sequence <= self.last_sequence:
            raise EventRejected(position, 'seq must advance past the last accepted event')
        self.source.deliver(event)
        self.last_sequence = event.sequence
        return event
