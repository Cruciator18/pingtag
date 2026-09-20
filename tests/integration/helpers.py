import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI

from app.services.email import OutgoingEmail

LINK_RE = re.compile(r"https?://\S+")


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    async def send(self, message: OutgoingEmail) -> None:
        self.sent.append(message)


@dataclass
class Env:
    client: httpx.AsyncClient
    email: FakeEmailSender
    app: FastAPI


def token_from_email(message: OutgoingEmail) -> str:
    match = LINK_RE.search(message.body)
    assert match is not None, "no link in email body"
    return parse_qs(urlparse(match.group(0)).query)["token"][0]


async def sign_in(env: Env, email: str = "owner@example.com") -> httpx.Response:
    """Run the whole magic-link flow. Afterwards the client holds a session cookie."""
    await env.client.post("/auth/magic-link", data={"email": email})
    token = token_from_email(env.email.sent[-1])
    return await env.client.post("/auth/verify", data={"token": token})
