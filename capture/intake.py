from collections.abc import Callable
import sqlite3

from capture.ledger import CaptureLedger
from capture.origin import CapturedBlock, make_origin
from capture.retention import MaterialStore
from device.events import DeviceEvent, EventRejected
from device.slots import OverwriteNotice
from quality.blocks import check_block
from quality.closeout import close_scan, evaluate_closeout
from quality.profile import ScanProfile
from quality.scanbook import ScanBook, ScanStateError, ScanStatus
from workdb import transaction


class CaptureIntake:
    def __init__(
        self,
        connection: sqlite3.Connection,
        shift_id: str,
        profile: ScanProfile,
        scans: ScanBook,
        ledger: CaptureLedger,
        materials: MaterialStore,
    ):
        self.connection = connection
        self.shift_id = shift_id
        self.profile = profile
        self.scans = scans
        self.ledger = ledger
        self.materials = materials

    def before_overwrite(self, notice: OverwriteNotice) -> None:
        old = notice.read_old_block()
        incoming = notice.replacement
        if old.slot != incoming.slot or old.event.sequence >= incoming.event.sequence:
            raise EventRejected(incoming.event.position, 'overwrite notice does not match physical slot and order')
        origin = make_origin(self.shift_id, old.event)
        receipt = self.ledger.find_receipt(origin)
        if receipt is not None and receipt.origin != origin:
            raise EventRejected(incoming.event.position, 'overwrite origin disagrees with recorded receipt')

    def accept_event(self, event: DeviceEvent, before_write: Callable[[], None] | None = None) -> None:
        with transaction(self.connection, write=True):
            if event.sequence <= self.ledger.last_sequence(self.shift_id):
                raise EventRejected(event.position, 'device sequence has already advanced')
            try:
                if event.kind == 'start':
                    self.scans.open_scan(self.shift_id, event.scan_id, event.sequence, self.profile)
                else:
                    status = self.scans.scan_status(self.shift_id, event.scan_id)
                    if status is None or status.state != 'in_progress':
                        raise ScanStateError(f'scan {event.scan_id!r} is not in progress')
                    if event.kind == 'write':
                        self._accept_write(event, status, before_write)
                    else:
                        self._accept_end(event, status)
            except ScanStateError as error:
                raise EventRejected(event.position, str(error)) from error
            self.ledger.record_sequence(self.shift_id, event.sequence)

    def _accept_write(
        self, event: DeviceEvent, status: ScanStatus, before_write: Callable[[], None] | None,
    ) -> None:
        origin = make_origin(self.shift_id, event)
        check = check_block(status.profile, origin.slot, event.raw_read, event.bad_reason)
        if before_write is not None:
            before_write()
        self.ledger.record_receipt(origin, check.reason)
        if check.value is not None:
            self.materials.stage_block(CapturedBlock(origin, check.value))

    def _accept_end(self, event: DeviceEvent, status: ScanStatus) -> None:
        receipts = self.ledger.read_receipts(self.shift_id, event.scan_id)
        staged = self.materials.staged_blocks(self.shift_id, event.scan_id)
        decision = evaluate_closeout(status.profile, receipts, staged)
        if decision.complete:
            self.materials.seal_scan(self.shift_id, event.scan_id, len(status.profile.slots))
        else:
            self.materials.discard_staging(self.shift_id, event.scan_id)
            self.ledger.discard_receipts(self.shift_id, event.scan_id)
        close_scan(self.scans, status, event.sequence, decision)
