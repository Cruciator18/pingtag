import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_user
from app.core.config import Settings
from app.core.templating import templates
from app.db.session import get_session
from app.models import TagKind, TagStatus, User
from app.services import qr as qr_service
from app.services import tags as tag_service

router = APIRouter(tags=["tags"])

CurrentUser = Annotated[User, Depends(require_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]

KIND_CHOICES = [(k.value, k.value.capitalize()) for k in TagKind]


def _error_page(request: Request, message: str, status_code: int) -> Response:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"message": message, "back_url": "/dashboard", "back_label": "Back to your tags"},
        status_code=status_code,
    )


def _not_found(request: Request) -> Response:
    return _error_page(request, "Tag not found.", 404)


def _form_page(
    request: Request,
    *,
    mode: str,
    form: dict[str, str],
    error: str | None,
    action: str,
    cancel_url: str,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "tag_form.html",
        {
            "mode": mode,
            "form": form,
            "error": error,
            "action": action,
            "cancel_url": cancel_url,
            "kinds": KIND_CHOICES,
        },
        status_code=status_code,
    )


@router.get("/tags/new", response_class=HTMLResponse)
async def new_tag_page(request: Request, user: CurrentUser) -> Response:
    return _form_page(
        request,
        mode="new",
        form={"label": "", "kind": "car", "public_note": ""},
        error=None,
        action="/tags",
        cancel_url="/dashboard",
    )


@router.post("/tags")
async def create_tag(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    label: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    public_note: Annotated[str, Form()] = "",
) -> Response:
    try:
        data = tag_service.validate_tag_input(label, kind, public_note)
        tag = await tag_service.create_tag(db, user, data)
    except (tag_service.TagValidationError, tag_service.TagLimitError) as exc:
        return _form_page(
            request,
            mode="new",
            form={"label": label, "kind": kind, "public_note": public_note},
            error=str(exc),
            action="/tags",
            cancel_url="/dashboard",
            status_code=400,
        )
    return RedirectResponse(f"/tags/{tag.id}", status_code=303)


@router.get("/tags/{tag_id}", response_class=HTMLResponse)
async def tag_detail(
    request: Request, user: CurrentUser, db: DbSession, tag_id: uuid.UUID
) -> Response:
    tag = await tag_service.get_owned_tag(db, user.id, tag_id)
    if tag is None:
        return _not_found(request)
    settings: Settings = request.app.state.settings
    scan_url = qr_service.build_scan_url(settings.base_url, tag.public_id)
    return templates.TemplateResponse(
        request, "tag_detail.html", {"tag": tag, "scan_url": scan_url}
    )


@router.get("/tags/{tag_id}/edit", response_class=HTMLResponse)
async def edit_tag_page(
    request: Request, user: CurrentUser, db: DbSession, tag_id: uuid.UUID
) -> Response:
    tag = await tag_service.get_owned_tag(db, user.id, tag_id)
    if tag is None:
        return _not_found(request)
    if tag.status == TagStatus.REVOKED:
        return _error_page(request, "This tag is revoked, so it can't be edited.", 409)
    return _form_page(
        request,
        mode="edit",
        form={"label": tag.label, "kind": tag.kind.value, "public_note": tag.public_note or ""},
        error=None,
        action=f"/tags/{tag.id}/edit",
        cancel_url=f"/tags/{tag.id}",
    )


@router.post("/tags/{tag_id}/edit")
async def edit_tag(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    tag_id: uuid.UUID,
    label: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    public_note: Annotated[str, Form()] = "",
) -> Response:
    tag = await tag_service.get_owned_tag(db, user.id, tag_id)
    if tag is None:
        return _not_found(request)
    try:
        data = tag_service.validate_tag_input(label, kind, public_note)
        await tag_service.update_tag(db, tag, data)
    except tag_service.TagValidationError as exc:
        return _form_page(
            request,
            mode="edit",
            form={"label": label, "kind": kind, "public_note": public_note},
            error=str(exc),
            action=f"/tags/{tag.id}/edit",
            cancel_url=f"/tags/{tag.id}",
            status_code=400,
        )
    except tag_service.InvalidTransitionError:
        return _error_page(request, "This tag is revoked, so it can't be edited.", 409)
    return RedirectResponse(f"/tags/{tag.id}", status_code=303)


async def _change_status(
    request: Request, db: AsyncSession, user: User, tag_id: uuid.UUID, new_status: TagStatus
) -> Response:
    tag = await tag_service.get_owned_tag(db, user.id, tag_id)
    if tag is None:
        return _not_found(request)
    try:
        await tag_service.change_status(db, tag, new_status)
    except tag_service.InvalidTransitionError:
        return _error_page(request, "This tag is revoked, so it can't be changed.", 409)
    return RedirectResponse(f"/tags/{tag.id}", status_code=303)


@router.post("/tags/{tag_id}/pause")
async def pause_tag(
    request: Request, user: CurrentUser, db: DbSession, tag_id: uuid.UUID
) -> Response:
    return await _change_status(request, db, user, tag_id, TagStatus.PAUSED)


@router.post("/tags/{tag_id}/resume")
async def resume_tag(
    request: Request, user: CurrentUser, db: DbSession, tag_id: uuid.UUID
) -> Response:
    return await _change_status(request, db, user, tag_id, TagStatus.ACTIVE)


@router.post("/tags/{tag_id}/revoke")
async def revoke_tag(
    request: Request, user: CurrentUser, db: DbSession, tag_id: uuid.UUID
) -> Response:
    return await _change_status(request, db, user, tag_id, TagStatus.REVOKED)


@router.post("/tags/{tag_id}/rotate")
async def rotate_tag(
    request: Request, user: CurrentUser, db: DbSession, tag_id: uuid.UUID
) -> Response:
    tag = await tag_service.get_owned_tag(db, user.id, tag_id)
    if tag is None:
        return _not_found(request)
    try:
        await tag_service.rotate_public_id(db, tag)
    except tag_service.InvalidTransitionError:
        return _error_page(request, "This tag is revoked, so it can't be changed.", 409)
    return RedirectResponse(f"/tags/{tag.id}", status_code=303)


async def _qr_response(
    request: Request,
    db: AsyncSession,
    user: User,
    tag_id: uuid.UUID,
    ext: str,
    download: bool,
) -> Response:
    tag = await tag_service.get_owned_tag(db, user.id, tag_id)
    if tag is None or tag.status == TagStatus.REVOKED:
        return Response(status_code=404)
    settings: Settings = request.app.state.settings
    url = qr_service.build_scan_url(settings.base_url, tag.public_id)
    if ext == "svg":
        body, media_type = qr_service.render_svg(url), "image/svg+xml"
    else:
        body, media_type = qr_service.render_png(url), "image/png"
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if download:
        name = qr_service.download_name(tag.label, ext)
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return Response(body, media_type=media_type, headers=headers)


@router.get("/tags/{tag_id}/qr.svg")
async def qr_svg(
    request: Request, user: CurrentUser, db: DbSession, tag_id: uuid.UUID, download: bool = False
) -> Response:
    return await _qr_response(request, db, user, tag_id, "svg", download)


@router.get("/tags/{tag_id}/qr.png")
async def qr_png(
    request: Request, user: CurrentUser, db: DbSession, tag_id: uuid.UUID, download: bool = False
) -> Response:
    return await _qr_response(request, db, user, tag_id, "png", download)


@router.get("/tags/{tag_id}/print", response_class=HTMLResponse)
async def print_sheet(
    request: Request, user: CurrentUser, db: DbSession, tag_id: uuid.UUID
) -> Response:
    tag = await tag_service.get_owned_tag(db, user.id, tag_id)
    if tag is None or tag.status == TagStatus.REVOKED:
        return _not_found(request)
    return templates.TemplateResponse(request, "tag_print.html", {"tag": tag})
