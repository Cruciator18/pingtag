from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.ratelimit import hit
from app.core.security import keyed_hash
from app.core.templating import templates
from app.db.session import get_session
from app.models import Tag
from app.services import scans as scan_service

router = APIRouter(tags=["finder"])
DbSession = Annotated[AsyncSession, Depends(get_session)]

PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}
VIEW_LIMIT, VIEW_WINDOW_S = 120, 60  # page views per IP per minute (blocks id guessing)

CAPTCHA_MESSAGE = "We could not verify you are human. Please try again."


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _ip_id(request: Request) -> str:
    settings: Settings = request.app.state.settings
    return keyed_hash(_client_ip(request), settings.ip_hash_salt.get_secret_value())


async def _view_allowed(request: Request) -> bool:
    return await hit(
        request.app.state.redis, f"rl:view:ip:{_ip_id(request)}", VIEW_LIMIT, VIEW_WINDOW_S
    )


def _page(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request, template, context or {}, status_code=status_code, headers=PRIVATE_HEADERS
    )


def _unavailable(
    request: Request, heading: str | None = None, message: str | None = None
) -> Response:
    return _page(request, "finder_unavailable.html", {"heading": heading, "message": message}, 404)


def _limited(request: Request) -> Response:
    return _page(request, "finder_limited.html", status_code=429)


def _finder_context(
    request: Request, tag: Tag, form: dict[str, str], error: str | None
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    return {
        "tag": tag,
        "reasons": scan_service.REASONS[tag.kind],
        "form": form,
        "error": error,
        "turnstile_site_key": settings.turnstile_site_key,
    }


async def _submit(
    request: Request,
    db: AsyncSession,
    public_id: str,
    *,
    reason: str,
    message: str,
    lat: float | None,
    lng: float | None,
    captcha_token: str,
) -> scan_service.CreatedScan:
    settings: Settings = request.app.state.settings
    return await scan_service.submit_scan(
        db,
        request.app.state.redis,
        request.app.state.captcha,
        salt=settings.ip_hash_salt.get_secret_value(),
        public_id=public_id,
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
        reason=reason,
        message=message,
        lat=lat,
        lng=lng,
        captcha_token=captcha_token,
    )


@router.get("/t/{public_id}", response_class=HTMLResponse)
async def finder_page(request: Request, public_id: str, db: DbSession) -> Response:
    if not await _view_allowed(request):
        return _limited(request)
    tag = await scan_service.get_scannable_tag(db, public_id)
    if tag is None:
        return _unavailable(request)
    form = {"reason": "", "message": "", "lat": "", "lng": ""}
    return _page(request, "finder.html", _finder_context(request, tag, form, None))


async def _redisplay(
    request: Request, db: AsyncSession, public_id: str, form: dict[str, str], error: str
) -> Response:
    tag = await scan_service.get_scannable_tag(db, public_id)
    if tag is None:
        return _unavailable(request)
    return _page(request, "finder.html", _finder_context(request, tag, form, error), 400)


@router.post("/t/{public_id}")
async def submit_form(
    request: Request,
    public_id: str,
    db: DbSession,
    reason: Annotated[str, Form()] = "",
    message: Annotated[str, Form()] = "",
    lat: Annotated[str, Form()] = "",
    lng: Annotated[str, Form()] = "",
    captcha_token: Annotated[str, Form(alias="cf-turnstile-response")] = "",
) -> Response:
    form = {"reason": reason, "message": message, "lat": lat, "lng": lng}
    try:
        result = await _submit(
            request,
            db,
            public_id,
            reason=reason,
            message=message,
            lat=scan_service.parse_optional_float(lat),
            lng=scan_service.parse_optional_float(lng),
            captcha_token=captcha_token,
        )
    except scan_service.TagUnavailableError:
        return _unavailable(request)
    except scan_service.RateLimitedError:
        return _limited(request)
    except scan_service.CaptchaFailedError:
        return await _redisplay(request, db, public_id, form, CAPTCHA_MESSAGE)
    except scan_service.ScanValidationError as exc:
        return await _redisplay(request, db, public_id, form, str(exc))
    return RedirectResponse(f"/s/{result.token}", status_code=303, headers=PRIVATE_HEADERS)


class ScanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(max_length=40)
    message: str = Field(default="", max_length=1000)
    lat: float | None = None
    lng: float | None = None
    turnstile_token: str = Field(default="", max_length=2048)


@router.post("/api/v1/t/{public_id}/scans", status_code=202)
async def create_scan(
    request: Request, public_id: str, body: ScanIn, db: DbSession
) -> JSONResponse:
    settings: Settings = request.app.state.settings
    try:
        result = await _submit(
            request,
            db,
            public_id,
            reason=body.reason,
            message=body.message,
            lat=body.lat,
            lng=body.lng,
            captcha_token=body.turnstile_token,
        )
    except scan_service.TagUnavailableError:
        return JSONResponse({"detail": "Tag not available"}, status_code=404)
    except scan_service.RateLimitedError:
        return JSONResponse({"detail": "Too many requests"}, status_code=429)
    except scan_service.CaptchaFailedError:
        return JSONResponse({"detail": "Captcha verification failed"}, status_code=400)
    except scan_service.ScanValidationError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    status_url = f"{settings.base_url.rstrip('/')}/s/{result.token}"
    return JSONResponse(
        {"scan_id": str(result.scan.id), "status": "queued", "status_url": status_url},
        status_code=202,
        headers=PRIVATE_HEADERS,
    )


@router.get("/s/{scan_token}", response_class=HTMLResponse)
async def scan_status(request: Request, scan_token: str, db: DbSession) -> Response:
    if not await _view_allowed(request):
        return _limited(request)
    scan = await scan_service.get_scan_by_token(db, scan_token)
    if scan is None:
        return _unavailable(request, "Link not found", "This link is invalid or has expired.")
    return _page(request, "finder_status.html", {"scan": scan})
