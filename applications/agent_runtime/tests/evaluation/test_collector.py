# coding: utf-8
"""ScopeTelemetryBuffer / EvaluationCollector 单测。"""

from __future__ import annotations

from types import SimpleNamespace

from fakeredis.aioredis import FakeRedis

from agent_runtime.evaluation.collector import (
    PHASE_ACTIVE,
    PHASE_DISABLED,
    PHASE_MISSING_RM_CFG,
    PHASE_ORPHAN_RM,
    EvaluationCollector,
    ScopeTelemetryBuffer,
)
from agent_runtime.evaluation.state import EvaluationState
from agent_runtime.resource_manager.state import ResourceState
from agent_runtime.session_manager.state import SessionState
from agent_runtime.session_manager.models import Template
from agent_runtime.session_manager.routing import RoutingSnapshot, RoutingScopeDef


# ------------------------------------------------------------ 缓冲


def test_buffer_observe_and_drain():
    buf = ScopeTelemetryBuffer()
    buf.observe_route("s1", True, None)
    buf.observe_route("s1", True, None)
    buf.observe_route("s1", False, "SCOPE_FULL")
    buf.observe_route("s1", False, "MYSTERY")       # 未知码 → other 桶
    buf.observe_acquire("s1", "reuse pod=x")        # 归一化首词
    buf.observe_acquire("s1", "deployed pod=y")
    buf.observe_acquire("s1", "")                   # 空 → error
    drained = buf.drain()
    assert drained["s1"] == {
        "route_total": 4, "route_ok": 2,
        "route_err_scope_full": 1, "route_err_other": 1,
        "acq_reuse": 1, "acq_deployed": 1, "acq_error": 1,
    }
    assert buf.drain() == {}                        # drain 后清零


def test_buffer_scope_cap_evicts_oldest(caplog):
    buf = ScopeTelemetryBuffer(max_scopes=3)
    for i in range(5):
        buf.observe_route(f"s{i}", True, None)
    drained = buf.drain()
    assert len(drained) == 3 and "s0" not in drained and "s4" in drained


def test_buffer_never_raises_on_bad_input():
    buf = ScopeTelemetryBuffer()
    buf.observe_route(None, True, None)             # scope_id=None 也不炸
    buf.observe_acquire("s1", None)
    assert isinstance(buf.drain(), dict)


# ------------------------------------------------------------ 采集器


def _snapshot(templates: dict, scopes: list):
    """routing_snapshot_view 的异步桩(生产实现是 async)。"""
    snap = RoutingSnapshot(ver=1, templates=templates, scopes=tuple(scopes))

    async def view():
        return snap
    return view


async def _make_collector(redis):
    sm_state = SessionState(redis)
    rm_state = ResourceState(redis)
    eval_state = EvaluationState(redis)
    store = SimpleNamespace(routing_snapshot_view=None)
    return EvaluationCollector(
        eval_state=eval_state, sm_state=sm_state, rm_state=rm_state,
        config_store=store,
    )


async def test_scope_inventory_union_and_phases():
    redis = FakeRedis()
    collector = await _make_collector(redis)
    tpl = Template(template_id="t1", enabled=True)
    active = RoutingScopeDef(scope_id="s-active", index=0, template_id="t1",
                             expr="", rule=None)
    disabled = RoutingScopeDef(scope_id="s-off", index=1, template_id="t1",
                               expr="", rule=None, enabled=False)
    collector.config.routing_snapshot_view = _snapshot(
        {"t1": tpl}, [active, disabled])

    # s-active 推 RM config → active;s-off 在快照禁用 → disabled;
    # s-ghost 只在 RM(config_sync drain 收敛推过 min_idle=0)→ orphan
    await redis.hset(
        "{resource_manager}:resource:scope:s-active:config",
        mapping={"min_idle_pods": "1", "max_pods": "2"},
    )
    await redis.hset(
        "{resource_manager}:resource:scope:s-off:config",
        mapping={"min_idle_pods": "0"},
    )
    await redis.hset(
        "{resource_manager}:resource:scope:s-ghost:config",
        mapping={"min_idle_pods": "0"},
    )

    rows = {r["scope_id"]: r for r in await collector.scope_inventory()}
    assert set(rows) == {"s-active", "s-off", "s-ghost"}
    assert rows["s-active"]["phase"] == PHASE_ACTIVE
    assert rows["s-off"]["phase"] == PHASE_DISABLED
    assert rows["s-ghost"]["phase"] == PHASE_ORPHAN_RM
    assert rows["s-active"]["template"] is tpl


async def test_scope_inventory_missing_rm_config():
    redis = FakeRedis()
    collector = await _make_collector(redis)
    tpl = Template(template_id="t1")
    scope = RoutingScopeDef(scope_id="s1", index=0, template_id="t1",
                            expr="", rule=None)
    collector.config.routing_snapshot_view = _snapshot({"t1": tpl}, [scope])
    rows = await collector.scope_inventory()
    assert rows[0]["phase"] == PHASE_MISSING_RM_CFG


async def test_sample_once_writes_counters_snapshot():
    redis = FakeRedis()
    collector = await _make_collector(redis)
    tpl = Template(template_id="t1", min_idle_pods=1)
    scope = RoutingScopeDef(scope_id="s1", index=0, template_id="t1",
                            expr="", rule=None)
    collector.config.routing_snapshot_view = _snapshot({"t1": tpl}, [scope])
    await redis.hset("{resource_manager}:resource:scope:s1:config",
                     mapping={"min_idle_pods": "1"})
    await collector.state.bump_counters("s1", {"route_total": 7, "route_ok": 7})

    await collector.sample_once()
    points = await collector.state.samples("s1", 0)
    assert len(points) == 1
    assert points[0]["rt"] == 7                     # 计数器快照入采样
    assert points[0]["i"] == 0 and points[0]["p"] == 0
