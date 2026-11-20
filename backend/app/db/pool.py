from pathlib import Path

import asyncpg

from app import config

_pool: asyncpg.Pool | None = None
SCHEMA = Path(__file__).with_name("schema.sql")


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=10)
    return _pool


async def init_db() -> asyncpg.Pool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA.read_text())
    try:
        from app.tools.registry import seed_builtin_tools
    except ImportError:
        return pool
    await seed_builtin_tools(pool)
    return pool


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
