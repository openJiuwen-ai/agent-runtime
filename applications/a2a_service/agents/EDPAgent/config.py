from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class DPASettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ─────────────────────────────────────────────────────────────────
    app_name: Optional[str] = None

    # ── LLM ─────────────────────────────────────────────────────────────────
    llm_provider: Optional[str] = None
    llm_api_base: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model_name: Optional[str] = None
    llm_verify_ssl: Optional[bool] = None

    # ── Redis（Checkpointer）────────────────────────────────────────────────
    redis_host: Optional[str] = None
    redis_port: Optional[int] = None
    redis_db: Optional[int] = None
    redis_password: Optional[str] = None
    redis_checkpointer_ttl_minutes: Optional[int] = None

    # ── DPA Agent ───────────────────────────────────────────────────────────
    dpa_agent_id: Optional[str] = None
    dpa_agent_name: Optional[str] = None
    dpa_max_iterations: Optional[int] = None

    # ── 日志 ────────────────────────────────────────────────────────────────
    log_level: Optional[str] = None
    log_file: Optional[str] = None

    @property
    def redis_url(self) -> str:
        """拼接 Redis 连接 URL（密码含特殊字符时自动转义）。"""
        if self.redis_password:
            pwd = quote_plus(self.redis_password)
            return f"redis://:{pwd}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> DPASettings:
    return DPASettings()
