"""LangGraph checkpointing in the backend's Postgres.

Sharing the backend's database is deliberate — one database to operate, and run
state is queryable next to the domain data. But LangGraph creates its own tables
(``checkpoints``, ``checkpoint_blobs``, ``checkpoint_writes``,
``checkpoint_migrations``) and manages them with its own migration mechanism,
which must not be allowed to collide with the backend's Flyway-style ``V*.sql``
migrations in ``public``.

``AsyncPostgresSaver`` has no schema parameter, so isolation is done the only way
Postgres offers: a ``search_path`` on the connection. Everything LangGraph
creates lands in :data:`SCHEMA`, and the backend's schema is untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from wiab_team.logging import get_logger

log = get_logger(__name__)

SCHEMA = "wiab_team"


def with_schema(dsn: str, schema: str = SCHEMA) -> str:
    """Pin a DSN's ``search_path`` to ``schema``, preserving any other options.

    An explicit ``options`` already in the DSN wins: an operator who set one
    knows something we don't.
    """
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "options" in query:
        return dsn
    query["options"] = f"-csearch_path={schema}"
    return urlunsplit(parts._replace(query=urlencode(query)))


@asynccontextmanager
async def checkpointer(dsn: str, *, schema: str = SCHEMA) -> AsyncIterator[Any]:
    """Yield a set-up checkpointer, creating its schema if it does not exist.

    Used as a context manager so the connection pool is closed with the run:

        async with checkpointer(dsn) as saver:
            await run_team(payload, checkpointer=saver)
    """
    import psycopg
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    # The schema has to exist before search_path can point at it, and LangGraph's
    # setup() will not create it.
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    async with AsyncPostgresSaver.from_conn_string(with_schema(dsn, schema)) as saver:
        await saver.setup()
        log.info("checkpointer_ready", schema=schema)
        yield saver
