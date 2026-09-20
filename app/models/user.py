import enum
import uuid
from datetime import datetime, time

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, UUIDPk, str_enum


class ChannelType(enum.StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"


class User(UUIDPk, CreatedAt, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    email: Mapped[str] = mapped_column(String(320), unique=True)  # always stored lowercased
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    quiet_hours_start: Mapped[time | None] = mapped_column(Time, default=None)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class PushSubscription(UUIDPk, CreatedAt, Base):
    __tablename__ = "push_subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class NotificationChannel(UUIDPk, CreatedAt, Base):
    __tablename__ = "notification_channels"
    __table_args__ = (UniqueConstraint("user_id", "type", "address"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[ChannelType] = mapped_column(str_enum(ChannelType))
    address: Mapped[str] = mapped_column(String(320))  # email address or telegram chat id
    priority: Mapped[int] = mapped_column(default=100, server_default="100")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
