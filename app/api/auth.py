import logging
import re
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_user
from app.core.config import Settings
from app.core.ratelimit import hit
from app.core.security import keyed_hash, sha256_hex
from app.core.templating import templates
from app.db.session import get_session
from app.models import User
from app.services.auth import (
    LOGIN_TOKEN_TTL_S,
    SESSION_TTL_S,
    consume_login_token,
    create_session,
    delete_session,
    issue_login_token,
    login_token_is_valid,
)
from app.services.email import EmailSender, OutgoingEmail
from app.services.users import get_or_create_user, parse_email

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

IP_LIMIT, IP_WINDOW_S = 10, 60 * 60  # 10 link requests per IP per hour
EMAIL_LIMIT, EMAIL_WINDOW_S = 3, 15 * 60  # 3 per address per 15 minutes

LOGIN_EMAIL_BODY = """Hi,

Use this link to sign in to PingTag. It works once and expires in {minutes} minutes:

{link}

If you didn't ask for this, you can ignore this email.
"""


def _private(response: Response) -> Response:
    """Pages carrying a token must not be cached or leak it through the Referer header."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _invalid_link(request: Request) -> Response:
    return _private(
        templates.TemplateResponse(
            request,
            "error.html",
            {"message": "This sign-in link is invalid or has expired."},
            status_code=400,
        )
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _send_login_email(sender: EmailSender, to: str, link: str) -> None:
    try:
        await sender.send(
            OutgoingEmail(
                to=to,
                subject="Your PingTag sign-in link",
                body=LOGIN_EMAIL_BODY.format(link=link, minutes=LOGIN_TOKEN_TTL_S // 60),
            )
        )
    except Exception:
        # Never log the address or link.
        logger.warning("login_email_failed", exc_info=True)


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request, user: Annotated[User | None, Depends(get_optional_user)]
) -> Response:
    if user is not None:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None, "email": ""})


@router.post("/auth/magic-link", response_class=HTMLResponse)
async def request_magic_link(
    request: Request, background: BackgroundTasks, email: Annotated[str, Form()]
) -> Response:
    settings: Settings = request.app.state.settings
    redis = request.app.state.redis

    parsed = parse_email(email)
    if parsed is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Please enter a valid email address.", "email": email},
            status_code=400,
        )

    ip_id = keyed_hash(_client_ip(request), settings.ip_hash_salt.get_secret_value())
    allowed = await hit(redis, f"rl:magic:ip:{ip_id}", IP_LIMIT, IP_WINDOW_S) and await hit(
        redis, f"rl:magic:email:{sha256_hex(parsed)}", EMAIL_LIMIT, EMAIL_WINDOW_S
    )
    if not allowed:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Too many requests. Please try again in a few minutes.", "email": parsed},
            status_code=429,
        )

    # No user lookup here, so the response is identical for known and unknown addresses.
    token = await issue_login_token(redis, parsed)
    link = f"{settings.base_url.rstrip('/')}/auth/verify?token={token}"
    background.add_task(_send_login_email, request.app.state.email_sender, parsed, link)
    return templates.TemplateResponse(request, "check_email.html", {"email": parsed})


@router.get("/auth/verify", response_class=HTMLResponse)
async def verify_page(request: Request, token: str = "") -> Response:
    """Confirm page. GET never consumes the token, so email link scanners are harmless."""
    if not TOKEN_RE.match(token) or not await login_token_is_valid(request.app.state.redis, token):
        return _invalid_link(request)
    return _private(templates.TemplateResponse(request, "verify.html", {"token": token}))


@router.post("/auth/verify")
async def verify(
    request: Request,
    token: Annotated[str, Form()],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    settings: Settings = request.app.state.settings
    redis = request.app.state.redis

    if not TOKEN_RE.match(token):
        return _invalid_link(request)
    email = await consume_login_token(redis, token)
    if email is None:
        return _invalid_link(request)

    user = await get_or_create_user(db, email)
    if user.deleted_at is not None:
        return _invalid_link(request)

    session_id = await create_session(redis, user.id)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        settings.session_cookie_name,
        session_id,
        max_age=SESSION_TTL_S,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    return _private(response)


@router.post("/auth/logout")
async def logout(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    session_id = request.cookies.get(settings.session_cookie_name)
    if session_id:
        await delete_session(request.app.state.redis, session_id)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response
