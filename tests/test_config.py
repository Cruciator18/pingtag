import pytest
from pydantic import ValidationError

from app.core.config import Settings

GOOD_SECRET = "s" * 32
GOOD_SALT = "h" * 16


def make(**overrides: object) -> Settings:
    base: dict[str, object] = {"secret_key": GOOD_SECRET, "ip_hash_salt": GOOD_SALT}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_local_defaults() -> None:
    s = make()
    assert s.env == "local"
    assert s.app_name == "PingTag"
    assert s.database_url.startswith("postgresql+asyncpg://")


def test_secrets_are_required() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_secrets_are_masked_in_repr() -> None:
    assert GOOD_SECRET not in repr(make())


def test_production_rejects_short_secret() -> None:
    with pytest.raises(ValidationError):
        make(env="production", secret_key="short")


def test_production_rejects_debug() -> None:
    with pytest.raises(ValidationError):
        make(env="production", debug=True)


def test_production_accepts_strong_config() -> None:
    assert make(env="production").env == "production"
