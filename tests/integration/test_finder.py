import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.core.security import keyed_hash, sha256_hex
from tests.integration.helpers import Env, sign_in

pytestmark = pytest.mark.integration

SALT = "h" * 16  # matches tests/integration/conftest.py


class FakeCaptcha:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.tokens: list[str] = []

    async def verify(self, token: str, remote_ip: str | None) -> bool:
        self.tokens.append(token)
        return self.ok


def client_for(app: FastAPI, ip: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 5000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def owner(env: Env) -> Env:
    await sign_in(env, "owner@example.com")
    return env


@pytest.fixture
async def anon(env: Env) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(env.app, "127.0.0.1") as client:
        yield client


async def make_tag(
    env: Env, kind: str = "car", label: str = "Red Swift", note: str = ""
) -> tuple[str, str]:
    r = await env.client.post("/tags", data={"label": label, "kind": kind, "public_note": note})
    assert r.status_code == 303, r.text
    tag_id = r.headers["location"].rsplit("/", 1)[1]
    async with env.app.state.engine.connect() as conn:
        pid = await conn.scalar(
            text("SELECT public_id FROM tags WHERE id = :i"), {"i": uuid.UUID(tag_id)}
        )
    return tag_id, str(pid)


def form(**overrides: str) -> dict[str, str]:
    data = {"reason": "blocking", "message": "", "lat": "", "lng": ""}
    data.update(overrides)
    return data


async def scan_rows(env: Env) -> list[dict[str, Any]]:
    async with env.app.state.engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT reason, message, lat, lng, ip_hash, ua_hash, scan_token, status FROM scans"
            )
        )
        return [dict(row) for row in result.mappings().all()]


async def test_finder_page_renders_without_login_or_owner_details(
    owner: Env, anon: httpx.AsyncClient
) -> None:
    _, pid = await make_tag(owner, "car", "Red Swift", "Ring the bell")
    r = await anon.get(f"/t/{pid}")
    assert r.status_code == 200
    assert "Red Swift" in r.text
    assert "Ring the bell" in r.text
    assert 'value="blocking"' in r.text
    assert f'action="/t/{pid}"' in r.text
    assert "owner@example.com" not in r.text
    assert "challenges.cloudflare.com" not in r.text
    assert "set-cookie" not in r.headers
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in r.headers["x-robots-tag"]
    assert len(r.content) < 100_000


async def test_reasons_depend_on_tag_kind(owner: Env, anon: httpx.AsyncClient) -> None:
    _, pet = await make_tag(owner, "pet", "Bruno")
    _, car = await make_tag(owner, "car", "Swift")
    assert 'value="injured"' in (await anon.get(f"/t/{pet}")).text
    assert 'value="injured"' not in (await anon.get(f"/t/{car}")).text


async def test_turnstile_widget_only_when_configured(owner: Env, anon: httpx.AsyncClient) -> None:
    _, pid = await make_tag(owner)
    owner.app.state.settings.turnstile_site_key = "site123"
    page = await anon.get(f"/t/{pid}")
    assert 'data-sitekey="site123"' in page.text
    assert "challenges.cloudflare.com" in page.text


async def test_unknown_paused_and_revoked_tags_look_identical(
    owner: Env, anon: httpx.AsyncClient
) -> None:
    tag_id, pid = await make_tag(owner)
    unknown = await anon.get("/t/abcdefghijklmnop")
    malformed = await anon.get("/t/nope")
    await owner.client.post(f"/tags/{tag_id}/pause")
    paused = await anon.get(f"/t/{pid}")
    await owner.client.post(f"/tags/{tag_id}/revoke")
    revoked = await anon.get(f"/t/{pid}")
    responses = (unknown, malformed, paused, revoked)
    assert {r.status_code for r in responses} == {404}
    assert len({r.text for r in responses}) == 1


async def test_submit_form_stores_hashed_and_rounded_data(
    owner: Env, anon: httpx.AsyncClient
) -> None:
    _, pid = await make_tag(owner)
    r = await anon.post(
        f"/t/{pid}",
        data=form(message="  Please move   it ", lat="12.34567", lng="-77.98765"),
        headers={"user-agent": "TestBrowser/1.0"},
    )
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/s/")
    assert r.headers["cache-control"] == "no-store"

    [row] = await scan_rows(owner)
    assert row["reason"] == "blocking"
    assert row["message"] == "Please move it"
    assert str(row["lat"]) == "12.346"
    assert str(row["lng"]) == "-77.988"
    assert row["status"] == "queued"
    assert row["ip_hash"] == keyed_hash("127.0.0.1", SALT)
    assert row["ip_hash"] != "127.0.0.1"
    assert row["ua_hash"] == keyed_hash("TestBrowser/1.0", SALT)
    token = location.rsplit("/", 1)[1]
    assert row["scan_token"] == sha256_hex(token)
    assert row["scan_token"] != token

    status = await anon.get(location)
    assert status.status_code == 200
    assert "being sent" in status.text
    assert 'http-equiv="refresh"' in status.text
    assert status.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    "bad",
    [
        {"reason": ""},
        {"reason": "injured"},
        {"message": "x" * 281},
        {"message": "bad\x00msg"},
        {"lat": "12.3"},
        {"lng": "12.3"},
        {"lat": "91", "lng": "10"},
        {"lat": "10", "lng": "181"},
        {"lat": "abc", "lng": "10"},
        {"lat": "nan", "lng": "10"},
        {"lat": "inf", "lng": "10"},
    ],
)
async def test_invalid_form_is_rejected_and_nothing_is_stored(
    owner: Env, anon: httpx.AsyncClient, bad: dict[str, str]
) -> None:
    _, pid = await make_tag(owner)
    r = await anon.post(f"/t/{pid}", data=form(**{"message": "keep me", **bad}))
    assert r.status_code == 400
    assert 'role="alert"' in r.text
    assert await scan_rows(owner) == []


async def test_form_keeps_typed_message_after_an_error(owner: Env, anon: httpx.AsyncClient) -> None:
    _, pid = await make_tag(owner)
    r = await anon.post(f"/t/{pid}", data=form(reason="", message="please hurry"))
    assert r.status_code == 400
    assert "please hurry" in r.text


async def test_json_api_creates_a_scan(owner: Env, anon: httpx.AsyncClient) -> None:
    _, pid = await make_tag(owner)
    r = await anon.post(f"/api/v1/t/{pid}/scans", json={"reason": "lights", "message": "Lights on"})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["status_url"].startswith("http://test/s/")
    assert uuid.UUID(body["scan_id"])
    [row] = await scan_rows(owner)
    assert row["reason"] == "lights"


async def test_json_api_errors(owner: Env, anon: httpx.AsyncClient) -> None:
    _, pid = await make_tag(owner)
    bad_reason = await anon.post(f"/api/v1/t/{pid}/scans", json={"reason": "nope"})
    assert bad_reason.status_code == 400
    unknown = await anon.post("/api/v1/t/abcdefghijklmnop/scans", json={"reason": "blocking"})
    assert unknown.status_code == 404
    extra = await anon.post(f"/api/v1/t/{pid}/scans", json={"reason": "blocking", "x": 1})
    assert extra.status_code == 422
    assert await scan_rows(owner) == []


async def test_paused_tag_rejects_scans(owner: Env, anon: httpx.AsyncClient) -> None:
    tag_id, pid = await make_tag(owner)
    await owner.client.post(f"/tags/{tag_id}/pause")
    assert (await anon.post(f"/t/{pid}", data=form())).status_code == 404
    assert (
        await anon.post(f"/api/v1/t/{pid}/scans", json={"reason": "blocking"})
    ).status_code == 404
    assert await scan_rows(owner) == []


async def test_captcha_failure_blocks_the_scan(owner: Env, anon: httpx.AsyncClient) -> None:
    _, pid = await make_tag(owner)
    fake = FakeCaptcha(ok=False)
    owner.app.state.captcha = fake
    r = await anon.post(f"/t/{pid}", data={**form(), "cf-turnstile-response": "tok123"})
    assert r.status_code == 400
    assert "verify" in r.text
    assert fake.tokens == ["tok123"]
    assert await scan_rows(owner) == []


async def test_per_ip_per_tag_limit(owner: Env, anon: httpx.AsyncClient) -> None:
    _, pid = await make_tag(owner)
    codes = [(await anon.post(f"/t/{pid}", data=form())).status_code for _ in range(4)]
    assert codes == [303, 303, 303, 429]
    assert len(await scan_rows(owner)) == 3
    api = await anon.post(f"/api/v1/t/{pid}/scans", json={"reason": "blocking"})
    assert api.status_code == 429


async def test_per_tag_daily_cap(owner: Env) -> None:
    _, pid = await make_tag(owner)
    codes = []
    for i in range(21):
        async with client_for(owner.app, f"203.0.113.{i + 1}") as client:
            codes.append((await client.post(f"/t/{pid}", data=form())).status_code)
    assert codes == [303] * 20 + [429]
    assert len(await scan_rows(owner)) == 20


async def test_failed_captchas_do_not_use_up_the_tag_cap(owner: Env) -> None:
    _, pid = await make_tag(owner)
    owner.app.state.captcha = FakeCaptcha(ok=False)
    for i in range(25):
        async with client_for(owner.app, f"198.51.100.{i + 1}") as client:
            assert (await client.post(f"/t/{pid}", data=form())).status_code == 400
    owner.app.state.captcha = FakeCaptcha(ok=True)
    async with client_for(owner.app, "192.0.2.50") as client:
        assert (await client.post(f"/t/{pid}", data=form())).status_code == 303


async def test_global_per_ip_limit_also_covers_unknown_tags(
    owner: Env, anon: httpx.AsyncClient
) -> None:
    codes = [
        (
            await anon.post("/api/v1/t/abcdefghijklmnop/scans", json={"reason": "blocking"})
        ).status_code
        for _ in range(31)
    ]
    assert codes == [404] * 30 + [429]


async def test_page_views_are_rate_limited(env: Env, anon: httpx.AsyncClient) -> None:
    codes = [(await anon.get("/t/nope")).status_code for _ in range(121)]
    assert codes[:120] == [404] * 120
    assert codes[120] == 429


async def test_status_page_edge_cases(owner: Env, anon: httpx.AsyncClient) -> None:
    _, pid = await make_tag(owner)
    r = await anon.post(f"/t/{pid}", data=form())
    location = r.headers["location"]

    for path in ("/s/short", "/s/" + "A" * 43):
        missing = await anon.get(path)
        assert missing.status_code == 404
        assert "invalid or has expired" in missing.text

    async with owner.app.state.engine.begin() as conn:
        await conn.execute(text("UPDATE scans SET created_at = now() - interval '8 days'"))
    expired = await anon.get(location)
    assert expired.status_code == 404
    assert "invalid or has expired" in expired.text
