from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Iterator

from device.events import EventPosition, EventRejected


@dataclass(frozen=True)
class RecordedItem:
    position: EventPosition
    payload: object


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate field {key!r}')
        result[key] = value
    return result


def _read_integer(text: str) -> int:
    return int(Decimal(text))


def _reject_constant(text: str) -> None:
    raise ValueError(f'invalid JSON constant {text}')


def read_recording(path: str | Path) -> Iterator[RecordedItem]:
    recording = Path(path)
    with recording.open('rb') as stream:
        for line_number, raw_line in enumerate(stream, 1):
            position = EventPosition(recording.name, line_number)
            try:
                payload = json.loads(
                    raw_line.decode('utf-8'),
                    object_pairs_hook=_unique_fields,
                    parse_int=_read_integer,
                    parse_constant=_reject_constant,
                )
            except (ValueError, ArithmeticError) as error:
                raise EventRejected(position, f'invalid recording: {error}') from error
            yield RecordedItem(position, payload)
