import uuid

from pydantic import BaseModel, EmailStr, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


class _EmailIn(BaseModel):
    email: EmailStr


def parse_email(raw: str) -> str | None:
    """Validate and normalise (trim + lowercase). Returns None if invalid."""
    raw = raw.strip()
    if not raw or len(raw) > 320:
        return None
    try:
        return _EmailIn(email=raw).email.lower()
    except ValidationError:
        return None


async def get_or_create_user(db: AsyncSession, email: str) -> User:
    # ON CONFLICT DO NOTHING keeps this safe if two sign-ins race.
    await db.execute(
        pg_insert(User).values(email=email).on_conflict_do_nothing(index_elements=["email"])
    )
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    await db.commit()
    return user


async def get_active_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
