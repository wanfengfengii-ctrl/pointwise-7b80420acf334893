from dataclasses import dataclass
from collections.abc import Iterable

from device.events import DeviceEvent, EventPosition


@dataclass(frozen=True)
class BlockOrigin:
    shift_id: str
    scan_id: str
    slot: str
    event_id: str
    sequence: int
    position: EventPosition


@dataclass(frozen=True)
class CapturedBlock:
    origin: BlockOrigin
    value: int


def make_origin(shift_id: str, event: DeviceEvent) -> BlockOrigin:
    if event.kind != 'write' or event.slot is None:
        raise ValueError('an original block requires an actual write event')
    return BlockOrigin(shift_id, event.scan_id, event.slot, event.event_id, event.sequence, event.position)


def origins_match(blocks: Iterable[CapturedBlock], shift_id: str, scan_id: str) -> bool:
    seen_sequences = set()
    for block in blocks:
        origin = block.origin
        if (
            origin.shift_id != shift_id
            or origin.scan_id != scan_id
            or not origin.slot.strip()
            or not origin.event_id.strip()
            or origin.sequence <= 0
            or origin.sequence in seen_sequences
            or type(block.value) is not int
        ):
            return False
        seen_sequences.add(origin.sequence)
    return True
