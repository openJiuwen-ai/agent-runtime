from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_env_file_path() -> Path:
    """
    Get .env file path with CONFIG_PATH support.
    
    Priority:
    1. CONFIG_PATH environment variable (for externalized config)
    2. Default: .env in the same directory as this file
    
    This enables:
    - Docker/K8s deployment with external config files
    - Multi-environment support (dev/test/prod)
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

    # ── Redis（会话状态）────────────────────────────────────────────────────
    redis_host: Optional[str] = None
    redis_port: Optional[int] = None
    redis_db: Optional[int] = None
    redis_password: Optional[str] = None
    redis_session_ttl: Optional[int] = None

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
    log_file: Optional[str] = None

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            pwd = quote_plus(self.redis_password)
            return f"redis://:{pwd}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
