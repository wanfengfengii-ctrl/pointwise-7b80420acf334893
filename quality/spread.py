from collections.abc import Iterable
from decimal import Decimal, InvalidOperation


def integer_spread(values: Iterable[int]) -> int:
    readings = iter(values)
    try:
        first = next(readings)
    except StopIteration as error:
        raise ValueError('empty input has no spread') from error
    if type(first) is not int:
        raise ValueError('spread requires integer readings')
    least = greatest = first
    for value in readings:
        if type(value) is not int:
            raise ValueError('spread requires integer readings')
        least = min(least, value)
        greatest = max(greatest, value)
    return greatest - least


def integer_text(value: int) -> str:
    if type(value) is not int:
        raise ValueError('integer required')
    return format(Decimal(value), 'f')


def integer_from_text(text: str) -> int:
    if not isinstance(text, str) or not text:
        raise ValueError('stored integer must be decimal text')
    digits = text[1:] if text.startswith('-') else text
    if not digits.isascii() or not digits.isdecimal():
        raise ValueError('invalid stored integer text')
    try:
        value = int(Decimal(text))
    except (InvalidOperation, ValueError, OverflowError) as error:
        raise ValueError('invalid stored integer text') from error
    if integer_text(value) != text:
        raise ValueError('stored integer text is not canonical')
    return value
