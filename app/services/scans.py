import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import hit
from app.core.redis import RedisClient
from app.core.security import keyed_hash, new_token, sha256_hex
from app.models import Scan, Tag, TagKind, TagStatus
from app.services.captcha import CaptchaVerifier

PUBLIC_ID_RE = re.compile(r"^[a-z2-7]{16}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

MESSAGE_MAX = 280
STATUS_LINK_TTL = timedelta(days=7)

IP_LIMIT, IP_WINDOW_S = 30, 60 * 60  # scan attempts per IP per hour, across all tags
IP_TAG_LIMIT, IP_TAG_WINDOW_S = 3, 10 * 60  # per IP per tag
TAG_LIMIT, TAG_WINDOW_S = 20, 24 * 60 * 60  # per tag per day, all finders together


class ReasonOption(NamedTuple):
    key: str
    label: str


REASONS: dict[TagKind, tuple[ReasonOption, ...]] = {
    TagKind.CAR: (
        ReasonOption("blocking", "Your car is blocking me"),
        ReasonOption("lights", "Lights are on"),
        ReasonOption("alarm", "The alarm is going off"),
        ReasonOption("damage", "There is damage or a problem"),
        ReasonOption("other", "Something else"),
    ),
    TagKind.PET: (
        ReasonOption("found", "I found your pet"),
        ReasonOption("injured", "Your pet looks injured"),
        ReasonOption("roaming", "Your pet is roaming alone"),
        ReasonOption("other", "Something else"),
    ),
    TagKind.OTHER: (
        ReasonOption("found", "I found this"),
        ReasonOption("other", "Something else"),
    ),
}


class ScanValidationError(ValueError):
    """User-facing validation problem; the message is safe to display."""


class TagUnavailableError(Exception):
    """Unknown, malformed, paused or revoked tag. Callers answer with one uniform 404."""


class RateLimitedError(Exception):
    pass


class CaptchaFailedError(Exception):
    pass


@dataclass(frozen=True)
class ScanInput:
    reason: str
    message: str | None
    lat: Decimal | None
    lng: Decimal | None


@dataclass(frozen=True)
class CreatedScan:
    scan: Scan
    token: str  # the raw status-link token; only its hash is stored


_COORD_QUANT = Decimal("0.001")  # 3 decimals ~ 100 m


def _to_decimal(value: float) -> Decimal:
    return Decimal(repr(value)).quantize(_COORD_QUANT, rounding=ROUND_HALF_EVEN)


def parse_optional_float(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ScanValidationError("Location is invalid.") from None


def validate_scan_input(
    kind: TagKind, reason: str, message: str, lat: float | None, lng: float | None
) -> ScanInput:
    if reason not in {r.key for r in REASONS[kind]}:
        raise ScanValidationError("Please choose what is happening.")

    clean = " ".join(message.split())
    if len(clean) > MESSAGE_MAX:
        raise ScanValidationError(f"Message must be at most {MESSAGE_MAX} characters.")
    if not clean.isprintable():
        raise ScanValidationError("Message contains unsupported characters.")

    if (lat is None) != (lng is None):
        raise ScanValidationError("Location is invalid.")
    lat_d: Decimal | None = None
    lng_d: Decimal | None = None
    if lat is not None and lng is not None:
        in_range = -90 <= lat <= 90 and -180 <= lng <= 180
        if not (math.isfinite(lat) and math.isfinite(lng) and in_range):
            raise ScanValidationError("Location is invalid.")
        lat_d, lng_d = _to_decimal(lat), _to_decimal(lng)

    return ScanInput(reason=reason, message=clean or None, lat=lat_d, lng=lng_d)


async def get_scannable_tag(db: AsyncSession, public_id: str) -> Tag | None:
    """Only active tags can be scanned. Paused and revoked look the same as unknown."""
    if not PUBLIC_ID_RE.match(public_id):
        return None
    return await db.scalar(
        select(Tag).where(Tag.public_id == public_id, Tag.status == TagStatus.ACTIVE)
    )


async def get_scan_by_token(db: AsyncSession, token: str) -> Scan | None:
    if not TOKEN_RE.match(token):
        return None
    scan = await db.scalar(select(Scan).where(Scan.scan_token == sha256_hex(token)))
    if scan is None or datetime.now(UTC) - scan.created_at > STATUS_LINK_TTL:
        return None
    return scan


async def submit_scan(
    db: AsyncSession,
    redis: RedisClient,
    captcha: CaptchaVerifier,
    *,
    salt: str,
    public_id: str,
    client_ip: str,
    user_agent: str,
    reason: str,
    message: str,
    lat: float | None,
    lng: float | None,
    captcha_token: str,
) -> CreatedScan:
    ip_id = keyed_hash(client_ip, salt)

    # Order matters: cheap checks first, and the per-tag daily cap only AFTER the captcha, so
    # bots that fail the captcha cannot use up a tag's budget and lock the real owner out.
    if not await hit(redis, f"rl:scan:ip:{ip_id}", IP_LIMIT, IP_WINDOW_S):
        raise RateLimitedError

    tag = await get_scannable_tag(db, public_id)
    if tag is None:
        raise TagUnavailableError

    data = validate_scan_input(tag.kind, reason, message, lat, lng)

    if not await hit(redis, f"rl:scan:iptag:{ip_id}:{tag.id}", IP_TAG_LIMIT, IP_TAG_WINDOW_S):
        raise RateLimitedError
    if not await captcha.verify(captcha_token, client_ip):
        raise CaptchaFailedError
    if not await hit(redis, f"rl:scan:tag:{tag.id}", TAG_LIMIT, TAG_WINDOW_S):
        raise RateLimitedError

    token = new_token()
    scan = Scan(
        tag_id=tag.id,
        scan_token=sha256_hex(token),  # the column stores the hash, never the raw token
        reason=data.reason,
        message=data.message,
        lat=data.lat,
        lng=data.lng,
        ip_hash=ip_id,
        ua_hash=keyed_hash(user_agent[:512], salt),
    )
    db.add(scan)
    await db.commit()
    return CreatedScan(scan=scan, token=token)
