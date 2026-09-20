import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, UUIDPk, str_enum


class TagKind(enum.StrEnum):
    CAR = "car"
    PET = "pet"
    OTHER = "other"


class TagStatus(enum.StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"


class Tag(UUIDPk, CreatedAt, Base):
    __tablename__ = "tags"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    public_id: Mapped[str] = mapped_column(String(32), unique=True)  # the opaque QR id
    label: Mapped[str] = mapped_column(String(80))
    kind: Mapped[TagKind] = mapped_column(str_enum(TagKind), default=TagKind.OTHER)
    public_note: Mapped[str | None] = mapped_column(String(280), default=None)
    photo_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    status: Mapped[TagStatus] = mapped_column(str_enum(TagStatus), default=TagStatus.ACTIVE)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Block(UUIDPk, CreatedAt, Base):
    """An owner-blocked finder (by salted IP hash) for one tag."""

    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("tag_id", "ip_hash"),)

    tag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)
    ip_hash: Mapped[str] = mapped_column(String(64))
