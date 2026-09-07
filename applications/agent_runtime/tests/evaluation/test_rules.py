# coding: utf-8
"""规则评估引擎单测(纯函数;静态/动态逐条触发与不触发边界)。"""

from __future__ import annotations

from datetime import timedelta

from agent_runtime.evaluation.rules import (
    Finding,
    RuleThresholds,
    ScopeConfigView,
    ServiceView,
    _reclaim_redeploy_pairs,
    dynamic_rules,
    static_rules,
)
from agent_runtime.util import utc_now


def make_view(**over) -> ScopeConfigView:
    """默认一个健康 active scope;测试覆写个别字段。"""
    base = dict(
        scope_id="s1", template_id="t1", scope_enabled=True, expires_at=None,
        template_enabled=True, scope_concurrency=3, pod_concurrency=2,
        session_ttl=60, pod_ttl=300, min_idle_pods=1, max_pods=2,
        phase="active", rm_min_idle=1,
    )
    base.update(over)
    return ScopeConfigView(**base)


def ids(findings: list[Finding]) -> list[str]:
    return [f.id for f in findings]


# ------------------------------------------------------------ 静态规则


def test_static_min_idle_exceeds_max_pods_is_critical():
    findings = static_rules(
        [make_view(min_idle_pods=3, max_pods=2)], ServiceView()
    )
    assert "S-CONTRADICTION-MIN-IDLE" in ids(findings)
    f = next(x for x in findings if x.id == "S-CONTRADICTION-MIN-IDLE")
    assert f.severity == "critical"
    assert f.change_class == "B"


def test_static_min_idle_equal_max_pods_not_flagged():
    assert "S-CONTRADICTION-MIN-IDLE" not in ids(static_rules(
        [make_view(min_idle_pods=2, max_pods=2)], ServiceView()))


def test_static_ceil_waste():
    assert "S-CEIL-WASTE" in ids(static_rules(
        [make_view(scope_concurrency=3, pod_concurrency=2, max_pods=2)],
        ServiceView()))
    assert "S-CEIL-WASTE" not in ids(static_rules(
        [make_view(scope_concurrency=4, pod_concurrency=2, max_pods=2)],
        ServiceView()))


def test_static_pod_budget_disabled_when_zero():
    views = [make_view(scope_id=f"s{i}", min_idle_pods=5) for i in range(5)]
    assert "S-POD-BUDGET" not in ids(static_rules(
        views, ServiceView(pod_budget=0)))


def test_static_pod_budget_over():
    views = [make_view(scope_id=f"s{i}", min_idle_pods=5) for i in range(5)]
    findings = static_rules(
        views, ServiceView(pod_budget=20))
    f = next(x for x in findings if x.id == "S-POD-BUDGET")
    assert "sum_min_idle=25" in f.evidence


def test_static_disabled_template_reference():
    findings = static_rules(
        [make_view(template_enabled=False, phase="disabled")],
        ServiceView())
    assert "S-DISABLED-TEMPLATE-REF" in ids(findings)


def test_static_scope_expiry_soon_and_past():
    now = utc_now()
    soon = static_rules(
        [make_view(expires_at=now + timedelta(hours=2), phase="disabled")],
        ServiceView())
    assert "S-SCOPE-EXPIRY" in ids(soon)
    past = static_rules(
        [make_view(expires_at=now - timedelta(hours=1), phase="disabled")],
        ServiceView())
    assert "S-SCOPE-EXPIRY" in ids(past)
    far = static_rules(
        [make_view(expires_at=now + timedelta(days=30), phase="disabled")],
        ServiceView())
    assert "S-SCOPE-EXPIRY" not in ids(far)


def test_static_orphan_phantom_warmup():
    findings = static_rules(
        [make_view(phase="orphan_rm", rm_min_idle=2, scope_enabled=False,
                   template_enabled=False)],
        ServiceView())
    f = next(x for x in findings if x.id == "S-RM-ORPHAN-CONFIG")
    assert f.severity == "warn"
    # min_idle=0 → info 残留
    findings = static_rules(
        [make_view(phase="orphan_rm", rm_min_idle=0, scope_enabled=False,
                   template_enabled=False)],
        ServiceView())
    f = next(x for x in findings if x.id == "S-RM-ORPHAN-CONFIG")
    assert f.severity == "info"


def test_static_missing_rm_config():
    findings = static_rules(
        [make_view(phase="missing_rm_cfg")], ServiceView())
    assert "S-RM-MISSING-CONFIG" in ids(findings)


def test_static_healthy_scope_produces_no_findings():
    assert static_rules(
        [make_view(scope_concurrency=4, pod_concurrency=2, max_pods=2,
                   session_ttl=300, min_idle_pods=1)],
        ServiceView(),
    ) == []


# ------------------------------------------------------------ 动态规则


def _samples(points: list[tuple[int, dict]]) -> list[dict]:
    """手搓采样序列:[(t, 字段覆盖)] → 升序 dict 列表。"""
    base_keys = ("p", "i", "d", "s", "w", "rt", "ef", "eq", "en", "ad", "ar",
                 "rc", "dd")
    out = []
    for t, over in points:
        rec = {"t": t}
        for k in base_keys:
            rec[k] = over.get(k, 0)
        out.append(rec)
    return out


def test_dynamic_saturation_triggers():
    # 1h 窗口 40 个采样,全部 sessions=3(=sc,顶格)
    samples = _samples([
        (1_000_000 + i * 90, {"s": 3, "rt": i + 1, "i": 0, "p": 2})
        for i in range(40)
    ])
    findings = dynamic_rules(make_view(), samples)
    assert "D-CONCURRENCY-SATURATION" in ids(findings)


def test_dynamic_saturation_not_triggered_low_usage():
    samples = _samples([
        (1_000_000 + i * 90, {"s": 1, "rt": i + 1}) for i in range(40)
    ])
    assert "D-CONCURRENCY-SATURATION" not in ids(dynamic_rules(make_view(), samples))


def test_dynamic_capacity_errors():
    # 1h 窗口 40 个采样,ef 从 0 涨到 10(≈10/h > 6/h)
    samples = _samples([
        (1_000_000 + i * 90, {"s": 3, "rt": i + 1, "ef": min(i // 4, 10)})
        for i in range(40)
    ])
    findings = dynamic_rules(make_view(), samples)
    f = next(x for x in findings if x.id == "D-CAPACITY-ERRORS")
    assert f.field == "scope_concurrency"
    assert "联动" not in f.rationale   # pods 未长期顶格时不注 max_pods 联动


def test_dynamic_cold_start_rate():
    samples = _samples([
        (1_000_000, {"ad": 0, "ar": 0, "rt": 10}),
        (1_000_360, {"ad": 8, "ar": 2, "rt": 100}),   # 80% 冷启动
    ])
    findings = dynamic_rules(make_view(), samples)
    f = next(x for x in findings if x.id == "D-COLD-START-RATE")
    assert f.field == "min_idle_pods"


def test_dynamic_warm_pool_slack():
    # 2h 窗口 70 个采样:暖池常满(min_idle=1)、低负载、零容量错误
    samples = _samples([
        (1_000_000 + i * 100, {"s": 0, "i": 1, "rt": i + 1})
        for i in range(70)
    ])
    findings = dynamic_rules(make_view(min_idle_pods=1), samples)
    f = next(x for x in findings if x.id == "D-WARM-POOL-SLACK")
    assert f.suggested == "0"


def test_dynamic_inactive_scope_skipped():
    assert dynamic_rules(make_view(phase="disabled"), _samples([
        (1_000_000, {"s": 3}),
    ])) == []


def test_reclaim_redeploy_pairs():
    samples = _samples([
        (1_000_000, {"rc": 0, "ad": 0}),
        (1_000_100, {"rc": 1, "ad": 0}),        # 回收
        (1_000_400, {"rc": 1, "ad": 1}),        # 300s 内重建 → churn 对
        (1_000_500, {"rc": 2, "ad": 1}),
        (1_000_800, {"rc": 2, "ad": 2}),        # 300s 内 → 对
        (1_000_900, {"rc": 3, "ad": 2}),
        (1_002_000, {"rc": 3, "ad": 3}),        # 1100s 外 → 不对
    ])
    assert _reclaim_redeploy_pairs(samples, 600) == 2


def test_dynamic_pod_ttl_churn():
    samples = _samples([
        (1_000_000 + i * 200, {"rc": i, "ad": i + 1})   # 每对都在 200s 内闭合
        for i in range(1, 6)
    ])
    findings = dynamic_rules(make_view(), samples)
    assert "D-POD-TTL-CHURN" in ids(findings)


def test_thresholds_override():
    th = RuleThresholds(saturation_min_samples=5, saturation_share=0.99)
    samples = _samples([
        (1_000_000 + i * 90, {"s": 3, "rt": i + 1}) for i in range(6)
    ])
    # share 0.99:全顶格也满足;但 min_samples=5 放宽了样本门槛
    assert "D-CONCURRENCY-SATURATION" in ids(dynamic_rules(make_view(), samples, th))


def test_finding_payload_shape():
    findings = static_rules(
        [make_view(min_idle_pods=3, max_pods=2)], ServiceView())
    payload = findings[0].to_payload()
    assert payload["source"] == "rule"
    assert set(payload) == {
        "id", "severity", "source", "target", "field", "current", "suggested",
        "rationale", "evidence", "change_class", "rebuild_cost",
    }
