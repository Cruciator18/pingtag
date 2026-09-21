import logging
from typing import Protocol

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class CaptchaVerifier(Protocol):
    async def verify(self, token: str, remote_ip: str | None) -> bool: ...


class NoopCaptchaVerifier:
    """Used when Turnstile is not configured (local development only)."""

    async def verify(self, token: str, remote_ip: str | None) -> bool:
        return True


class TurnstileVerifier:
    def __init__(
        self,
        secret: str,
        *,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._secret = secret
        self._timeout = timeout
        self._transport = transport

    async def verify(self, token: str, remote_ip: str | None) -> bool:
        if not token or len(token) > 2048:
            return False
        payload = {"secret": self._secret, "response": token}
        if remote_ip:
            payload["remoteip"] = remote_ip
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.post(TURNSTILE_VERIFY_URL, data=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError):
            # Fail closed: if Cloudflare cannot be reached we do not let the request through.
            logger.warning("turnstile_verification_error", exc_info=True)
            return False
        return isinstance(data, dict) and data.get("success") is True


def build_captcha_verifier(settings: Settings) -> CaptchaVerifier:
    site_key = settings.turnstile_site_key
    secret = settings.turnstile_secret_key
    if site_key and secret:
        return TurnstileVerifier(secret.get_secret_value())
    if settings.env == "production":
        raise RuntimeError("TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY are required in production")
    return NoopCaptchaVerifier()
