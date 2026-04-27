# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ─────────────────────────────────────────────────────────────────
    adapter_app_name: Optional[str] = None

    # ── Versatile 低码平台 ───────────────────────────────────────────────────
    versatile_url_template: Optional[str] = None
    versatile_timeout: Optional[int] = None

    # ── FastAPI ─────────────────────────────────────────────────────────────
    adapter_fastapi_host: Optional[str] = None
    adapter_fastapi_port: Optional[int] = None
    adapter_fastapi_debug: Optional[bool] = None
    adapter_fastapi_workers: Optional[int] = None

    # ── 日志 ────────────────────────────────────────────────────────────────
    adapter_log_level: Optional[str] = None
    adapter_log_file: Optional[str] = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
