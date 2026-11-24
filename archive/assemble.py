from dataclasses import dataclass

from archive.bindings import RequestBinding, read_bound_material
from capture.origin import CapturedBlock
from capture.recovery import MaterialReader
from capture.retention import MaterialUnavailable
from quality.profile import ScanProfile
from quality.scanbook import ScanStatus
from quality.spread import integer_spread


@dataclass(frozen=True)
class ResultDraft:
    request_id: str
    shift_id: str
    scan_id: str
    profile: ScanProfile
    spread: int
    blocks: tuple[CapturedBlock, ...]


def assemble_result(
    request_id: str, binding: RequestBinding, status: ScanStatus, reader: MaterialReader,
) -> ResultDraft:
    if status.state != 'complete' or (binding.shift_id, binding.scan_id) != (status.shift_id, status.scan_id):
        raise MaterialUnavailable('request and scan conclusion disagree')
    originals = read_bound_material(binding, reader)
    by_slot = {block.origin.slot: block for block in originals}
    if len(by_slot) != len(originals) or set(by_slot) != set(status.profile.slots):
        raise MaterialUnavailable('bound material does not contain each specified slot exactly once')
    if status.end_sequence is None or any(
        not status.open_sequence < block.origin.sequence < status.end_sequence for block in originals
    ):
        raise MaterialUnavailable('block origin is outside the bound scan interval')
    blocks = tuple(by_slot[slot] for slot in status.profile.slots)
    spread = integer_spread(block.value for block in blocks)
    if spread != status.spread:
        raise MaterialUnavailable('bound material spread disagrees with the complete scan')
    return ResultDraft(request_id, status.shift_id, status.scan_id, status.profile, spread, blocks)
