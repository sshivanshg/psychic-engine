"""Database access helpers.

Thin wrapper around psycopg so the rest of the code never has to know the connection string.
Raw SQL via psycopg (no ORM) is clearer — and more educational — than an ORM at this scale.

Connections come from a process-wide **pool** (`psycopg_pool`). Before this, every query opened a
fresh `psycopg.connect()` and paid a TCP + auth handshake; a single `analyze()` builds an
`AnalysisContext` that fans out ~6 reads, so the per-query connect cost added up on every API
request. The pool amortises that: connections are reused, and pgvector is registered ONCE per
physical connection via the `configure` hook (so RAG queries get the `vector` adapter for free and
non-RAG queries don't pay for it). The pool is created lazily, so importing this module never needs
a live DB — pure-function code paths (and the unit tests) don't touch Postgres at all.

Tunables (env): DB_POOL_MIN (1), DB_POOL_MAX (10), DB_POOL_TIMEOUT seconds to wait for a free
connection (5).
"""

import atexit
import os
from contextlib import AbstractContextManager

import psycopg

from .config import DATABASE_URL
from .log import get_logger

log = get_logger()

_pool = None


def _configure(conn: psycopg.Connection) -> None:
    """Run once per new physical connection. Register the pgvector adapter so `vector` columns
    round-trip as numpy arrays. Tolerant: a DB without the `vector` extension still serves every
    non-RAG query (prices/risk/fundamentals) — only `docs` actually needs the adapter."""
    try:
        from pgvector.psycopg import register_vector
        register_vector(conn)
    except Exception as e:  # noqa: BLE001 - vector is optional; don't break non-RAG connections
        log.debug("pgvector not registered on this connection: %s", e)


def _get_pool():
    """Lazily build + open the process-wide connection pool (so import never needs a live DB)."""
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=int(os.getenv("DB_POOL_MIN", "1")),
            max_size=int(os.getenv("DB_POOL_MAX", "10")),
            timeout=float(os.getenv("DB_POOL_TIMEOUT", "5")),
            configure=_configure,
            open=False,
            name="tradeos",
        )
        _pool.open()
        atexit.register(_pool.close)
    return _pool


def get_connection() -> AbstractContextManager[psycopg.Connection]:
    """A pooled connection, used as a context manager:

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")

    On a clean exit the connection is committed and RETURNED to the pool (not closed); on an
    exception it's rolled back and returned. pgvector is already registered (see `_configure`).
    """
    return _get_pool().connection()
