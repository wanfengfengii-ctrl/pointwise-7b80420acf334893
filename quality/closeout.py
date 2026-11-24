from collections import Counter
from dataclasses import dataclass

from capture.ledger import CaptureReceipt
from capture.origin import CapturedBlock
from capture.retention import MaterialUnavailable
from quality.profile import ScanProfile
from quality.scanbook import ScanBook, ScanStatus
from quality.spread import integer_spread


@dataclass(frozen=True)
class CloseoutDecision:
    spread: int | None
    reason: str | None

    @property
    def complete(self) -> bool:
        return self.reason is None


def evaluate_closeout(
    profile: ScanProfile,
    receipts: tuple[CaptureReceipt, ...],
    staged: tuple[CapturedBlock, ...],
) -> CloseoutDecision:
    received_slots = Counter(receipt.origin.slot for receipt in receipts)
    reasons = [
        f'{receipt.origin.slot}@{receipt.origin.sequence}: {receipt.reason}'
        for receipt in receipts if receipt.reason is not None
    ]
    duplicate = [slot for slot, count in received_slots.items() if count > 1]
    missing = [slot for slot in profile.slots if slot not in received_slots]
    if duplicate:
        reasons.append('duplicate slots: ' + ', '.join(duplicate))
    if missing:
        reasons.append('missing slots: ' + ', '.join(missing))
    if reasons:
        return CloseoutDecision(None, ' | '.join(reasons))

    if tuple(block.origin for block in staged) != tuple(receipt.origin for receipt in receipts):
        raise MaterialUnavailable('qualified receipts and staged originals disagree')
    values_by_slot = {block.origin.slot: block.value for block in staged}
    return CloseoutDecision(integer_spread(values_by_slot[slot] for slot in profile.slots), None)


def close_scan(scans: ScanBook, status: ScanStatus, sequence: int, decision: CloseoutDecision) -> ScanStatus:
    return scans.save_closeout(status.shift_id, status.scan_id, sequence, decision.spread, decision.reason)
