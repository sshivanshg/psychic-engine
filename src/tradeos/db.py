"""Database access helpers.

Thin wrapper around psycopg so the rest of the code never has to know the
connection string. Phase 0 is small enough that raw SQL via psycopg is clearer
(and more educational) than pulling in an ORM.
"""

import psycopg

from .config import DATABASE_URL


def get_connection() -> psycopg.Connection:
    """Open a new connection to the TradeOS database.

    Use as a context manager:

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")

    On a clean exit psycopg commits and closes the connection for you.
    """
    return psycopg.connect(DATABASE_URL)
