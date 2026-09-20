from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import aiosmtplib

from app.core.config import Settings


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, message: OutgoingEmail) -> None: ...


class SmtpEmailSender:
    """One implementation for dev (Mailpit) and prod: Resend, SES and Brevo all speak SMTP."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        starttls: bool,
        sender: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._starttls = starttls
        self._sender = sender

    async def send(self, message: OutgoingEmail) -> None:
        msg = EmailMessage()
        msg["From"] = self._sender
        msg["To"] = message.to  # EmailMessage rejects header injection (newlines)
        msg["Subject"] = message.subject
        msg.set_content(message.body)
        await aiosmtplib.send(
            msg,
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            start_tls=self._starttls,
            timeout=10,
        )


def build_email_sender(settings: Settings) -> EmailSender:
    password = settings.smtp_password.get_secret_value() if settings.smtp_password else None
    return SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=password,
        starttls=settings.smtp_starttls,
        sender=settings.email_from,
    )
