from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from config import settings

_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


async def create_checkpointer() -> AsyncPostgresSaver:
    global _pool, _saver
    _pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.checkpointer_pool_min_size,
        max_size=settings.checkpointer_pool_max_size,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
        open=False,
    )
    await _pool.open()
    _saver = AsyncPostgresSaver(_pool)
    await _saver.setup()
    return _saver


async def close_checkpointer() -> None:
    global _pool, _saver
    if _pool is not None:
        await _pool.close()
    _pool = None
    _saver = None


def get_checkpointer() -> AsyncPostgresSaver:
    if _saver is None:
        raise RuntimeError("Checkpointer not initialised — call create_checkpointer() first")
    return _saver
