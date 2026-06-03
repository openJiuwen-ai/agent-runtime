# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import Field, Json
from pydantic_settings import BaseSettings, SettingsConfigDict


# 调用 Versatile 时默认注入的请求头（JSON 字符串，会被 Json[...] 字段解析）。
#
# 默认值仅放跨环境通用的非鉴权头（Accept / stream），**不含 Cookie**：
# 把环境特定的 Session token（如 ``AGENT_SID=...``）钉死在代码里，跨环境共用同
# 一份代码时会触发 VA "URL.project_id ↔ token.project_id mismatch" 的 403
# （c1a46ad 引入、随后由本次重构纠正）。
#
# 需要 Cookie 的部署，在 `.env` 或环境变量里设置 ``VERSATILE_HEADERS_TEMPLATE``
# 显式注入：
#
#     VERSATILE_HEADERS_TEMPLATE={"Cookie":"AGENT_SID=<your_token>",\
#         "Accept":"application/json, text/event-stream","stream":"true"}
_DEFAULT_VERSATILE_HEADERS_TEMPLATE = (
    '{"Accept":"application/json, text/event-stream",'
    '"stream":"true"}'
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_by_name=True,
        validate_by_alias=True,
    )

    # ── App ─────────────────────────────────────────────────────────────────
    adapter_app_name: Optional[str] = None

    # ── Versatile 低码平台 ───────────────────────────────────────────────────
    versatile_url_template: Optional[str] = None
    versatile_timeout: Optional[int] = None
    versatile_headers_template: Json[Dict[str, Any]] = _DEFAULT_VERSATILE_HEADERS_TEMPLATE
    versatile_adapter_type: str = "controller"  # "controller" 或 "workflow"
    versatile_workflow_result_node: Optional[str] = Field(
        default=None, alias="va_workflow_result_node",
    )  # 环境变量 VA_WORKFLOW_RESULT_NODE，代码中用 versatile_ 前缀访问

    # ── FastAPI ─────────────────────────────────────────────────────────────
    adapter_fastapi_host: Optional[str] = None
    adapter_fastapi_port: Optional[int] = None
    adapter_fastapi_debug: Optional[bool] = None
    adapter_fastapi_workers: Optional[int] = None

    # ── 日志 ────────────────────────────────────────────────────────────────
    adapter_log_level: Optional[str] = None
    adapter_log_file: Optional[str] = None

    # ── Redis ──────────────────────────────────────────────────────────────
    redis_host: Optional[str] = None
    redis_port: Optional[int] = None
    redis_db: Optional[int] = None
    redis_password: Optional[str] = None
    redis_session_ttl: Optional[int] = None

    @property
    def redis_url(self) -> str:
        from urllib.parse import quote_plus
        if self.redis_password:
            pwd = quote_plus(self.redis_password)
            return f"redis://:{pwd}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
