# coding: utf-8
"""MetricsRegistry 单元测试：计数算术 / 错误分桶 / 延迟窗口截断 / 环形容量。"""

from __future__ import annotations

from agent_runtime.metrics import MetricsRegistry


def test_counters_and_error_buckets():
    reg = MetricsRegistry()
    reg.observe_request(endpoint="route", ok=True, error_code=None, duration_ms=5)
    reg.observe_request(endpoint="route", ok=True, error_code=None, duration_ms=7)
    reg.observe_request(endpoint="route", ok=False, error_code="SCOPE_FULL_TIMEOUT",
                        duration_ms=30000, request_id="r1", session_id="s1",
                        detail="waited")
    reg.observe_request(endpoint="touch", ok=True, error_code=None, duration_ms=1)

    snap = reg.snapshot()
    assert snap["requests"] == {"total": 4, "ok": 3, "error": 1}
    route = snap["endpoints"]["route"]
    assert route["total"] == 3 and route["ok"] == 2 and route["error"] == 1
    assert route["by_error_code"] == {"SCOPE_FULL_TIMEOUT": 1}
    assert route["max_ms"] == 30000
    assert "touch" in snap["endpoints"]

    errors = reg.recent_errors()
    assert len(errors) == 1
    assert errors[0]["error_code"] == "SCOPE_FULL_TIMEOUT"
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
