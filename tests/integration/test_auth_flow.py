import pytest
from sqlalchemy import text

from tests.integration.helpers import Env, sign_in, token_from_email

pytestmark = pytest.mark.integration


async def _count_users(env: Env) -> int:
    async with env.app.state.engine.connect() as conn:
        return int(await conn.scalar(text("SELECT count(*) FROM users")) or 0)


async def test_magic_link_email_is_sent(env: Env) -> None:
    r = await env.client.post("/auth/magic-link", data={"email": "new@example.com"})
    assert r.status_code == 200
    assert len(env.email.sent) == 1
    assert env.email.sent[0].to == "new@example.com"
    assert "/auth/verify?token=" in env.email.sent[0].body


async def test_invalid_email_is_rejected_without_sending(env: Env) -> None:
    r = await env.client.post("/auth/magic-link", data={"email": "not-an-email"})
    assert r.status_code == 400
    assert env.email.sent == []


async def test_get_verify_does_not_consume_the_token(env: Env) -> None:
    """Email link scanners issue GET requests; that must not burn the link."""
    await env.client.post("/auth/magic-link", data={"email": "scan@example.com"})
    token = token_from_email(env.email.sent[-1])
    for _ in range(2):
        r = await env.client.get("/auth/verify", params={"token": token})
        assert r.status_code == 200
        assert "Sign in" in r.text
        assert r.headers["referrer-policy"] == "no-referrer"
    r = await env.client.post("/auth/verify", data={"token": token})
    assert r.status_code == 303


async def test_sign_in_sets_cookie_and_opens_dashboard(env: Env) -> None:
    r = await sign_in(env, "Alice@Example.com ")
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    dash = await env.client.get("/dashboard")
    assert dash.status_code == 200
    assert "alice@example.com" in dash.text


async def test_token_is_single_use(env: Env) -> None:
    await env.client.post("/auth/magic-link", data={"email": "once@example.com"})
    token = token_from_email(env.email.sent[-1])
    assert (await env.client.post("/auth/verify", data={"token": token})).status_code == 303
    env.client.cookies.clear()
    assert (await env.client.post("/auth/verify", data={"token": token})).status_code == 400


@pytest.mark.parametrize("token", ["garbage", "A" * 43])
async def test_invalid_tokens_are_rejected(env: Env, token: str) -> None:
    assert (await env.client.get("/auth/verify", params={"token": token})).status_code == 400
    assert (await env.client.post("/auth/verify", data={"token": token})).status_code == 400


async def test_dashboard_requires_login(env: Env) -> None:
    r = await env.client.get("/dashboard")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_logout_invalidates_the_session_server_side(env: Env) -> None:
    await sign_in(env)
    session_cookie = env.client.cookies["pt_session"]
    r = await env.client.post("/auth/logout")
    assert r.status_code == 303
    # Replaying the old cookie must fail: the session is gone in Redis, not just in the browser.
    env.client.cookies.set("pt_session", session_cookie)
    r = await env.client.get("/dashboard")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_email_is_case_insensitive(env: Env) -> None:
    await sign_in(env, "Bob@Example.com")
    env.client.cookies.clear()
    await sign_in(env, "bob@example.com")
    assert await _count_users(env) == 1


async def test_per_email_rate_limit(env: Env) -> None:
    codes = [
        (await env.client.post("/auth/magic-link", data={"email": "spam@example.com"})).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]
    assert len(env.email.sent) == 3


async def test_per_ip_rate_limit(env: Env) -> None:
    codes = [
        (
            await env.client.post("/auth/magic-link", data={"email": f"user{i}@example.com"})
        ).status_code
        for i in range(11)
    ]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


async def test_soft_deleted_user_cannot_sign_in_or_keep_a_session(env: Env) -> None:
    await sign_in(env, "gone@example.com")
    async with env.app.state.engine.begin() as conn:
        await conn.execute(text("UPDATE users SET deleted_at = now()"))
    r = await env.client.get("/dashboard")
    assert r.status_code == 303  # existing session no longer works
    env.client.cookies.clear()
    r = await sign_in(env, "gone@example.com")
    assert r.status_code == 400
