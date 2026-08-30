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
| ``OPENJIUWEN_SERVICE_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS`` | redis 建连超时秒数，0 表示不限制 | ``3`` |
| ``OPENJIUWEN_SERVICE_REDIS_SOCKET_TIMEOUT_SECONDS`` | redis 命令读写 socket 超时秒数，0 表示不限制 | ``5`` |
| ``OPENJIUWEN_SERVICE_REDIS_HEALTH_CHECK_INTERVAL_SECONDS`` | redis 空闲连接周期性 PING 保活/验活间隔秒，0 关闭 | ``30`` |
| ``OPENJIUWEN_SERVICE_REDIS_RETRY_ATTEMPTS`` | redis 连接类错误命令级重试次数，0 关闭 | ``3`` |
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from openjiuwen_runtime.foundation.db.utils import is_postgresql, is_sqlite


_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8090
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_DEFAULT_KEY_PREFIX = "service"
_DEFAULT_TITLE = "service"
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 0.0
_DEFAULT_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS = 3.0
_DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS = 5.0
_DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SECONDS = 30
_DEFAULT_REDIS_RETRY_ATTEMPTS = 3
_DEFAULT_LOCK_BACKEND = "auto"
_DEFAULT_LOCK_KEY_PREFIX = "service:lock"
_DEFAULT_LOCK_TTL_SECONDS = 30.0
_DEFAULT_LOCK_WAIT_SECONDS = 0.0
_DEFAULT_LOCK_RENEW_RATIO = 0.333
_DEFAULT_LOCK_RELEASE_TIMEOUT_SECONDS = 3.0
_DEFAULT_DEPLOY_REPLICAS = 1
_DEFAULT_CACHE_BACKEND = "memory"
_DEFAULT_CACHE_KEY_PREFIX = "service:cache"
_DEFAULT_CACHE_DEFAULT_TTL_SECONDS = 300.0
_DEFAULT_CACHE_MAX_ENTRIES = 1000
_DEFAULT_DB_TYPE = "none"
_DEFAULT_DB_PORT = 3306
_DEFAULT_ETCD_CONNECT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ServiceConfig:
    """服务部署配置。用 :meth:`from_env` 从环境变量构造。"""

    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    redis_url: str = _DEFAULT_REDIS_URL
    redis_password: str | None = None
    key_prefix: str = _DEFAULT_KEY_PREFIX
    title: str = _DEFAULT_TITLE
    request_timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS
    # redis 网络兜底：0 表示关闭该项（socket 层无超时，恢复 redis-py 原生行为）
    redis_socket_connect_timeout_seconds: float = (
        _DEFAULT_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS
    )
    redis_socket_timeout_seconds: float = _DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS
    redis_health_check_interval_seconds: int = (
        _DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SECONDS
    )
    redis_retry_attempts: int = _DEFAULT_REDIS_RETRY_ATTEMPTS
    lock_backend: str = _DEFAULT_LOCK_BACKEND
    lock_key_prefix: str = _DEFAULT_LOCK_KEY_PREFIX
    lock_ttl_seconds: float = _DEFAULT_LOCK_TTL_SECONDS
    lock_wait_seconds: float = _DEFAULT_LOCK_WAIT_SECONDS
    lock_renew_ratio: float = _DEFAULT_LOCK_RENEW_RATIO
    lock_release_timeout_seconds: float = _DEFAULT_LOCK_RELEASE_TIMEOUT_SECONDS
    deploy_replicas: int = _DEFAULT_DEPLOY_REPLICAS
    etcd_endpoints: tuple[str, ...] = ()
    etcd_username: str | None = None
    etcd_password: str | None = None
    etcd_ca_cert: str | None = None
    etcd_cert: str | None = None
    etcd_key: str | None = None
    etcd_connect_timeout_seconds: float = _DEFAULT_ETCD_CONNECT_TIMEOUT_SECONDS
    cache_backend: str = _DEFAULT_CACHE_BACKEND
    cache_key_prefix: str = _DEFAULT_CACHE_KEY_PREFIX
    cache_default_ttl_seconds: float = _DEFAULT_CACHE_DEFAULT_TTL_SECONDS
    cache_max_entries: int = _DEFAULT_CACHE_MAX_ENTRIES
    db_type: str = _DEFAULT_DB_TYPE
    db_host: str | None = None
    db_port: int = _DEFAULT_DB_PORT
    db_name: str | None = None
    db_user: str | None = None
    db_password: str | None = None
    pg_schema: str = "public"

    def __post_init__(self) -> None:
        self._validate_port("port", self.port)
        self._validate_port("db_port", self.db_port)
        self._validate_non_negative(
            "request_timeout_seconds", self.request_timeout_seconds
        )
        self._validate_non_negative(
            "redis_socket_connect_timeout_seconds",
            self.redis_socket_connect_timeout_seconds,
        )
        self._validate_non_negative(
            "redis_socket_timeout_seconds", self.redis_socket_timeout_seconds
        )
        self._validate_non_negative_int(
            "redis_health_check_interval_seconds",
            self.redis_health_check_interval_seconds,
        )
        self._validate_non_negative_int("redis_retry_attempts", self.redis_retry_attempts)
        object.__setattr__(self, "lock_backend", str(self.lock_backend).strip().lower())
        object.__setattr__(
            self, "cache_backend", str(self.cache_backend).strip().lower()
        )
        object.__setattr__(self, "db_type", str(self.db_type).strip().lower())
        self._validate_choice(
            "lock_backend", self.lock_backend, {"auto", "etcd", "redis", "memory"}
        )
        self._validate_choice(
            "cache_backend",
            self.cache_backend,
            {"memory", "redis", "none"},
        )
        self._validate_choice(
            "db_type", self.db_type, {"mysql", "postgresql", "sqlite", "none"}
        )
        self._validate_positive("lock_ttl_seconds", self.lock_ttl_seconds)
        self._validate_non_negative("lock_wait_seconds", self.lock_wait_seconds)
        self._validate_positive("lock_renew_ratio", self.lock_renew_ratio)
        if float(self.lock_renew_ratio) > 1:
            raise ValueError("lock_renew_ratio must be less than or equal to 1")
        self._validate_positive(
            "lock_release_timeout_seconds", self.lock_release_timeout_seconds
        )
        self._validate_positive_int("deploy_replicas", self.deploy_replicas)
        self._validate_positive(
            "etcd_connect_timeout_seconds", self.etcd_connect_timeout_seconds
        )
        self._validate_positive(
            "cache_default_ttl_seconds", self.cache_default_ttl_seconds
        )
        self._validate_positive_int("cache_max_entries", self.cache_max_entries)
        if not self.lock_key_prefix.strip():
            raise ValueError("lock_key_prefix must not be empty")
        if not self.cache_key_prefix.strip():
            raise ValueError("cache_key_prefix must not be empty")
        if self.etcd_endpoints is None:
            object.__setattr__(self, "etcd_endpoints", ())
        elif isinstance(self.etcd_endpoints, str):
            object.__setattr__(
                self,
                "etcd_endpoints",
                self._parse_endpoints(self.etcd_endpoints),
            )
        else:
            values = tuple(
                str(value).strip()
                for value in self.etcd_endpoints
                if str(value).strip()
            )
            if values:
                self._parse_endpoints(",".join(values))
            object.__setattr__(self, "etcd_endpoints", values)
        if (self.etcd_username is None) != (self.etcd_password is None):
            raise ValueError(
                "etcd_username and etcd_password must be configured together"
            )
        if (self.etcd_cert is None) != (self.etcd_key is None):
            raise ValueError("etcd_cert and etcd_key must be configured together")
        if self.lock_backend == "etcd" and not self.etcd_endpoints:
            raise ValueError("etcd_endpoints is required for lock_backend=etcd")
        if self.lock_backend == "redis" and not self.redis_url:
            raise ValueError("redis_url is required for lock_backend=redis")
        if self.cache_backend == "redis" and not self.redis_url:
            raise ValueError(
                f"redis_url is required for cache_backend={self.cache_backend}"
            )
        if self.deploy_replicas > 1 and self.lock_backend == "memory":
            raise ValueError(
                "memory lock backend cannot be used with multiple replicas"
            )
        if self.db_type in ("mysql", "postgresql"):
            missing = []
            required_fields = (
                ("db_host", self.db_host),
                ("db_name", self.db_name),
                ("db_user", self.db_user),
            )
            for name, value in required_fields:
                if not value:
                    missing.append(name)
            if missing:
                raise ValueError(
                    f"{', '.join(missing)} is required for db_type={self.db_type}"
                )
        if is_sqlite(self.db_type) and not self.db_name:
            raise ValueError("db_name is required for db_type=sqlite")

    @staticmethod
    def _validate_port(name: str, value: int) -> None:
        if (
            isinstance(value, bool)
            or int(value) != value
            or not 1 <= int(value) <= 65535
        ):
            raise ValueError(f"{name} must be an integer between 1 and 65535")

    @staticmethod
    def _validate_positive(name: str, value: float) -> None:
        if not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"{name} must be a finite positive number")

    @staticmethod
    def _validate_non_negative(name: str, value: float) -> None:
        if not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"{name} must be a finite non-negative number")

    @staticmethod
    def _validate_positive_int(name: str, value: int) -> None:
        if isinstance(value, bool) or int(value) != value or int(value) < 1:
            raise ValueError(f"{name} must be a positive integer")

    @staticmethod
    def _validate_non_negative_int(name: str, value: int) -> None:
        if isinstance(value, bool) or int(value) != value or int(value) < 0:
            raise ValueError(f"{name} must be a non-negative integer")

    @staticmethod
    def _validate_choice(name: str, value: str, choices: set[str]) -> None:
        if str(value).strip().lower() not in choices:
            choices_text = ", ".join(sorted(choices))
            raise ValueError(f"{name} must be one of {choices_text}")

    @staticmethod
    def _parse_endpoints(value: str) -> tuple[str, ...]:
        from .context.locks.backends.etcd import parse_etcd_endpoint

        endpoints = tuple(item.strip() for item in value.split(",") if item.strip())
        if not endpoints:
            raise ValueError("etcd_endpoints must contain at least one endpoint")
        for endpoint in endpoints:
            parse_etcd_endpoint(endpoint)
        return endpoints

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        """从环境变量读取；非法端口立即报错（fail-fast）。"""
        endpoints = os.getenv("OPENJIUWEN_SERVICE_ETCD_ENDPOINTS", "")
        return cls(
            host=os.getenv("OPENJIUWEN_SERVICE_HOST", _DEFAULT_HOST),
            port=int(os.getenv("OPENJIUWEN_SERVICE_PORT", str(_DEFAULT_PORT))),
            redis_url=os.getenv("OPENJIUWEN_SERVICE_REDIS_URL", _DEFAULT_REDIS_URL),
            redis_password=(os.getenv("REDIS_PASSWORD", "").strip() or None),
            key_prefix=os.getenv(
                "OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX", _DEFAULT_KEY_PREFIX
            ),
            title=os.getenv("OPENJIUWEN_SERVICE_TITLE", _DEFAULT_TITLE),
            request_timeout_seconds=float(
                os.getenv(
                    "OPENJIUWEN_SERVICE_REQUEST_TIMEOUT_SECONDS",
                    str(_DEFAULT_REQUEST_TIMEOUT_SECONDS),
                )
            ),
            redis_socket_connect_timeout_seconds=float(
                os.getenv(
                    "OPENJIUWEN_SERVICE_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS",
                    str(_DEFAULT_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS),
                )
            ),
            redis_socket_timeout_seconds=float(
                os.getenv(
                    "OPENJIUWEN_SERVICE_REDIS_SOCKET_TIMEOUT_SECONDS",
                    str(_DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS),
                )
            ),
            redis_health_check_interval_seconds=int(
                os.getenv(
                    "OPENJIUWEN_SERVICE_REDIS_HEALTH_CHECK_INTERVAL_SECONDS",
                    str(_DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SECONDS),
                )
            ),
            redis_retry_attempts=int(
                os.getenv(
                    "OPENJIUWEN_SERVICE_REDIS_RETRY_ATTEMPTS",
                    str(_DEFAULT_REDIS_RETRY_ATTEMPTS),
                )
            ),
            lock_backend=os.getenv(
                "OPENJIUWEN_SERVICE_LOCK_BACKEND", _DEFAULT_LOCK_BACKEND
            ).lower(),
            lock_key_prefix=os.getenv(
                "OPENJIUWEN_SERVICE_LOCK_KEY_PREFIX", _DEFAULT_LOCK_KEY_PREFIX
            ),
            lock_ttl_seconds=float(
                os.getenv(
                    "OPENJIUWEN_SERVICE_LOCK_TTL_SECONDS",
                    str(_DEFAULT_LOCK_TTL_SECONDS),
                )
            ),
            lock_wait_seconds=float(
                os.getenv(
                    "OPENJIUWEN_SERVICE_LOCK_WAIT_SECONDS",
                    str(_DEFAULT_LOCK_WAIT_SECONDS),
                )
            ),
            lock_renew_ratio=float(
                os.getenv(
                    "OPENJIUWEN_SERVICE_LOCK_RENEW_RATIO",
                    str(_DEFAULT_LOCK_RENEW_RATIO),
                )
            ),
            lock_release_timeout_seconds=float(
                os.getenv(
                    "OPENJIUWEN_SERVICE_LOCK_RELEASE_TIMEOUT_SECONDS",
                    str(_DEFAULT_LOCK_RELEASE_TIMEOUT_SECONDS),
                )
            ),
            deploy_replicas=int(
                os.getenv(
                    "OPENJIUWEN_SERVICE_DEPLOY_REPLICAS", str(_DEFAULT_DEPLOY_REPLICAS)
                )
            ),
            etcd_endpoints=cls._parse_endpoints(endpoints) if endpoints.strip() else (),
            etcd_username=os.getenv("OPENJIUWEN_SERVICE_ETCD_USERNAME") or None,
            etcd_password=os.getenv("OPENJIUWEN_SERVICE_ETCD_PASSWORD") or None,
            etcd_ca_cert=os.getenv("OPENJIUWEN_SERVICE_ETCD_CA_CERT") or None,
            etcd_cert=os.getenv("OPENJIUWEN_SERVICE_ETCD_CERT") or None,
            etcd_key=os.getenv("OPENJIUWEN_SERVICE_ETCD_KEY") or None,
            etcd_connect_timeout_seconds=float(
                os.getenv(
                    "OPENJIUWEN_SERVICE_ETCD_CONNECT_TIMEOUT_SECONDS",
                    str(_DEFAULT_ETCD_CONNECT_TIMEOUT_SECONDS),
                )
            ),
            cache_backend=os.getenv(
                "OPENJIUWEN_SERVICE_CACHE_BACKEND", _DEFAULT_CACHE_BACKEND
            ).lower(),
            cache_key_prefix=os.getenv(
                "OPENJIUWEN_SERVICE_CACHE_KEY_PREFIX", _DEFAULT_CACHE_KEY_PREFIX
            ),
            cache_default_ttl_seconds=float(
                os.getenv(
                    "OPENJIUWEN_SERVICE_CACHE_DEFAULT_TTL_SECONDS",
                    str(_DEFAULT_CACHE_DEFAULT_TTL_SECONDS),
                )
            ),
            cache_max_entries=int(
                os.getenv(
                    "OPENJIUWEN_SERVICE_CACHE_MAX_ENTRIES",
                    str(_DEFAULT_CACHE_MAX_ENTRIES),
                )
            ),
            db_type=os.getenv("OPENJIUWEN_SERVICE_DB_TYPE", _DEFAULT_DB_TYPE).lower(),
            db_host=os.getenv("OPENJIUWEN_SERVICE_DB_HOST") or None,
            # 端口默认随 db_type：mysql 3306 / postgresql 5432（显式设置优先）
            db_port=int(os.getenv(
                "OPENJIUWEN_SERVICE_DB_PORT",
                "5432"
                if is_postgresql(
                    os.getenv("OPENJIUWEN_SERVICE_DB_TYPE", _DEFAULT_DB_TYPE)
                )
                else str(_DEFAULT_DB_PORT),
            )),
            db_name=os.getenv("OPENJIUWEN_SERVICE_DB_NAME") or None,
            db_user=os.getenv("OPENJIUWEN_SERVICE_DB_USER") or None,
            db_password=os.getenv("OPENJIUWEN_SERVICE_DB_PASSWORD"),
            pg_schema=os.getenv("OPENJIUWEN_SERVICE_PG_SCHEMA", "public"),
        )

    @property
    def etcd_configured(self) -> bool:
        return bool(self.etcd_endpoints)

    @property
    def redis_configured(self) -> bool:
        return bool(self.redis_url)

    @property
    def multi_replica(self) -> bool:
        return self.deploy_replicas > 1
