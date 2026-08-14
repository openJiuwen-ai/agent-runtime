# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Configuration and process resource bootstrap tests."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from openjiuwen_runtime.foundation.db import MySQLHandler, SQLiteHandler
from openjiuwen_runtime.foundation.db.table_def import ColumnDefinition, TableDefinition
from openjiuwen_runtime.service import (
    CacheUnavailable,
    MemoryLockBackend,
    ServiceConfig,
    build_db_handler,
    build_system_context,
)
from openjiuwen_runtime.service.context.locks import LockCapabilities
from openjiuwen_runtime.service.context.system_context import SystemContext
from openjiuwen_runtime.service.envelope import Metadata


@pytest.mark.unit
def test_extended_config_parses_endpoints_and_resource_fields(monkeypatch):
    monkeypatch.setenv(
        "OPENJIUWEN_SERVICE_ETCD_ENDPOINTS",
        "https://etcd-a:2379,etcd-b:22379",
    )
    monkeypatch.setenv("OPENJIUWEN_SERVICE_LOCK_BACKEND", "etcd")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_CACHE_BACKEND", "redis")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_DEPLOY_REPLICAS", "2")

    config = ServiceConfig.from_env()

    assert config.etcd_endpoints == ("https://etcd-a:2379", "etcd-b:22379")
    assert config.lock_backend == "etcd"
    assert config.cache_backend == "redis"
    assert config.multi_replica is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"lock_backend": "etcd"}, "etcd_endpoints"),
        ({"deploy_replicas": 2, "lock_backend": "memory"}, "multiple replicas"),
        ({"db_type": "mysql"}, "db_host"),
        ({"cache_backend": "redis", "redis_url": ""}, "redis_url"),
        ({"lock_renew_ratio": 1.1}, "less than or equal"),
    ],
)
def test_extended_config_rejects_incomplete_or_out_of_range_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        ServiceConfig(**kwargs)


@pytest.mark.unit
def test_db_builder_creates_sqlite_and_mysql_handlers():
    sqlite = build_db_handler(
        ServiceConfig(
            redis_url="",
            lock_backend="memory",
            db_type="sqlite",
            db_name=":memory:",
        )
    )
    mysql = build_db_handler(
        ServiceConfig(
            db_type="mysql",
            db_host="mysql.internal",
            db_name="service",
            db_user="service_user",
            db_password="secret",
        )
    )

    assert isinstance(sqlite, SQLiteHandler)
    assert isinstance(mysql, MySQLHandler)
    assert mysql.host == "mysql.internal"
    assert mysql.database == "service"


@pytest.mark.unit
async def test_system_builder_starts_sqlite_memory_resources_and_reports_readiness():
    db_path = Path(__file__).parent / f".bootstrap-{uuid4().hex}.db"
    config = ServiceConfig(
        redis_url="",
        lock_backend="memory",
        cache_backend="memory",
        db_type="sqlite",
        db_name=str(db_path),
    )
    table = TableDefinition(
        table_name="bootstrap_records",
        columns=[ColumnDefinition("id", "integer", primary_key=True)],
    )
    system = build_system_context(config, table_definitions=[table])

    assert isinstance(system.db, SQLiteHandler)
    assert isinstance(system.lock_backend, MemoryLockBackend)
    await system.start()
    try:
        assert system.db.is_table_registered("bootstrap_records") is True
        assert await system.readiness() == {
            "db": True,
            "redis": None,
            "kubernetes": None,
            "lock": True,
            "cache": True,
            "ready": True,
        }
    finally:
        await system.stop()
        db_path.unlink(missing_ok=True)


@pytest.mark.unit
async def test_lock_defaults_are_taken_from_service_config():
    config = ServiceConfig(
        redis_url="",
        lock_backend="memory",
        lock_ttl_seconds=0.2,
        lock_wait_seconds=0.15,
    )
    system = build_system_context(config)
    request = system.for_request(Metadata(request_id="config-lock"))

    assert request.locks.default_ttl == pytest.approx(0.2)
    assert request.locks.default_wait_timeout == pytest.approx(0.15)
    await request.close()


@pytest.mark.unit
async def test_stop_closes_constructed_resources_even_before_start():
    config = ServiceConfig(redis_url="", lock_backend="memory")
    system = build_system_context(config)
    cache = system.cache_backend
    lock = system.lock_backend

    await system.stop()

    with pytest.raises(CacheUnavailable, match="cache backend is closed"):
        await cache.ping()
    assert await lock.ping() is False


@pytest.mark.unit
async def test_start_failure_closes_owned_resources_in_reverse_order():
    events: list[str] = []

    class Db:
        async def init_database(self):
            events.append("db.init")

        async def connect(self):
            events.append("db.connect")

        async def ping(self):
            events.append("db.ping")
            return True

        async def disconnect(self):
            events.append("db.close")

    class Redis:
        async def ping(self):
            events.append("redis.ping")
            return True

        async def aclose(self):
            events.append("redis.close")

    class Lock:
        capabilities = LockCapabilities(distributed=True, fencing=False)

        async def ping(self):
            events.append("lock.ping")
            return True

        async def close(self):
            events.append("lock.close")

    class Kubernetes:
        async def start(self):
            events.append("kubernetes.start")

        async def ping(self):
            events.append("kubernetes.ping")
            return True

        async def close(self):
            events.append("kubernetes.close")

    class Cache:
        async def ping(self):
            events.append("cache.ping")
            raise RuntimeError("cache offline")

        async def close(self):
            events.append("cache.close")

    system = SystemContext(
        db=Db(),
        redis=Redis(),
        kubernetes=Kubernetes(),
        lock_backend=Lock(),
        cache_backend=Cache(),
        settings=ServiceConfig(deploy_replicas=2),
        _owns_db=True,
        _owns_redis=True,
        _owns_kubernetes=True,
        _owns_lock_backend=True,
        _owns_cache_backend=True,
    )

    with pytest.raises(RuntimeError, match="cache offline"):
        await system.start()

    assert events == [
        "db.init",
        "db.connect",
        "db.ping",
        "redis.ping",
        "kubernetes.start",
        "kubernetes.ping",
        "lock.ping",
        "cache.ping",
        "cache.close",
        "lock.close",
        "kubernetes.close",
        "redis.close",
        "db.close",
    ]
