# coding: utf-8
"""MetricsRegistry 单元测试：计数算术 / 错误分桶 / 延迟窗口截断 / 环形容量。"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from types import SimpleNamespace

from agent_runtime import metrics
from agent_runtime.metrics import MetricsRegistry, request_metrics_middleware


def test_counters_and_error_buckets():
    reg = MetricsRegistry()
    reg.observe_request(endpoint="route", ok=True, error_code=None, duration_ms=5)
    reg.observe_request(endpoint="route", ok=True, error_code=None, duration_ms=7)
    reg.observe_request(endpoint="route", ok=False, error_code="SCOPE_FULL",
                        duration_ms=5, request_id="r1", session_id="s1",
                        detail="full")
    reg.observe_request(endpoint="touch", ok=True, error_code=None, duration_ms=1)

    snap = reg.snapshot()
    assert snap["requests"] == {"total": 4, "ok": 3, "error": 1}
    route = snap["endpoints"]["route"]
    assert route["total"] == 3 and route["ok"] == 2 and route["error"] == 1
    assert route["by_error_code"] == {"SCOPE_FULL": 1}
    assert route["max_ms"] == 7
    assert "touch" in snap["endpoints"]

    errors = reg.recent_errors()
    assert len(errors) == 1
    assert errors[0]["error_code"] == "SCOPE_FULL"
    assert errors[0]["request_id"] == "r1" and errors[0]["session_id"] == "s1"


def test_latency_window_trimming():
    reg = MetricsRegistry(latency_window=4)
    for i in range(10):
        reg.observe_request(endpoint="route", ok=True, error_code=None,
                            duration_ms=float(i))
    # 窗口 4：只保留最近 4 个样本（6,7,8,9）→ 分位数/max 不受早期样本影响
    snap = reg.snapshot()["endpoints"]["route"]
    assert snap["max_ms"] == 9.0
    assert snap["p50_ms"] == 8.0    # idx=int(4*0.5)=2 → 样本[6,7,8,9][2]
    assert snap["p95_ms"] == 9.0


def test_recent_errors_capacity_and_order():
    reg = MetricsRegistry(error_capacity=3)
    for i in range(5):
        reg.observe_request(endpoint="route", ok=False, error_code="E",
                            duration_ms=1.0, request_id=f"r{i}")
    errors = reg.recent_errors()
    assert len(errors) == 3                    # 环形容量
    assert [e["request_id"] for e in errors] == ["r4", "r3", "r2"]  # 新在前
    assert reg.recent_errors(limit=1)[0]["request_id"] == "r4"
    assert reg.recent_errors(limit=0) == []


# ---------------------------------------------------------------- 中间件汇总/慢请求

class _FakeClock:
    """假单调钟：nxt 内推进模拟处理耗时（真 sleep 会拖慢测试）。"""

    def __init__(self) -> None:
        self.now = 0.0

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(
            metrics, "time",
            SimpleNamespace(monotonic=lambda: self.now, time=_time.time),
        )


def _drive_middleware(clock, advance_s: float):
    """走一遍中间件：nxt 推进假钟 advance_s 后成功返回。"""
    middleware = request_metrics_middleware(MetricsRegistry())
    env = SimpleNamespace(type="route",
                          metadata=SimpleNamespace(request_id="r1", session_id="s1"))
    ctx = SimpleNamespace(sysctx=SimpleNamespace(instance_id="i1"))

    async def nxt(ctx_, env_):
        clock.now += advance_s
        return SimpleNamespace(response=SimpleNamespace(
            ok=True, error_code=None, error_message=""))

    return asyncio.run(middleware(ctx, env, nxt))


def test_middleware_slow_request_warning(monkeypatch, caplog):
    """超阈值 → INFO 汇总 + WARNING 分诊行；阈值内 → 只有汇总。"""
    clock = _FakeClock()
    clock.install(monkeypatch)
    with caplog.at_level(logging.INFO, logger="agent_runtime.metrics"):
        result = _drive_middleware(clock, advance_s=metrics._SLOW_REQUEST_MS / 1000 + 1)
        assert result.response.ok is True
        messages = [r.getMessage() for r in caplog.records]
        assert any("request: endpoint=route outcome=ok" in m for m in messages)
        assert any("request slow: endpoint=route outcome=ok" in m for m in messages)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="agent_runtime.metrics"):
        _drive_middleware(clock, advance_s=0.1)
        messages = [r.getMessage() for r in caplog.records]
        assert any("request: endpoint=route" in m for m in messages)
        assert not any("request slow" in m for m in messages)
