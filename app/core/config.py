from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (or a local .env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "staging", "production"] = "local"
    debug: bool = False
    app_name: str = "PingTag"
    base_url: str = "http://localhost:8000"

    database_url: str = "postgresql+asyncpg://pingtag:pingtag@127.0.0.1:5432/pingtag"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # No defaults on purpose: the app must refuse to start without them.
    secret_key: SecretStr
    ip_hash_salt: SecretStr

    @model_validator(mode="after")
    def _harden_production(self) -> Self:
        if self.env == "production":
            if len(self.secret_key.get_secret_value()) < 32:
                raise ValueError("SECRET_KEY must be at least 32 chars in production")
            if len(self.ip_hash_salt.get_secret_value()) < 16:
                raise ValueError("IP_HASH_SALT must be at least 16 chars in production")
            if self.debug:
                raise ValueError("DEBUG must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
