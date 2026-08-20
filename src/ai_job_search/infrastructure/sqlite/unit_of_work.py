from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    if connection.in_transaction:
        raise RuntimeError("Nested transactions are not supported")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
