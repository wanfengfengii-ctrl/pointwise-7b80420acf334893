from dataclasses import dataclass
import sqlite3

from quality.profile import ScanProfile, decode_profile, encode_profile
from workdb import transaction


@dataclass(frozen=True)
class Shift:
    shift_id: str
    profile: ScanProfile


class ShiftBook:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS shifts (
                shift_id TEXT PRIMARY KEY NOT NULL,
                profile TEXT NOT NULL
            )
        ''')

    def open_shift(self, shift_id: str, profile: ScanProfile | None = None) -> Shift:
        if not isinstance(shift_id, str) or not shift_id.strip():
            raise ValueError('shift_id must be nonempty text')
        with transaction(self.connection, write=True):
            prior = self.find_shift(shift_id)
            if prior is not None:
                if profile is not None and prior.profile != profile:
                    raise ValueError(f'profile already fixed for shift {shift_id!r}')
                return prior
            if profile is None:
                raise ValueError(f'new shift {shift_id!r} requires a profile')
            self.connection.execute('INSERT INTO shifts (shift_id, profile) VALUES (?, ?)', (shift_id, encode_profile(profile)))
            return Shift(shift_id, profile)

    def find_shift(self, shift_id: str) -> Shift | None:
        row = self.connection.execute('SELECT profile FROM shifts WHERE shift_id = ?', (shift_id,)).fetchone()
        return Shift(shift_id, decode_profile(row['profile'])) if row is not None else None
