import hashlib
import hmac
import secrets


def new_token(nbytes: int = 32) -> str:
    """URL-safe random token (32 bytes of entropy -> 43 chars)."""
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def keyed_hash(value: str, salt: str) -> str:
    """HMAC-SHA256. Used to store IPs/user-agents without keeping the raw value."""
    return hmac.new(salt.encode(), value.encode(), hashlib.sha256).hexdigest()
