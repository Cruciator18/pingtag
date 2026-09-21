import base64
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tag, TagKind, TagStatus, User

MAX_ACTIVE_TAGS = 20
LABEL_MAX = 80
NOTE_MAX = 280


class TagValidationError(ValueError):
    """User-facing validation problem; the message is safe to display."""


class TagLimitError(Exception):
    """User-facing: the owner already has the maximum number of live tags."""


class InvalidTransitionError(Exception):
    """The requested change is not allowed from the tag's current state."""


@dataclass(frozen=True)
class TagInput:
    label: str
    kind: TagKind
    public_note: str | None


def generate_public_id() -> str:
    """80 random bits -> exactly 16 base32 characters (no padding), lowercase.

    Collision odds are ~2^-80 per tag; the UNIQUE constraint on tags.public_id is the backstop.
    """
    return base64.b32encode(secrets.token_bytes(10)).decode("ascii").lower()


def _clean_text(value: str, *, field: str, max_len: int, required: bool) -> str:
    cleaned = " ".join(value.split())  # trims and collapses whitespace, newlines and tabs
    if required and not cleaned:
        raise TagValidationError(f"{field} is required.")
    if len(cleaned) > max_len:
        raise TagValidationError(f"{field} must be at most {max_len} characters.")
    if not cleaned.isprintable():  # rejects control and zero-width characters
        raise TagValidationError(f"{field} contains unsupported characters.")
    return cleaned


def validate_tag_input(label: str, kind: str, public_note: str) -> TagInput:
    clean_label = _clean_text(label, field="Name", max_len=LABEL_MAX, required=True)
    try:
        parsed_kind = TagKind(kind)
    except ValueError:
        raise TagValidationError("Please choose a valid type.") from None
    note = _clean_text(public_note, field="Public note", max_len=NOTE_MAX, required=False)
    return TagInput(label=clean_label, kind=parsed_kind, public_note=note or None)


async def count_live_tags(db: AsyncSession, owner_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Tag)
        .where(Tag.owner_id == owner_id, Tag.status != TagStatus.REVOKED)
    )
    return int(await db.scalar(stmt) or 0)


async def create_tag(db: AsyncSession, owner: User, data: TagInput) -> Tag:
    if await count_live_tags(db, owner.id) >= MAX_ACTIVE_TAGS:
        raise TagLimitError(
            f"You've reached the limit of {MAX_ACTIVE_TAGS} active tags. "
            "Revoke one you no longer use."
        )
    tag = Tag(
        owner_id=owner.id,
        public_id=generate_public_id(),
        label=data.label,
        kind=data.kind,
        public_note=data.public_note,
    )
    db.add(tag)
    await db.commit()
    return tag


async def get_owned_tag(db: AsyncSession, owner_id: uuid.UUID, tag_id: uuid.UUID) -> Tag | None:
    """Returns None for a missing tag AND for someone else's tag (callers answer 404 for both)."""
    return await db.scalar(select(Tag).where(Tag.id == tag_id, Tag.owner_id == owner_id))


async def list_tags(db: AsyncSession, owner_id: uuid.UUID) -> list[Tag]:
    rows = await db.scalars(
        select(Tag).where(Tag.owner_id == owner_id).order_by(Tag.created_at.desc())
    )
    tags = list(rows)
    tags.sort(key=lambda t: t.status == TagStatus.REVOKED)  # stable: revoked tags last
    return tags


async def update_tag(db: AsyncSession, tag: Tag, data: TagInput) -> None:
    if tag.status == TagStatus.REVOKED:
        raise InvalidTransitionError("revoked tags cannot be edited")
    tag.label = data.label
    tag.kind = data.kind
    tag.public_note = data.public_note
    await db.commit()


_ALLOWED: dict[TagStatus, frozenset[TagStatus]] = {
    TagStatus.ACTIVE: frozenset({TagStatus.ACTIVE, TagStatus.PAUSED, TagStatus.REVOKED}),
    TagStatus.PAUSED: frozenset({TagStatus.ACTIVE, TagStatus.PAUSED, TagStatus.REVOKED}),
    TagStatus.REVOKED: frozenset(),  # terminal
}


async def change_status(db: AsyncSession, tag: Tag, new_status: TagStatus) -> None:
    if new_status not in _ALLOWED[tag.status]:
        raise InvalidTransitionError(f"{tag.status.value} -> {new_status.value}")
    if tag.status != new_status:
        tag.status = new_status
        await db.commit()


async def rotate_public_id(db: AsyncSession, tag: Tag) -> None:
    """New public_id; the old QR stops working immediately."""
    if tag.status == TagStatus.REVOKED:
        raise InvalidTransitionError("revoked tags cannot be rotated")
    tag.public_id = generate_public_id()
    await db.commit()
