# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import Json
from pydantic_settings import BaseSettings, SettingsConfigDict


# 调用 Versatile 时无条件注入的默认请求头（JSON 字符串，会被 Json[...] 字段解析）。
# 用途：注入 Cookie:AGENT_SID 等 Session 鉴权头，让依赖 Session 的工作流（FUND_BETA 等）
# 即使上游不传 Cookie 也能跑通（issue 2026-04-28-versatile-adapter-missing-auth-headers）。
# 暂不暴露到 .env：硬编码默认值；如需覆盖可通过环境变量 VERSATILE_HEADERS_TEMPLATE 临时设置。
_DEFAULT_VERSATILE_HEADERS_TEMPLATE = (
    '{"Cookie":"AGENT_SID=testUser|0",'
    '"Accept":"application/json, text/event-stream",'
    '"stream":"true"}'
)


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
    versatile_headers_template: Json[Dict[str, Any]] = _DEFAULT_VERSATILE_HEADERS_TEMPLATE

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
