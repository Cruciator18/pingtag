import uuid

from app.core.redis import RedisClient
from app.core.security import new_token, sha256_hex

LOGIN_TOKEN_TTL_S = 15 * 60
SESSION_TTL_S = 30 * 24 * 60 * 60


def _login_key(token: str) -> str:
    return f"login:{sha256_hex(token)}"  # only the hash is stored


def _session_key(session_id: str) -> str:
    return f"session:{sha256_hex(session_id)}"


async def issue_login_token(redis: RedisClient, email: str) -> str:
    token = new_token()
    await redis.set(_login_key(token), email, ex=LOGIN_TOKEN_TTL_S)
    return token


async def login_token_is_valid(redis: RedisClient, token: str) -> bool:
    """Peek without consuming (used by the confirm page)."""
    return bool(await redis.exists(_login_key(token)))


async def consume_login_token(redis: RedisClient, token: str) -> str | None:
    """Atomically read-and-delete: a token can be used exactly once."""
    email: str | None = await redis.getdel(_login_key(token))
    return email


async def create_session(redis: RedisClient, user_id: uuid.UUID) -> str:
    session_id = new_token()  # always a fresh ID at login (no session fixation)
    await redis.set(_session_key(session_id), str(user_id), ex=SESSION_TTL_S)
    return session_id


async def session_user_id(redis: RedisClient, session_id: str) -> uuid.UUID | None:
    raw = await redis.get(_session_key(session_id))
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def delete_session(redis: RedisClient, session_id: str) -> None:
    await redis.delete(_session_key(session_id))
