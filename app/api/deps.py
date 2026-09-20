from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import User
from app.services.auth import session_user_id
from app.services.users import get_active_user


class NotAuthenticatedError(Exception):
    """Handled in main.py: redirect to /login for pages, 401 JSON for /api/*."""


async def get_optional_user(
    request: Request, db: Annotated[AsyncSession, Depends(get_session)]
) -> User | None:
    settings = request.app.state.settings
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        return None
    user_id = await session_user_id(request.app.state.redis, session_id)
    if user_id is None:
        return None
    return await get_active_user(db, user_id)


async def require_user(user: Annotated[User | None, Depends(get_optional_user)]) -> User:
    if user is None:
        raise NotAuthenticatedError
    return user
