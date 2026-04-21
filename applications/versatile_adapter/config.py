from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_env_file_path() -> Path:
    """
    Get .env file path with CONFIG_PATH support.
    
    Priority:
    1. CONFIG_PATH environment variable (for externalized config)
    2. Default: .env in the same directory as this file
    """
    config_path = os.environ.get("CONFIG_PATH")
    if config_path:
        return Path(config_path)
    return Path(__file__).parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_get_env_file_path(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ─────────────────────────────────────────────────────────────────
    app_name: Optional[str] = None

    # ── Versatile 低码平台 ───────────────────────────────────────────────────
    versatile_url_template: Optional[str] = None
    versatile_timeout: Optional[int] = None

    # ── FastAPI ─────────────────────────────────────────────────────────────
    fastapi_host: Optional[str] = None
    fastapi_port: Optional[int] = None
    fastapi_debug: Optional[bool] = None
    fastapi_workers: Optional[int] = None

    # ── 日志 ────────────────────────────────────────────────────────────────
    log_level: Optional[str] = None
    log_file: Optional[str] = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
