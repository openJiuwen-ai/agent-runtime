# coding: utf-8
"""基础设施故障注入测试(2026-09 健壮性加固):Redis/DB 本身坏掉时的错误契约。

现有测试几乎全部是「依赖正常 + 业务出错」;本文件补「依赖故障」——
- Redis 连接级异常 → 503 STATE_UNAVAILABLE + retry_after=1(而非 internal 500,
  LB/客户端重试语义依赖这一区分);
- DB 连接级异常 → 同上;
- 幂等缓存写失败不吞已成功的路由结果。
"""

from __future__ import annotations

import types
from uuid import uuid4

import pytest

from agent_runtime.session_manager.handlers import (
    handle_config_sync,
    handle_route,
    handle_touch,
)
from openjiuwen_runtime.service.envelope import Envelope, Metadata
from tests.conftest import requires_lua, split_sync_payload


def _envelope(msg_type: str, *, session_id="sess-fault", rawdata=None) -> Envelope:
    return Envelope(
        type=msg_type,
        metadata=Metadata(
            request_id=f"req-{uuid4().hex[:8]}", session_id=session_id,
            user_id="u", bot_id="b", extra={"group_id": "g"}),
        rawdata=rawdata or {},
    )


class _AcquiredGuard:
    """恒 acquired 的幂等闸替身;succeed 可编程抛错(考验容错)。"""

    acquired = True
    cached_result = None

    def __init__(self, succeed_exc: BaseException | None = None) -> None:
        self.succeed_exc = succeed_exc

    async def succeed(self, response) -> None:
        if self.succeed_exc is not None:
            raise self.succeed_exc


class _StubIdempotency:
    def __init__(self, guard: _AcquiredGuard) -> None:
        self._guard = guard

    async def acquire(self, request_id: str, window: int) -> _AcquiredGuard:
        return self._guard


def _ctx(runtime, guard: _AcquiredGuard) -> types.SimpleNamespace:
    """handle_* 需要的最小 ctx(sysctx 三件套 + idempotency)。"""
    return types.SimpleNamespace(
        sysctx=types.SimpleNamespace(
            sm_orchestrator=runtime.orchestrator,
            sm_config_store=runtime.config_store,
            rm_facade=runtime.rm_facade,
        ),
        idempotency=_StubIdempotency(guard),
    )


def _assert_state_unavailable(resp) -> None:
    assert resp.ok is False
    assert resp.error_code == "STATE_UNAVAILABLE"
    assert resp.retry_after == 1


@requires_lua
async def test_route_returns_503_envelope_on_redis_outage(runtime, monkeypatch):
    """Redis 连接级故障(EVAL 抛 ConnectionError)→ 503 STATE_UNAVAILABLE 信封。"""
    from redis.exceptions import ConnectionError as RedisConnectionError

    await runtime.seed_template()

    async def _boom_eval(*args, **kwargs):
        raise RedisConnectionError("connection lost")

    monkeypatch.setattr(runtime.redis, "eval", _boom_eval)
    resp = await handle_route(_ctx(runtime, _AcquiredGuard()),
                              _envelope("route", session_id="sess-outage"))
    _assert_state_unavailable(resp)
    assert "ConnectionError" in resp.error_message


@requires_lua
async def test_touch_returns_503_envelope_on_redis_outage(runtime, monkeypatch):
    from redis.exceptions import TimeoutError as RedisTimeoutError

    async def _boom_eval(*args, **kwargs):
        raise RedisTimeoutError("timeout")

    monkeypatch.setattr(runtime.redis, "eval", _boom_eval)
    resp = await handle_touch(_ctx(runtime, _AcquiredGuard()),
                              _envelope("touch", session_id="sess-outage"))
    _assert_state_unavailable(resp)


@requires_lua
async def test_config_sync_returns_503_on_db_outage(
        runtime, db_handler, monkeypatch):
    """DB 连接级故障 → 503 STATE_UNAVAILABLE(读旧态 list_records 即失败)。"""
    from sqlalchemy.exc import OperationalError

    await runtime.seed_template()

    async def _boom(*args, **kwargs):
        raise OperationalError("stmt", None, RuntimeError("db down"))

    monkeypatch.setattr(db_handler, "list_records", _boom)
    resp = await handle_config_sync(
        _ctx(runtime, _AcquiredGuard()),
        _envelope("config_sync", rawdata=split_sync_payload(
            [{"template_id": "tpl-fault", "agent_image": "agentserver:1.0",
              "namespace": "default", "scope_concurrency": 3, "pod_concurrency": 2,
              "session_ttl": 60, "pod_ttl": 300, "min_idle_pods": 0}],
            [{"scope_id": "scope-fault", "index": 0,
              "template_id": "tpl-fault", "routing_rules": ""}],
        )),
    )
    _assert_state_unavailable(resp)


@requires_lua
async def test_route_success_survives_idempotency_write_failure(runtime, caplog):
    """幂等缓存写失败(guard.succeed 抛)→ 成功响应照常返回,不吞成异常。"""
    await runtime.seed_template()
    ctx = _ctx(runtime, _AcquiredGuard(succeed_exc=RuntimeError("cache write lost")))

    result = await handle_route(ctx, _envelope("route", session_id="sess-idem"))
    assert result["pod_sse_url"].startswith("http://")
    assert result["pod_id"].startswith("agentserver-")
    assert any("idempotency cache write failed" in r.message
               for r in caplog.records), "写失败须留痕(exception 日志)"
