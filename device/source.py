from collections.abc import Callable
from dataclasses import dataclass

from device.events import DeviceEvent, EventPosition, EventRejected
from device.short_port import RawBlock, ShortPort
from device.slots import OverwriteNotice, SlotBlock, SourceSlots


@dataclass(frozen=True)
class ShortDelivery:
    """One control record or one block handed over to the station.

    The shape is an implementation choice of this project: every record
    carries its producing scan so a recycled short tag cannot transfer an
    undelivered original's ownership to a newer belt.
    """

    kind: str
    position: EventPosition
    scan_id: str
    tag: int
    expected_blocks: int | None = None
    slot: str | None = None
    value: int | None = None


class DeviceSource:
    def __init__(
        self,
        accept_event: Callable[[DeviceEvent, Callable[[], None] | None], None],
        before_overwrite: Callable[[OverwriteNotice], None],
    ):
        self.slots = SourceSlots()
        self._accept_event = accept_event
        self._before_overwrite = before_overwrite
        self._online = True

    def deliver(self, event: DeviceEvent) -> None:
        if not self._online:
            raise EventRejected(event.position, 'source is offline')
        if event.kind != 'write':
            self._accept_event(event, None)
            return

        def accept_write(notify: Callable[[], None]) -> None:
            self._accept_event(event, notify)

        self.slots.replace(SlotBlock(event), accept_write, self._before_overwrite)

    def close(self) -> None:
        self.slots.close()
        self._online = False


class ShortSource:
    """Drives one controller through recycled short tags.

    The controller keeps running while station databases reopen, so this
    object never waits for the station. It remembers which scan owns every
    produced original: a recycled tag must never let a new belt claim the
    undelivered blocks of the previous one.
    """

    def __init__(self, port: ShortPort):
        self.port = port
        self._starts: dict[EventPosition, tuple[str, int]] = {}
        self._ends: dict[EventPosition, tuple[str, int]] = {}
        self._blocks: dict[EventPosition, RawBlock] = {}
        self._block_owner: dict[EventPosition, str] = {}
        self._scan_tag: dict[str, int] = {}
        self._active_scan_by_tag: dict[int, str] = {}
        self._used_positions: set[EventPosition] = set()
        port.observe(self._observe_block)

    def _active_scan(self, tag: int) -> str:
        try:
            return self._active_scan_by_tag[tag]
        except KeyError as error:
            raise RuntimeError(f'tag {tag} has no active scan') from error

    def _observe_block(self, record: RawBlock) -> None:
        # The observer fires while the producing scan's begin/end window is
        # open, so the active scan is the original owner even after the
        # hardware recycles the tag for a later belt.
        self._blocks[record.position] = record
        self._block_owner[record.position] = self._active_scan(record.tag)

    def start(self, scan_id: str, tag: int, position: EventPosition) -> None:
        if not isinstance(scan_id, str) or not scan_id.strip():
            raise ValueError('scan id must be nonempty text')
        if type(tag) is not int or not 0 <= tag < 256:
            raise ValueError('tag must be an unsigned byte')
        if not isinstance(position, EventPosition):
            raise ValueError('start requires a recording position')
        if position in self._used_positions:
            raise ValueError('recording position must be unique')
        if scan_id in self._scan_tag:
            raise ValueError(f'scan {scan_id!r} already started')
        self.port.begin(tag)
        self._used_positions.add(position)
        self._starts[position] = (scan_id, tag)
        self._scan_tag[scan_id] = tag
        self._active_scan_by_tag[tag] = scan_id

    def end(self, position: EventPosition) -> int:
        if not isinstance(position, EventPosition):
            raise ValueError('end requires a recording position')
        if position in self._used_positions:
            raise ValueError('recording position must be unique')
        active_tag = self.port.active_tag
        if active_tag is None:
            raise RuntimeError('no short-tag scan is active')
        produced = self.port.end()
        self._used_positions.add(position)
        scan_id = self._active_scan_by_tag[active_tag]
        self._ends[position] = (scan_id, produced)
        return produced

    def deliver(
        self,
        position: EventPosition,
        accept: Callable[[ShortDelivery], None],
    ) -> None:
        """Hand over one control record or one block at its device position."""
        if position in self._starts:
            scan_id, tag = self._starts[position]
            accept(ShortDelivery('start', position, scan_id, tag))
            return
        if position in self._ends:
            scan_id, produced = self._ends[position]
            accept(ShortDelivery(
                'end', position, scan_id, self._scan_tag[scan_id],
                expected_blocks=produced,
            ))
            return
        record = self._blocks.get(position)
        if record is None:
            raise KeyError(f'no undelivered short-tag original at {position}')
        accept(ShortDelivery(
            'block', position, self._block_owner[record.position], record.tag,
            slot=record.slot, value=record.value,
        ))
