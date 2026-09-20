from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.api.deps import require_user
from app.core.templating import templates
from app.models import User

router = APIRouter(tags=["pages"])


@router.get("/")
async def index() -> Response:
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: Annotated[User, Depends(require_user)]) -> Response:
    return templates.TemplateResponse(request, "dashboard.html", {"user": user})
