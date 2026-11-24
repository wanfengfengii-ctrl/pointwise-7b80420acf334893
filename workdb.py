from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


def connect_workdb(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection, *, write: bool = False) -> Iterator[None]:
    connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
