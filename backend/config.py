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
    # Hard limit for a single uploaded file.
    max_upload_mb: int = 200
    # Stubbed "processing time" for the video analysis pipeline (see analysis.py).
    analysis_delay_seconds: float = 8.0
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

    @field_validator("public_base_url", mode="before")
    @classmethod
    def _strip_trailing_slash(cls, value: object) -> object:
        if isinstance(value, str):
            return value.rstrip("/")
        return value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
