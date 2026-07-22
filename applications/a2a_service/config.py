# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.crypto import decrypt_config_value


def _parse_size_to_bytes(value) -> int:
    """将 MB 字符串转为字节 int。

    只支持 MB 单位，如 '500 MB'、'20MB'；纯数字按字节。
    """
    if isinstance(value, int):
        return value
    s = str(value).strip().upper()
    if s.endswith("MB"):
        num = s[:-2].strip()
        return int(float(num) * 1024 * 1024)
    return int(s)



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
    # 会话级限流
    rate_limit_max_requests: int = 1
    rate_limit_window_seconds: int = 10
    # 全局级限流
    global_rate_limit_max_requests: int = 10
    global_rate_limit_window_seconds: int = 10

    # ── VersatileAdapter（内部 A2A 服务地址）────────────────────────────────
    versatile_adapter_url: Optional[str] = None
    # a2a_service → VersatileAdapter 的 HTTP 超时（秒），与versatile_adapter中VERSATILE_TIMEOUT 默认值对齐，需要<=工小智前端超时时延57s
    versatile_adapter_timeout: int = 57
    # Runtime 心跳参数
    heartbeat_interval_seconds: int = 15
    heartbeat_timeout_seconds: int = 1800
    # VA 流中携带工作流最终结果的 QA 节点名称（node_type=="QA" 且 node_name==此值）
    va_workflow_result_node: Optional[str] = None

    # ── 并行子 Agent / 多工作流（对齐 TECH §5.1）────────────────────────────
    # 注：子 Agent url 不再由框架配置（P-006）——由 Agent 自管、随派发请求下传，
    # Executor 按 spec.url 懒构造 client。原 SUB_AGENT_URL 已移除。
    max_concurrent_sub_agents: int = 3          # 全局并发子 Agent 上限
    sub_agent_timeout_seconds: int = 1800        # 单子 Agent 执行超时秒数（默认 30 分钟）
    max_parallel_workflows_per_agent: int = 3    # 单子 Agent 内最大并行工作流数
    workflow_timeout_seconds: int = 900          # 单工作流 VA 调用超时秒数（默认 15 分钟）
    max_call_depth: int = 3                      # 递归派发深度上限（root=0，主→子=1…），防爆栈
    # 子 Agent 写自身 Redis 会话上下文时使用的 agent_id（标识子 Agent 身份，不继承主流程）
    dpa_agent_id: Optional[str] = None

    # ── FastAPI ─────────────────────────────────────────────────────────────
    fastapi_host: Optional[str] = None
    fastapi_port: Optional[int] = None
    fastapi_debug: Optional[bool] = None
    fastapi_workers: Optional[int] = None

    # ── Runtime DB（DB 权威 + Redis 缓存）──────────────────────────────────
    # 开关：true=启用DB持久化（先写DB再写Redis，Redis miss时回源DB）；false=纯Redis模式（不连DB）
    runtime_db_enabled: bool = False
    # 禁用DB时以下字段允许留空，启用DB时在 validator 中校验
    runtime_db_type: Optional[str] = None
    runtime_db_host: Optional[str] = None
    runtime_db_port: Optional[str] = None
    runtime_db_name: Optional[str] = None
    runtime_db_user: Optional[str] = None
    runtime_db_password: Optional[str] = None
    runtime_db_sqlite_path: Optional[str] = None

    # ── 日志 ────────────────────────────────────────────────────────────────
    log_level: Optional[str] = None
    log_dir: Optional[str] = None
    # 日志轮转大小（loguru格式，如 "20 MB"）
    log_rotation_size: str = "20 MB"
    # 日志保留天数（0=不按天数清理）
    log_retention_days: int = 7
    # 日志总空间上限（字节，0=不按空间清理，默认500MB）
    log_max_total_size: int = 524288000

    # ── SDK 日志（openjiuwen SDK 的日志清理配置）────────────────────────────
    # SDK 日志单文件大小阈值（字节，达到即触发轮转，默认20MB）
    jiuwen_log_max_bytes: int = 20971520
    # SDK 日志归档文件保留数量（轮转后最多保留 N 个 .gz，默认20）
    jiuwen_log_backup_count: int = 20
    # SDK 日志归档保留天数（0=不按天数清理，默认7天）
    jiuwen_log_retention_days: int = 7
    # SDK 日志总空间上限（字节，0=不按空间清理，默认500MB）
    jiuwen_log_max_total_size: int = 524288000

    @field_validator("log_max_total_size", "jiuwen_log_max_bytes", "jiuwen_log_max_total_size", mode="before")
    @classmethod
    def _parse_size_fields(cls, v):
        return _parse_size_to_bytes(v)

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
