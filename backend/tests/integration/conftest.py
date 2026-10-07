"""Fixtures for tests that need the real SQL/v1 schema. Each test module here
skips itself unless ENVIRONMENT=test (CI), so none of this ever runs against
the database a developer's .env points at."""

import asyncio
import contextlib
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.core import live
from app.core.db import engine, get_migration_engine, get_worker_engine

needs_test_database = pytest.mark.skipif(get_settings().environment != "test", reason="needs the CI test database")


@asynccontextmanager
async def connected_listener():
    """This process's live listener (app/core/live.py), up and listening."""
    live.LISTENER_STATE["connected_at"] = None
    task = asyncio.create_task(live.run_listener_forever())
    async with asyncio.timeout(10):
        while live.LISTENER_STATE["connected_at"] is None:
            await asyncio.sleep(0.01)
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.fixture
async def listener():
    async with connected_listener():
        yield


@pytest.fixture
async def admin():
    """Runs one statement over the admin connection (bypasses RLS), to
    arrange and inspect rows."""
    admin_engine = get_migration_engine()

    async def run(sql: str, **params):
        async with admin_engine.begin() as conn:
            return await conn.execute(text(sql), params)

    yield run
    await admin_engine.dispose()


@pytest.fixture(autouse=True)
async def _fresh_pools():
    """The app's pools are created at import time; their connections belong
    to one test's event loop and must not be reused by the next."""
    yield
    await engine.dispose()
    await get_worker_engine().dispose()
