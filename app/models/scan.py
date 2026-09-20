import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, UUIDPk, str_enum


class ScanStatus(enum.StrEnum):
    QUEUED = "queued"
    NOTIFIED = "notified"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class DeliveryChannel(enum.StrEnum):
    WEBPUSH = "webpush"
    EMAIL = "email"
    TELEGRAM = "telegram"


class DeliveryStatus(enum.StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    DEAD = "dead"


class Scan(UUIDPk, CreatedAt, Base):
    __tablename__ = "scans"
    __table_args__ = (
        CheckConstraint("lat BETWEEN -90 AND 90", name="lat_range"),
        CheckConstraint("lng BETWEEN -180 AND 180", name="lng_range"),
        Index("ix_scans_tag_id_created_at", "tag_id", "created_at"),  # owner history
        Index("ix_scans_created_at", "created_at"),  # retention purge
    )

    tag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"))
    scan_token: Mapped[str] = mapped_column(String(64), unique=True)  # finder's private status link
    reason: Mapped[str] = mapped_column(String(40))
    message: Mapped[str | None] = mapped_column(String(280), default=None)
    # Numeric(6, 3) = 3 decimals (~100 m): the precision limit is enforced by the DB itself.
    lat: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), default=None)
    lng: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), default=None)
    ip_hash: Mapped[str] = mapped_column(String(64))
    ua_hash: Mapped[str] = mapped_column(String(64))
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    status: Mapped[ScanStatus] = mapped_column(str_enum(ScanStatus), default=ScanStatus.QUEUED)


class Delivery(UUIDPk, CreatedAt, Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        # Exactly one target: a push subscription (device) OR a notification channel.
        CheckConstraint(
            "(subscription_id IS NOT NULL) <> (channel_id IS NOT NULL)",
            name="exactly_one_target",
        ),
        # Idempotency: retries can never notify the same target twice for one scan.
        UniqueConstraint("scan_id", "subscription_id", name="uq_deliveries_scan_subscription"),
        UniqueConstraint("scan_id", "channel_id", name="uq_deliveries_scan_channel"),
    )

    scan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"))
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("push_subscriptions.id", ondelete="CASCADE"), default=None
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), default=None
    )
    channel: Mapped[DeliveryChannel] = mapped_column(str_enum(DeliveryChannel))
    status: Mapped[DeliveryStatus] = mapped_column(
        str_enum(DeliveryStatus), default=DeliveryStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Reply(UUIDPk, CreatedAt, Base):
    __tablename__ = "replies"

    scan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"),
        unique=True,  # one reply per scan
    )
    preset_key: Mapped[str] = mapped_column(String(32))
