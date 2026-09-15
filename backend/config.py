from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://csm:csm@localhost:5432/csm"
    secret_key: str = "change-me-to-a-long-random-string"
    session_ttl_days: int = 7

    # Where uploaded files (site plans, journal videos) are stored on disk.
    media_root: Path = Path("media")
    # Base URL the browser can reach this API on; used to build file URLs
    # ({public_base_url}/files/{asset_id}) returned in responses.
    public_base_url: str = "http://localhost:8000"
    # Base URL the vision service (a different container) reaches this API
    # on to download the uploaded video — not the same as public_base_url,
    # which is only reachable from the host/browser. In docker-compose this
    # is http://backend:8000; defaults to public_base_url for local dev
    # where everything runs on localhost.
    internal_base_url: str | None = None
    # Hard limit for a single uploaded file.
    max_upload_mb: int = 200
    # RabbitMQ broker the vision/phase/delay pipeline runs over (see
    # integrations/README.md and integrations/broker.py).
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    # Any localhost / 127.0.0.1 port passes by default so a Vite dev server that
    # falls back to 5174, 5175, … still works. Set to "" to disable.
    cors_origin_regex: str | None = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origin_regex", mode="before")
    @classmethod
    def _blank_regex_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("public_base_url", "internal_base_url", mode="before")
    @classmethod
    def _strip_trailing_slash(cls, value: object) -> object:
        if isinstance(value, str):
            return value.rstrip("/")
        return value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def internal_url(self) -> str:
        return self.internal_base_url or self.public_base_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
