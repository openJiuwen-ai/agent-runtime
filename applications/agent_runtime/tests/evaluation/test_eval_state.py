# coding: utf-8
"""EvaluationState 单测(fakeredis):采样/计数/报告 + cluster 键形。"""

from __future__ import annotations

import pytest
from fakeredis.aioredis import FakeRedis

from agent_runtime.evaluation.state import (
    KEY_PREFIX,
    REPORT_HISTORY_MAX,
    EvaluationState,
)


@pytest.fixture
async def state():
    redis = FakeRedis()
    yield EvaluationState(redis)
    await redis.aclose()


def test_key_prefix_has_hash_tag():
    # cluster 兼容:eval 键域整体单槽(首段字面量 {agent_runtime:eval})
    assert KEY_PREFIX == "{agent_runtime:eval}"


def test_state_keys_shape():
    state = EvaluationState(redis=None)
    assert state.k.scope_samples("s1") == f"{KEY_PREFIX}:sample:scope:s1"
    assert state.k.scope_counters("s1") == f"{KEY_PREFIX}:ct:scope:s1"
    assert state.k.report_latest() == f"{KEY_PREFIX}:report:latest"
    assert state.k.report_history() == f"{KEY_PREFIX}:report:history"


async def test_bump_counters_accumulates(state):
    await state.bump_counters("s1", {"route_total": 2, "route_ok": 1})
    await state.bump_counters("s1", {"route_total": 3})
    counters = await state.read_counters("s1")
    assert counters["route_total"] == 5
    assert counters["route_ok"] == 1
    # TTL 有界(25h;防 scope 消失后残留)
    ttl = await state.redis.ttl(state.k.scope_counters("s1"))
    assert 0 < ttl <= 25 * 3600


async def test_bump_event_noop_without_scope(state):
    await state.bump_event("", "ev_reclaimed")     # 无 scope:静默 noop
    assert await state.read_counters("noscope") == {}


async def test_samples_window_and_corrupt_member(state):
    for i in range(5):
        await state.add_sample("s1", 1_000_000 + i * 30,
                               {"t": 1_000_000 + i * 30, "s": i})
    points = await state.samples("s1", 1_000_060)
    assert [p["s"] for p in points] == [2, 3, 4]   # 升序,窗口过滤
    # 坏 member(手工塞非 JSON)被跳过不炸
    await state.redis.zadd(state.k.scope_samples("s1"), {"not-json": 1.0})
    points = await state.samples("s1", 0)
    assert all(isinstance(p, dict) for p in points)


async def test_report_roundtrip_and_history_cap(state):
    for i in range(REPORT_HISTORY_MAX + 5):
        await state.write_report({
            "generated_at": 1_000_000 + i,
            "instance_id": f"inst-{i}",
            "llm": {"status": "ok"},
            "summary": {"findings_total": i},
            "findings": [{"id": f"f{i}"}],
        })
    latest = await state.latest_report()
    assert latest["instance_id"] == f"inst-{REPORT_HISTORY_MAX + 4}"
    history = await state.list_reports(500)
    assert len(history) == REPORT_HISTORY_MAX      # 容量裁剪
    assert history[0]["instance_id"] == f"inst-{REPORT_HISTORY_MAX + 4}"
    assert "findings" not in history[0]            # 瘦身条目


async def test_latest_corrupt_returns_none(state):
    await state.redis.set(state.k.report_latest(), "not-json")
    assert await state.latest_report() is None
