import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://pingtag:pingtag@127.0.0.1:5432/pingtag_test"
)
EXPECTED_TABLES = {
    "users",
    "push_subscriptions",
    "notification_channels",
    "tags",
    "blocks",
    "scans",
    "deliveries",
    "replies",
}


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


async def _table_names(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def alembic_cfg() -> Config:
    asyncio.run(_ensure_database(make_url(TEST_DB_URL)))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = TEST_DB_URL
    return cfg


def test_upgrade_creates_all_tables(alembic_cfg: Config) -> None:
    command.downgrade(alembic_cfg, "base")  # start clean
    command.upgrade(alembic_cfg, "head")
    assert EXPECTED_TABLES <= asyncio.run(_table_names(TEST_DB_URL))


def test_downgrade_removes_everything(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    remaining = asyncio.run(_table_names(TEST_DB_URL)) - {"alembic_version"}
    assert remaining == set()
