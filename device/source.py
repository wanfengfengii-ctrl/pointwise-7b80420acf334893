from collections.abc import Callable

from device.events import DeviceEvent, EventRejected
from device.slots import OverwriteNotice, SlotBlock, SourceSlots


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
