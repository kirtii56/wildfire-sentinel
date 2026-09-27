"""Database connection helper.

Plain psycopg with explicit SQL — no ORM. The schema is small, the queries are
hand-written, and migrations are checked-in SQL, so an ORM would add a layer
without removing any work.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection]:
    """Open a connection with dict rows. Commits on success, rolls back on error."""
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        yield conn
