from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_user
from app.core.templating import templates
from app.db.session import get_session
from app.models import User
from app.services import tags as tag_service

router = APIRouter(tags=["pages"])


@router.get("/")
async def index() -> Response:
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: Annotated[User, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    tags = await tag_service.list_tags(db, user.id)
    return templates.TemplateResponse(request, "dashboard.html", {"user": user, "tags": tags})
