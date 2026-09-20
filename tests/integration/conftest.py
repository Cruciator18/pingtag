import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.main import create_app
from tests.integration.helpers import Env, FakeEmailSender

ROOT = Path(__file__).resolve().parents[2]
APP_DB_URL = os.getenv(
    "TEST_APP_DATABASE_URL", "postgresql+asyncpg://pingtag:pingtag@127.0.0.1:5432/pingtag_test"
)
# Redis DB 15, never the dev DB 0: it is flushed before every test.
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")


async def _ensure_database(url: URL) -> None:
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
def app_db() -> str:
    asyncio.run(_ensure_database(make_url(APP_DB_URL)))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = APP_DB_URL
    command.upgrade(cfg, "head")
    return APP_DB_URL


@pytest.fixture
async def env(app_db: str) -> AsyncIterator[Env]:
    settings = Settings(
        _env_file=None,
        secret_key="s" * 32,
        ip_hash_salt="h" * 16,
        database_url=app_db,
        redis_url=TEST_REDIS_URL,
        base_url="http://test",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        sender = FakeEmailSender()
        app.state.email_sender = sender
        await app.state.redis.flushdb()
        async with app.state.engine.begin() as conn:
            await conn.execute(text("TRUNCATE users CASCADE"))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(client=client, email=sender, app=app)
