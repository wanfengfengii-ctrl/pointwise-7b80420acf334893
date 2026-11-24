from dataclasses import dataclass

from quality.profile import ScanProfile


@dataclass(frozen=True)
class BlockCheck:
    value: int | None
    reason: str | None


def check_block(profile: ScanProfile, slot: str, raw_read: object, bad_reason: str | None = None) -> BlockCheck:
    reasons = []
    if slot not in profile.slots:
        reasons.append('slot is outside the scan profile')
    if bad_reason is not None:
        reasons.append(f'device bad block: {bad_reason}')
    if type(raw_read) is not int:
        reasons.append('raw read must be an integer')
    if reasons:
        return BlockCheck(None, ', '.join(reasons))
    return BlockCheck(raw_read, None)
