# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

from common.crypto import decrypt_config_value



class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ─────────────────────────────────────────────────────────────────
    app_name: Optional[str] = None

    # ── Redis（会话状态）────────────────────────────────────────────────────
    redis_host: Optional[str] = None
    redis_port: Optional[int] = None
    redis_db: Optional[int] = None
    redis_password: Optional[str] = None
    redis_session_ttl: Optional[int] = None

    # ── 启动编排（Redis 锁 + 状态协同）────────────────────────────────────
    bootstrap_coordination_enabled: bool = True
    bootstrap_lock_name: str = "a2a_global_bootstrap"
    bootstrap_lock_ttl_sec: int = 180
    bootstrap_wait_timeout_sec: int = 300
    bootstrap_poll_interval_sec: float = 1.0

    # ── 入口限流（与 Orchestrator 限流能力对齐）─────────────────────────────
    rate_limit_max_requests: int = 1
    rate_limit_window_seconds: int = 10
    global_rate_limit_max_requests: int = 10
    global_rate_limit_window_seconds: int = 10

    # ── VersatileAdapter（内部 A2A 服务地址）────────────────────────────────
    versatile_adapter_url: Optional[str] = None
    # VA 流中携带工作流最终结果的 QA 节点名称（node_type=="QA" 且 node_name==此值）
    va_workflow_result_node: Optional[str] = None

    # ── FastAPI ─────────────────────────────────────────────────────────────
    fastapi_host: Optional[str] = None
    fastapi_port: Optional[int] = None
    fastapi_debug: Optional[bool] = None
    fastapi_workers: Optional[int] = None

    # ── 日志 ────────────────────────────────────────────────────────────────
    log_level: Optional[str] = None
    log_dir: Optional[str] = None

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            plain = decrypt_config_value(self.redis_password) or ""
            pwd = quote_plus(plain)
            return f"redis://:{pwd}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
