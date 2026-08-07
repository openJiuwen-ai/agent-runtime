# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""部署相关配置（设计：环境变量驱动，命名详细清楚）。

所有部署相关项从环境变量读取，缺省给出可直接本地跑的安全值。环境变量统一前缀
``OPENJIUWEN_SERVICE_``，避免与 foundation 的通用 ``HOST``/``PORT`` 混淆。

| 环境变量 | 含义 | 默认 |
|---|---|---|
| ``OPENJIUWEN_SERVICE_HOST`` | 监听地址 | ``0.0.0.0`` |
| ``OPENJIUWEN_SERVICE_PORT`` | 监听端口 | ``8090`` |
| ``OPENJIUWEN_SERVICE_REDIS_URL`` | 协调用 redis 连接串 | ``redis://localhost:6379/0`` |
| ``OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX`` | redis 键命名空间前缀 | ``service`` |
| ``OPENJIUWEN_SERVICE_TITLE`` | 服务标题（OpenAPI/日志） | ``service`` |
| ``OPENJIUWEN_SERVICE_REQUEST_TIMEOUT_SECONDS`` | 请求超时秒数，0 表示不设置 deadline | ``0`` |
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8090
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_DEFAULT_KEY_PREFIX = "service"
_DEFAULT_TITLE = "service"
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 0.0


@dataclass(frozen=True)
class ServiceConfig:
    """服务部署配置。用 :meth:`from_env` 从环境变量构造。"""

    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    redis_url: str = _DEFAULT_REDIS_URL
    key_prefix: str = _DEFAULT_KEY_PREFIX
    title: str = _DEFAULT_TITLE
    request_timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds < 0:
            raise ValueError("request_timeout_seconds must be a finite non-negative number")

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        """从环境变量读取；非法端口立即报错（fail-fast）。"""
        return cls(
            host=os.getenv("OPENJIUWEN_SERVICE_HOST", _DEFAULT_HOST),
            port=int(os.getenv("OPENJIUWEN_SERVICE_PORT", str(_DEFAULT_PORT))),
            redis_url=os.getenv("OPENJIUWEN_SERVICE_REDIS_URL", _DEFAULT_REDIS_URL),
            key_prefix=os.getenv("OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX", _DEFAULT_KEY_PREFIX),
            title=os.getenv("OPENJIUWEN_SERVICE_TITLE", _DEFAULT_TITLE),
            request_timeout_seconds=float(os.getenv(
                "OPENJIUWEN_SERVICE_REQUEST_TIMEOUT_SECONDS",
                str(_DEFAULT_REQUEST_TIMEOUT_SECONDS),
            )),
        )
