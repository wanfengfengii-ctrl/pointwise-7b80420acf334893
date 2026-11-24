from collections.abc import Callable
from dataclasses import dataclass

from device.events import EventPosition


@dataclass(frozen=True)
class RawBlock:
    tag: int
    slot: str
    value: int
    position: EventPosition


class ShortPort:
    """Deterministic stand-in for a controller and its delayed transport."""

    def __init__(self):
        self.active_tag: int | None = None
        self._queue: dict[EventPosition, RawBlock] = {}
        self._seen: set[EventPosition] = set()
        self._observer: Callable[[RawBlock], None] | None = None
        self._produced = 0
        self._closed = False
        self.fail_next_cut = False

    def _require_online(self) -> None:
        if self._closed:
            raise RuntimeError('short port is offline')

    def observe(self, callback: Callable[[RawBlock], None] | None) -> None:
        self._require_online()
        self._observer = callback

    def begin(self, tag: int) -> None:
        self._require_online()
        if type(tag) is not int or not 0 <= tag < 256:
            raise ValueError('tag must be an unsigned byte')
        if self.active_tag is not None:
            raise RuntimeError('controller is still scanning')
        self.active_tag = tag
        self._produced = 0

    def emit(self, slot: str, value: int, position: EventPosition) -> None:
        self._require_online()
        if self.active_tag is None:
            raise RuntimeError('controller is idle')
        if not isinstance(slot, str) or not slot.strip() or type(value) is not int:
            raise ValueError('expected a slot and an integer reading')
        if not isinstance(position, EventPosition) or position in self._seen:
            raise ValueError('expected a fresh recording position')
        record = RawBlock(self.active_tag, slot, value, position)
        self._queue[position] = record
        self._seen.add(position)
        self._produced += 1
        if self._observer is not None:
            self._observer(record)

    def end(self) -> int:
        self._require_online()
        if self.active_tag is None:
            raise RuntimeError('controller is idle')
        self.active_tag = None
        return self._produced

    def read(self, position: EventPosition) -> RawBlock | None:
        self._require_online()
        return self._queue.get(position)

    def release(self, position: EventPosition) -> None:
        self._require_online()
        self._queue.pop(position, None)

    def cut(self, tag: int, take: Callable[[tuple[RawBlock, ...]], None]) -> None:
        self._require_online()
        if tag == self.active_tag:
            raise RuntimeError('cannot cut a tag that is still scanning')
        if self.fail_next_cut:
            self.fail_next_cut = False
            raise OSError('controller queue cut failed')
        records = tuple(sorted(
            (record for record in self._queue.values() if record.tag == tag),
            key=lambda record: (record.position.recording, record.position.line),
        ))
        take(records)
        for record in records:
            del self._queue[record.position]

    def close(self) -> None:
        self._queue.clear()
        self._observer = None
        self.active_tag = None
        self._closed = True
