"""Shared test setup.

Safety model (tests must never touch the development database):
  * Tests run against a dedicated database whose name ends with ``_test``.
  * ``TEST_DATABASE_URL`` selects it; by default it is ``DATABASE_URL`` with ``_test``
    appended to the database name (e.g. ``tasktools`` -> ``tasktools_test``).
  * A hard guard aborts the whole session before any connection is made when the
    resolved database name does not end with ``_test``.
  * Tests never TRUNCATE/DROP/bulk-DELETE; they only remove rows they created, by id/name.

DATABASE_URL is exported here, before any ``app`` module is imported, so that
``app.config`` and ``get_pool()`` also point at the test database.
"""
import asyncio
import os
import pathlib
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

DEFAULT_URL = "postgresql://tasktools:tasktools@127.0.0.1:5440/tasktools"
SCHEMA = pathlib.Path(__file__).parents[1] / "app/db/schema.sql"


def _with_db(url: str, name: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))


def _db_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def _resolve_test_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    base = os.environ.get("DATABASE_URL", DEFAULT_URL)
    return _with_db(base, _db_name(base) + "_test")


TEST_URL = _resolve_test_url()
os.environ["DATABASE_URL"] = TEST_URL  # before any `import app...`


def pytest_configure(config):
    name = _db_name(TEST_URL)
    if not name.endswith("_test"):
        pytest.exit(
            f"REFUSING TO RUN: test database name {name!r} does not end with '_test'. "
            "Tests never run against a non-test database. Set TEST_DATABASE_URL to a *_test database.",
            returncode=2,
        )


async def _ensure_database(url: str) -> None:
    name = _db_name(url)
    assert name.endswith("_test")
    admin = await asyncpg.connect(_with_db(url, "postgres"))
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name)
        if not exists:
            await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(SCHEMA.read_text())
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    try:
        from app.tools.registry import seed_builtin_tools
        await seed_builtin_tools(pool)
    finally:
        await pool.close()


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    assert _db_name(TEST_URL).endswith("_test")
    asyncio.run(_ensure_database(TEST_URL))
    return TEST_URL


@pytest.fixture(scope="session")
def dsn(_test_database) -> str:
    return _test_database


@pytest.fixture
async def pool(dsn):
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
def run(dsn):
    """Sync helper: run(async_fn) calls async_fn(pool) on a fresh loop and pool."""
    def _run(fn):
        async def main():
            p = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
            try:
                return await fn(p)
            finally:
                await p.close()
        return asyncio.run(main())
    return _run


@pytest.fixture(autouse=True)
def track_task(monkeypatch):
    """Record every task created through the service layer and delete only those rows afterwards."""
    from app.tasks import service as svc
    orig = svc.create_task
    created: list[int] = []

    async def tracked(*a, **kw):
        t = await orig(*a, **kw)
        created.append(int(t["id"]))
        return t

    monkeypatch.setattr(svc, "create_task", tracked)
    yield created
    if created:
        async def cleanup():
            c = await asyncpg.connect(TEST_URL)
            try:
                await c.execute("DELETE FROM tasks WHERE id = ANY($1::bigint[])", created)
            finally:
                await c.close()
        asyncio.run(cleanup())


@pytest.fixture
async def track_tool(pool):
    """Register custom tool names created by a test; they are removed by name afterwards."""
    names: list[str] = []
    yield names
    if names:
        await pool.execute("DELETE FROM tools WHERE kind<>'builtin' AND name = ANY($1::text[])", names)
