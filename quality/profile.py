from collections.abc import Iterable
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class ScanProfile:
    slots: tuple[str, ...]


def validate_profile(slots: Iterable[str]) -> ScanProfile:
    if isinstance(slots, (str, bytes)):
        raise ValueError('profile must supply an ordered sequence of slot identifiers')
    ordered = tuple(slots)
    if not ordered or any(not isinstance(slot, str) or not slot.strip() for slot in ordered):
        raise ValueError('profile requires nonempty slot identifiers')
    if len(set(ordered)) != len(ordered):
        raise ValueError('profile slot identifiers must be unique')
    return ScanProfile(ordered)


def encode_profile(profile: ScanProfile) -> str:
    return json.dumps(profile.slots, ensure_ascii=True, separators=(',', ':'))


def decode_profile(text: str) -> ScanProfile:
    slots = json.loads(text)
    if not isinstance(slots, list):
        raise ValueError('stored profile must be a slot list')
    return validate_profile(slots)
