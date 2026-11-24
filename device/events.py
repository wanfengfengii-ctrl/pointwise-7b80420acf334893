from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EventPosition:
    recording: str
    line: int

    def __post_init__(self) -> None:
        if not isinstance(self.recording, str) or not self.recording.strip():
            raise ValueError('recording must be a nonempty identifier')
        if type(self.line) is not int or not 1 <= self.line <= (1 << 63) - 1:
            raise ValueError('position must be a positive SQLite integer')

    def __str__(self) -> str:
        return f'{self.recording}:{self.line}'


class EventRejected(ValueError):
    def __init__(self, position: EventPosition, reason: str):
        self.position = position
        self.reason = reason
        super().__init__(f'{position}: {reason}')


@dataclass(frozen=True)
class DeviceEvent:
    kind: Literal['start', 'write', 'end']
    sequence: int
    scan_id: str
    event_id: str
    position: EventPosition
    slot: str | None = None
    raw_read: object = None
    bad_reason: str | None = None


def _required_text(payload: dict, key: str, position: EventPosition) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EventRejected(position, f'{key} must be nonempty text')
    return value


def parse_event(payload: object, position: EventPosition) -> DeviceEvent:
    if not isinstance(payload, dict):
        raise EventRejected(position, 'event must be an object')
    kind = payload.get('kind')
    if kind not in ('start', 'write', 'end'):
        raise EventRejected(position, 'kind must be start, write or end')
    sequence = payload.get('seq')
    if type(sequence) is not int or not 1 <= sequence <= (1 << 63) - 1:
        raise EventRejected(position, 'seq must be a positive SQLite integer')
    scan_id = _required_text(payload, 'scan', position)
    event_id = _required_text(payload, 'event_id', position)
    if kind != 'write':
        return DeviceEvent(kind, sequence, scan_id, event_id, position)

    slot = _required_text(payload, 'slot', position)
    if 'read' not in payload:
        raise EventRejected(position, 'write requires a read field')
    bad_reason = _required_text(payload, 'bad', position) if 'bad' in payload else None
    return DeviceEvent(kind, sequence, scan_id, event_id, position, slot, payload['read'], bad_reason)
