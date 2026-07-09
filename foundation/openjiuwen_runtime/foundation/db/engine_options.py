# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SQLAlchemy async engine 连接池参数（可通过环境变量覆盖）。"""

from __future__ import annotations

import os
from typing import Any

# 定期回收池内连接，应小于 MySQL wait_timeout / 中间层 idle 超时。
DEFAULT_POOL_RECYCLE_SECONDS = 1800

# 与历史 create_async_engine(pool_size=20, max_overflow=20) 保持一致（可通过 RUNTIME_DB_POOL_* 覆盖）
DEFAULT_POOL_SIZE = 20
DEFAULT_MAX_OVERFLOW = 20
DEFAULT_POOL_TIMEOUT = 30


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
