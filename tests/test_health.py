from typing import Self

import httpx
import pytest

from app.core.config import Settings
from app.main import create_app


def make_settings(**kw: object) -> Settings:
    base: dict[str, object] = {"secret_key": "s" * 32, "ip_hash_salt": "h" * 16}
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


class FakeConn:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    async def __aenter__(self) -> Self:
        if not self.healthy:
            raise ConnectionError("db down")
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, *_: object) -> None:
        return None


class FakeEngine:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    def connect(self) -> FakeConn:
        return FakeConn(self.healthy)


class FakeRedis:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    async def ping(self) -> bool:
        if not self.healthy:
            raise ConnectionError("redis down")
        return True


def make_client(
    *, db_ok: bool = True, redis_ok: bool = True, **settings_kw: object
) -> httpx.AsyncClient:
    app = create_app(make_settings(**settings_kw))
    app.state.engine = FakeEngine(db_ok)
    app.state.redis = FakeRedis(redis_ok)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_healthz_ok() -> None:
    async with make_client() as client:
        r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_request_id_generated_echoed_and_sanitised() -> None:
    async with make_client() as client:
        generated = await client.get("/healthz")
        echoed = await client.get("/healthz", headers={"X-Request-ID": "abc12345-req"})
        rejected = await client.get("/healthz", headers={"X-Request-ID": "bad id!"})
    assert len(generated.headers["x-request-id"]) == 32
    assert echoed.headers["x-request-id"] == "abc12345-req"
    assert rejected.headers["x-request-id"] != "bad id!"


async def test_readyz_ok() -> None:
    async with make_client() as client:
        r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.parametrize(
    ("db_ok", "redis_ok", "expected"),
    [
        (False, True, {"database": "fail", "redis": "ok"}),
        (True, False, {"database": "ok", "redis": "fail"}),
        (False, False, {"database": "fail", "redis": "fail"}),
    ],
)
async def test_readyz_reports_failing_dependency(
    db_ok: bool, redis_ok: bool, expected: dict[str, str]
) -> None:
    async with make_client(db_ok=db_ok, redis_ok=redis_ok) as client:
        r = await client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"] == expected


async def test_docs_hidden_in_production() -> None:
    async with make_client(env="production") as client:
        assert (await client.get("/docs")).status_code == 404
    async with make_client() as client:
        assert (await client.get("/docs")).status_code == 200
