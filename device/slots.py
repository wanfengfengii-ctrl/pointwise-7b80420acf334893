from collections.abc import Callable
from dataclasses import dataclass

from device.events import DeviceEvent


@dataclass(frozen=True)
class SlotBlock:
    event: DeviceEvent

    @property
    def slot(self) -> str:
        if self.event.kind != 'write' or self.event.slot is None:
            raise ValueError('physical slot requires a write event')
        return self.event.slot


class OverwriteNotice:
    def __init__(self, old: SlotBlock, replacement: SlotBlock):
        self.replacement = replacement
        self._old: SlotBlock | None = old

    def read_old_block(self) -> SlotBlock:
        if self._old is None:
            raise RuntimeError('overwrite window has ended')
        return self._old

    def _expire(self) -> None:
        self._old = None


class SourceSlots:
    def __init__(self):
        self._blocks: dict[str, SlotBlock] = {}
        self._online = True
        self._changing = False

    def read_slot(self, slot: str) -> SlotBlock | None:
        if not self._online:
            raise RuntimeError('source slots are offline')
        return self._blocks.get(slot)

    def replace(
        self,
        replacement: SlotBlock,
        accept_write: Callable[[Callable[[], None]], None],
        on_overwrite: Callable[[OverwriteNotice], None],
    ) -> None:
        if not self._online or self._changing:
            raise RuntimeError('source slots cannot start a write')
        slot = replacement.slot
        old = self._blocks.get(slot)

        def notify() -> None:
            if old is None:
                return
            notice = OverwriteNotice(old, replacement)
            try:
                on_overwrite(notice)
            finally:
                notice._expire()

        self._changing = True
        try:
            accept_write(notify)
            self._blocks[slot] = replacement
        finally:
            self._changing = False

    def close(self) -> None:
        if self._changing:
            raise RuntimeError('source slots are delivering a write')
        self._blocks.clear()
        self._online = False
