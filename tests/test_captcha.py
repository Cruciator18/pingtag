import httpx
import pytest

from app.core.config import Settings
from app.services.captcha import (
    NoopCaptchaVerifier,
    TurnstileVerifier,
    build_captcha_verifier,
)


def make_settings(**kw: object) -> Settings:
    return Settings(_env_file=None, secret_key="s" * 32, ip_hash_salt="h" * 16, **kw)  # type: ignore[arg-type]


def make_verifier(handler: httpx.MockTransport | object) -> TurnstileVerifier:
    return TurnstileVerifier("topsecret", transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_success_sends_secret_token_and_ip() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"success": True})

    assert await make_verifier(handler).verify("tok", "203.0.113.9") is True
    assert "secret=topsecret" in seen["body"]
    assert "response=tok" in seen["body"]
    assert "remoteip=203.0.113.9" in seen["body"]


async def test_rejected_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"success": False, "error-codes": ["invalid-input-response"]}
        )

    assert await make_verifier(handler).verify("tok", None) is False


@pytest.mark.parametrize("bad", ["server-error", "connect-error", "not-json"])
async def test_failures_fail_closed(bad: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if bad == "server-error":
            return httpx.Response(500)
        if bad == "connect-error":
            raise httpx.ConnectError("boom")
        return httpx.Response(200, text="oops")

    assert await make_verifier(handler).verify("tok", None) is False


async def test_empty_token_never_calls_cloudflare() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    assert await make_verifier(handler).verify("", None) is False


async def test_noop_verifier_accepts_everything() -> None:
    assert await NoopCaptchaVerifier().verify("", None) is True


def test_build_verifier() -> None:
    assert isinstance(build_captcha_verifier(make_settings()), NoopCaptchaVerifier)
    keyed = make_settings(turnstile_site_key="site", turnstile_secret_key="secret")
    assert isinstance(build_captcha_verifier(keyed), TurnstileVerifier)
    with pytest.raises(RuntimeError):
        build_captcha_verifier(make_settings(env="production"))
