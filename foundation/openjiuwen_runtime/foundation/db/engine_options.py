# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SQLAlchemy async engine 连接池参数（可通过环境变量覆盖）。"""

from __future__ import annotations

import os
from typing import Any

# 定期回收池内连接，应小于 MySQL wait_timeout / 中间层 idle 超时。
DEFAULT_POOL_RECYCLE_SECONDS = 1800

# 可通过 RUNTIME_DB_POOL_* / DB_POOL_* 覆盖
DEFAULT_POOL_SIZE = 2
DEFAULT_MAX_OVERFLOW = 20
DEFAULT_POOL_TIMEOUT = 30

# aiomysql 建连超时（秒）：防网络黑洞时建连永久挂起（aiomysql 默认无限制）。
# asyncpg 同用此值注入建连 timeout（asyncpg 默认 60s，与 MySQL 对齐收紧）。
# 可通过 RUNTIME_DB_CONNECT_TIMEOUT / DB_CONNECT_TIMEOUT 覆盖。
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5

# asyncpg 单条命令超时（秒）：asyncpg 默认无限制（command_timeout=None），
# 慢查询/网络黑洞时语句可永久挂起。默认 30s：远大于本框架业务的任何合法
# 查询，又低于请求级 deadline 兜底。可通过 RUNTIME_DB_COMMAND_TIMEOUT /
# DB_COMMAND_TIMEOUT 覆盖。
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30


def _int_env(*names: str, default: int) -> int:
    for name in names:
        raw = os.getenv(name, "").strip()
        if not raw:
            continue
        try:
            return max(1, int(raw))
        except ValueError:
            continue
    return default


def get_pool_size() -> int:
    return _int_env("RUNTIME_DB_POOL_SIZE", "DB_POOL_SIZE", default=DEFAULT_POOL_SIZE)


def get_max_overflow() -> int:
    return _int_env(
        "RUNTIME_DB_MAX_OVERFLOW",
        "DB_MAX_OVERFLOW",
        default=DEFAULT_MAX_OVERFLOW,
    )


def get_pool_timeout() -> int:
    return _int_env(
        "RUNTIME_DB_POOL_TIMEOUT",
        "DB_POOL_TIMEOUT",
        default=DEFAULT_POOL_TIMEOUT,
    )


def get_connect_timeout() -> int:
    return _int_env(
        "RUNTIME_DB_CONNECT_TIMEOUT",
        "DB_CONNECT_TIMEOUT",
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
    )


def get_command_timeout() -> int:
    return _int_env(
        "RUNTIME_DB_COMMAND_TIMEOUT",
        "DB_COMMAND_TIMEOUT",
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )


def build_async_engine_kwargs(
    *,
    connect_args: dict[str, Any] | None = None,
    echo: bool = False,
) -> dict[str, Any]:
    """构造 ``create_async_engine`` 的通用连接池参数。"""
    return {
        "echo": echo,
        "connect_args": connect_args or {},
        "pool_pre_ping": True,
        "pool_recycle": DEFAULT_POOL_RECYCLE_SECONDS,
        "pool_size": get_pool_size(),
        "max_overflow": get_max_overflow(),
        "pool_timeout": get_pool_timeout(),
    }
