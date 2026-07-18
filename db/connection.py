import json

import asyncpg

from config import settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Without this, asyncpg treats jsonb params/columns as opaque text —
    # every repository touching verification_results.explanations would
    # otherwise need its own json.dumps/loads. One codec, registered once,
    # means callers just pass/receive plain dicts.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text",
    )


async def create_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        init=_init_connection,
        # Neon's pooled endpoint (PgBouncer-style, transaction mode) can route
        # the same client connection to a different backend over time, which
        # invalidates asyncpg's server-side prepared statement cache and
        # surfaces as intermittent "prepared statement does not exist"
        # errors. Harmless against plain Postgres (local Docker Compose) too.
        statement_cache_size=0,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call create_pool() first")
    return _pool
