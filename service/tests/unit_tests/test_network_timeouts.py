# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""网络抖动兜底测试：redis socket 超时 / DB 建连超时 / 周期任务 tick 超时。

redis-py 与 aiomysql 默认均无 socket 级超时（TCP 半开/黑洞时 await 永久挂起），
此处锁定 bootstrap / engine_options / JobRunner 注入的兜底参数不被回归。
"""

from __future__ import annotations

import asyncio
import time

import pytest

import openjiuwen_runtime.foundation.db.sqlalchemy_handler as sa_handler_mod
from openjiuwen_runtime.foundation.db.engine_options import (
    get_command_timeout,
    get_connect_timeout,
)
from openjiuwen_runtime.service import ServiceConfig, build_redis_client
from openjiuwen_runtime.service.context.periodic.runner import JobRunner
from openjiuwen_runtime.service.context.periodic.schedule.interval import (
    IntervalSchedule,
)


# ---------------------------------------------------------------- redis 客户端


@pytest.mark.unit
def test_build_redis_client_injects_socket_timeouts_by_default():
    client = build_redis_client(ServiceConfig(redis_url="redis://127.0.0.1:6379/0"))

    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == pytest.approx(3.0)
    assert kwargs["socket_timeout"] == pytest.approx(5.0)
    assert kwargs["health_check_interval"] == 30
    assert kwargs["decode_responses"] is False
    # 连接类错误命令级重试（连接错误 + socket 超时）
    assert kwargs["retry_on_error"]
    assert kwargs["retry"] is not None


@pytest.mark.unit
def test_build_redis_client_zero_disables_all_timeouts():
    client = build_redis_client(
        ServiceConfig(
            redis_url="redis://127.0.0.1:6379/0",
            redis_socket_connect_timeout_seconds=0,
            redis_socket_timeout_seconds=0,
            redis_health_check_interval_seconds=0,
            redis_retry_attempts=0,
        )
    )

    kwargs = client.connection_pool.connection_kwargs
    assert "socket_connect_timeout" not in kwargs
    assert "socket_timeout" not in kwargs
    assert "health_check_interval" not in kwargs
    assert "retry_on_error" not in kwargs


@pytest.mark.unit
def test_redis_timeout_config_reads_from_env(monkeypatch):
    monkeypatch.setenv(
        "OPENJIUWEN_SERVICE_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS", "2"
    )
    monkeypatch.setenv("OPENJIUWEN_SERVICE_REDIS_SOCKET_TIMEOUT_SECONDS", "8")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_REDIS_HEALTH_CHECK_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_REDIS_RETRY_ATTEMPTS", "5")

    config = ServiceConfig.from_env()

    assert config.redis_socket_connect_timeout_seconds == pytest.approx(2.0)
    assert config.redis_socket_timeout_seconds == pytest.approx(8.0)
    assert config.redis_health_check_interval_seconds == 15
    assert config.redis_retry_attempts == 5


@pytest.mark.unit
@pytest.mark.parametrize(
    "field",
    [
        "redis_socket_connect_timeout_seconds",
        "redis_socket_timeout_seconds",
        "redis_health_check_interval_seconds",
        "redis_retry_attempts",
    ],
)
def test_redis_timeout_config_rejects_negative_values(field):
    with pytest.raises(ValueError, match=field):
        ServiceConfig(**{field: -1})


# ---------------------------------------------------------------- DB 建连超时


def _capture_engine_kwargs(monkeypatch) -> dict:
    """替换 create_async_engine，捕获 SQLAlchemyHandler.connect 的入参。"""
    captured: dict = {}

    def fake_create_async_engine(url, **kwargs):  # noqa: ANN001, ANN202
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(sa_handler_mod, "create_async_engine", fake_create_async_engine)
    return captured


@pytest.mark.unit
async def test_mysql_engine_kwargs_include_connect_timeout(monkeypatch):
    captured = _capture_engine_kwargs(monkeypatch)

    handler = sa_handler_mod.SQLAlchemyHandler(
        "mysql+aiomysql://user:pw@127.0.0.1:3306/svc"
    )
    await handler.connect()

    assert captured["kwargs"]["connect_args"]["connect_timeout"] == get_connect_timeout()


@pytest.mark.unit
async def test_mysql_explicit_connect_args_take_precedence(monkeypatch):
    captured = _capture_engine_kwargs(monkeypatch)

    handler = sa_handler_mod.SQLAlchemyHandler(
        "mysql+aiomysql://user:pw@127.0.0.1:3306/svc",
        connect_args={"connect_timeout": 9},
    )
    await handler.connect()

    assert captured["kwargs"]["connect_args"]["connect_timeout"] == 9


@pytest.mark.unit
async def test_postgresql_engine_kwargs_include_timeouts(monkeypatch):
    """asyncpg：建连 timeout + 命令 command_timeout 均注入（MySQL 同款兜底）。"""
    captured = _capture_engine_kwargs(monkeypatch)

    handler = sa_handler_mod.SQLAlchemyHandler(
        "postgresql+asyncpg://user:pw@127.0.0.1:5432/svc"
    )
    await handler.connect()

    connect_args = captured["kwargs"]["connect_args"]
    assert connect_args["timeout"] == get_connect_timeout()
    assert connect_args["command_timeout"] == get_command_timeout()
    # connect_timeout 是 mysql 系驱动专有 kwarg，不注入 asyncpg
    assert "connect_timeout" not in connect_args


@pytest.mark.unit
async def test_postgresql_explicit_connect_args_take_precedence(monkeypatch):
    captured = _capture_engine_kwargs(monkeypatch)

    handler = sa_handler_mod.SQLAlchemyHandler(
        "postgresql+asyncpg://user:pw@127.0.0.1:5432/svc",
        connect_args={"timeout": 9},
    )
    await handler.connect()

    connect_args = captured["kwargs"]["connect_args"]
    assert connect_args["timeout"] == 9
    assert connect_args["command_timeout"] == get_command_timeout()


@pytest.mark.unit
async def test_sqlite_backend_not_injected(monkeypatch):
    captured = _capture_engine_kwargs(monkeypatch)

    handler = sa_handler_mod.SQLAlchemyHandler("sqlite+aiosqlite:////tmp/svc.db")
    await handler.connect()

    connect_args = captured["kwargs"]["connect_args"]
    assert "timeout" not in connect_args
    assert "command_timeout" not in connect_args
    assert "connect_timeout" not in connect_args


@pytest.mark.unit
def test_command_timeout_reads_from_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_DB_COMMAND_TIMEOUT", "17")
    from openjiuwen_runtime.foundation.db.engine_options import get_command_timeout

    assert get_command_timeout() == 17


# ---------------------------------------------------------------- postgresql 接入


@pytest.mark.unit
def test_build_db_handler_postgresql():
    """DB_TYPE=postgresql 经 bootstrap 构造 PostgreSQLHandler（无需 asyncpg 运行时）。"""
    from openjiuwen_runtime.foundation.db import PostgreSQLHandler
    from openjiuwen_runtime.service import ServiceConfig, build_db_handler

    handler = build_db_handler(ServiceConfig(
        db_type="postgresql", db_host="pg.example", db_name="svc",
        db_user="svc", db_password="pw", db_port=5432,
    ))
    assert isinstance(handler, PostgreSQLHandler)
    assert handler.database_url.startswith("postgresql+asyncpg://")
    # 建连/命令超时在 connect() 时注入（见上方 engine kwargs 用例）


@pytest.mark.unit
def test_postgresql_config_requires_same_fields_as_mysql():
    from openjiuwen_runtime.service import ServiceConfig

    with pytest.raises(ValueError, match="db_host is required"):
        ServiceConfig(db_type="postgresql", db_name="svc", db_user="svc")


@pytest.mark.unit
def test_postgresql_default_port_5432_mysql_3306(monkeypatch):
    from openjiuwen_runtime.service import ServiceConfig

    monkeypatch.delenv("OPENJIUWEN_SERVICE_DB_PORT", raising=False)
    for field in ("DB_HOST", "DB_NAME", "DB_USER"):
        monkeypatch.setenv(f"OPENJIUWEN_SERVICE_{field}", "x")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_DB_TYPE", "postgresql")
    assert ServiceConfig.from_env().db_port == 5432

    monkeypatch.setenv("OPENJIUWEN_SERVICE_DB_TYPE", "mysql")
    assert ServiceConfig.from_env().db_port == 3306


# ---------------------------------------------------------------- tick 超时


class _StubCoordinator:
    """永远抢到锁的协调器桩：只验证 tick 执行路径。"""

    def __init__(self) -> None:
        self.released = False

    async def try_claim(self, *, now, instance_id, planned_fire):  # noqa: ANN001, ANN202
        return object()

    async def release(self, claim) -> None:  # noqa: ANN001
        self.released = True


@pytest.mark.unit
async def test_job_runner_tick_timeout_aborts_stuck_tick():
    """on_tick 内 IO await 永久挂起时，tick_timeout_sec 取消本拍而不是拖死循环。"""
    started = asyncio.Event()

    async def stuck_tick() -> None:
        started.set()
        await asyncio.sleep(60)

    runner = JobRunner(
        name="stuck-job",
        schedule=IntervalSchedule(1),
        coordinator=_StubCoordinator(),
        on_tick=stuck_tick,
        instance_id="test-instance",
        redis=None,
        tick_timeout_sec=0.05,
    )

    t0 = time.monotonic()
    await runner._safe_tick(planned_fire=0.0, now=0.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 5  # 没有被 60s sleep 拖住
    assert started.is_set()  # tick 确实进入过（是超时取消，不是没执行）
    assert runner._coordinator.released is True  # 超时路径也放锁


@pytest.mark.unit
async def test_job_runner_tick_timeout_none_keeps_waiting():
    """默认 None 不限制：维持既有行为（长 tick 不被打断）。"""
    async def slow_but_ok_tick() -> None:
        await asyncio.sleep(0.05)

    runner = JobRunner(
        name="default-job",
        schedule=IntervalSchedule(1),
        coordinator=_StubCoordinator(),
        on_tick=slow_but_ok_tick,
        instance_id="test-instance",
        redis=None,
    )

    await runner._safe_tick(planned_fire=0.0, now=0.0)  # 正常完成不抛


@pytest.mark.unit
async def test_job_runner_tick_timeout_spares_normal_tick():
    """超时只裁挂死：正常完成的 tick 不受短超时外的影响（这里给足余量）。"""
    async def quick_tick() -> None:
        await asyncio.sleep(0.01)

    runner = JobRunner(
        name="quick-job",
        schedule=IntervalSchedule(1),
        coordinator=_StubCoordinator(),
        on_tick=quick_tick,
        instance_id="test-instance",
        redis=None,
        tick_timeout_sec=5.0,
    )

    await runner._safe_tick(planned_fire=0.0, now=0.0)
