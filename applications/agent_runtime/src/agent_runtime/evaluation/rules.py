# coding: utf-8
"""确定性配置评估规则(纯函数、零 IO;evaluator 组装数据后调用)。

分两类:

- **静态规则**(输入 = 快照配置视图 + 服务 env 摘要):配置自身可判定的
  矛盾/风险,如 min_idle_pods > max_pods(config 层只查下界不拦此矛盾)。
- **动态规则**(输入 = 采样序列):从运行行为反推配置是否合理,如并发
  长期顶格 → scope_concurrency 偏小;冷启动率高 → min_idle_pods 偏小。

建议只落在 **B 类策略字段**(即时生效,spec_fields.POLICY_FIELDS);A 类
(deploy 子集)变更触发 Pod 日落重建 + 409 中间态,报告 caveat 明示不建议。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..util import utc_now

SEV_INFO = "info"
SEV_WARN = "warn"
SEV_CRITICAL = "critical"

SOURCE_RULE = "rule"

# B 类策略字段白名单(LLM 补充建议同样只许引用这些字段,见 llm.py)
POLICY_FIELD_WHITELIST = frozenset({
    "scope_concurrency", "pod_concurrency", "session_ttl", "pod_ttl",
    "min_idle_pods",
})


@dataclass(frozen=True)
class Finding:
    """一条评估结论(报告/LLM prompt 的原子单元)。"""

    id: str
    severity: str                       # info | warn | critical
    source: str                         # rule | llm
    target: dict[str, str]              # {scope_id?, template_id?}
    field: str                          # 建议作用的配置字段("" = 非字段类)
    current: str                        # 现值描述
    suggested: str                      # 建议值描述("" = 仅提示不给出值)
    rationale: str
    evidence: list[str] = field(default_factory=list)
    change_class: str = ""              # A | B | ""(非字段类)
    rebuild_cost: str = ""              # 变更代价描述

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "source": self.source,
            "target": dict(self.target),
            "field": self.field,
            "current": self.current,
            "suggested": self.suggested,
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "change_class": self.change_class,
            "rebuild_cost": self.rebuild_cost,
        }


@dataclass(frozen=True)
class ScopeConfigView:
    """单 scope 的配置视图(evaluator 从路由快照 + RM 派生组装;纯数据)。"""

    scope_id: str
    template_id: str
    scope_enabled: bool
    expires_at: datetime | None
    template_enabled: bool
    scope_concurrency: int
    pod_concurrency: int
    session_ttl: int
    pod_ttl: int
    min_idle_pods: int
    max_pods: int                       # 派生值(⌈sc/pc⌉,用 Template 属性算好)
    phase: str                          # collector 的 phase 分类
    rm_min_idle: int                    # RM 侧生效值(orphan 判定用)


@dataclass(frozen=True)
class ServiceView:
    """服务级配置摘要(静态规则输入)。

    2026-09 场景 F 快失败后无等待队列,scope_full_timeout 概念已删
    (原 S-TIMEOUT-TTL-RATIO 规则随之废弃)。
    """

    pod_budget: int = 0                 # 0 = S-POD-BUDGET 规则关闭


@dataclass(frozen=True)
class RuleThresholds:
    """动态规则阈值(常量表;evaluator/测试可整体覆写)。"""

    saturation_ratio: float = 0.95      # sessions/sc ≥ 此值记一次顶格
    saturation_share: float = 0.80      # 窗口内顶格采样占比 ≥ 此值触发
    saturation_window_sec: int = 3600
    saturation_min_samples: int = 30
    capacity_err_per_hour: float = 6.0  # SCOPE_FULL 快失败速率阈值
    cold_start_ratio: float = 0.5       # deployed/(deployed+reuse) 阈值
    warm_slack_window_sec: int = 7200   # 暖池松弛观察窗(2h)
    warm_slack_util: float = 0.3        # sessions < sc×此值视为低负载
    warm_slack_min_samples: int = 60
    churn_window_sec: int = 600         # 回收后 10 分钟内重建记一次 churn
    churn_pairs: int = 3                # 窗口 24h 内 churn 对数阈值
    expiry_soon_sec: int = 86400        # 临期阈值(24h)


# ----------------------------------------------------------------------
# 静态规则


def static_rules(
    scopes: list[ScopeConfigView],
    service: ServiceView,
    thresholds: RuleThresholds | None = None,
) -> list[Finding]:
    """配置自身可判定的矛盾/风险(零运行数据依赖)。"""
    th = thresholds or RuleThresholds()
    findings: list[Finding] = []

    for v in scopes:
        if v.phase == "orphan_rm":
            if v.rm_min_idle > 0:
                findings.append(Finding(
                    id="S-RM-ORPHAN-CONFIG", severity=SEV_WARN, source=SOURCE_RULE,
                    target={"scope_id": v.scope_id}, field="min_idle_pods",
                    current=str(v.rm_min_idle), suggested="0",
                    rationale="scope 已从快照消失但 RM config 残留且 min_idle>0,"
                              "幻影预热仍在发生(autoscale 持续补热备 Pod)",
                    evidence=[f"rm_min_idle={v.rm_min_idle}", "phase=orphan_rm"],
                    change_class="B", rebuild_cost="即时生效;建议经 Manager 重下发收敛",
                ))
            else:
                findings.append(Finding(
                    id="S-RM-ORPHAN-CONFIG", severity=SEV_INFO, source=SOURCE_RULE,
                    target={"scope_id": v.scope_id}, field="",
                    current="RM config 键残留(min_idle=0)", suggested="",
                    rationale="scope 已删,RM 侧 config 键为无害残留;建议清理防误读",
                    evidence=["rm_min_idle=0", "phase=orphan_rm"],
                ))
            continue

        if v.phase == "missing_rm_cfg":
            findings.append(Finding(
                id="S-RM-MISSING-CONFIG", severity=SEV_INFO, source=SOURCE_RULE,
                target={"scope_id": v.scope_id}, field="",
                current="快照生效但 RM 无池配置键", suggested="",
                rationale="config_sync 推送失败或尚未推送;无请求预热不生效,"
                          "首个请求走冷部署",
                evidence=["phase=missing_rm_cfg"],
            ))
            continue

        if v.phase != "active":
            # disabled:过期/禁用提示(仅 expires_at 临期值得报)
            if v.expires_at is not None:
                now = utc_now()
                if v.expires_at <= now:
                    findings.append(Finding(
                        id="S-SCOPE-EXPIRY", severity=SEV_INFO, source=SOURCE_RULE,
                        target={"scope_id": v.scope_id}, field="",
                        current=f"expired at {v.expires_at.isoformat()}", suggested="",
                        rationale="scope 已过期不再参与匹配,占 DB/枚举;建议清理",
                        evidence=["phase=disabled"],
                    ))
                elif v.expires_at <= now + timedelta(seconds=th.expiry_soon_sec):
                    findings.append(Finding(
                        id="S-SCOPE-EXPIRY", severity=SEV_WARN, source=SOURCE_RULE,
                        target={"scope_id": v.scope_id}, field="",
                        current=f"expires at {v.expires_at.isoformat()}",
                        suggested="续期或安排迁移",
                        rationale="scope 24h 内到期,届时流量将落兜底 scope",
                        evidence=["phase=disabled"],
                    ))
            if not v.template_enabled:
                findings.append(Finding(
                    id="S-DISABLED-TEMPLATE-REF", severity=SEV_WARN, source=SOURCE_RULE,
                    target={"scope_id": v.scope_id, "template_id": v.template_id},
                    field="", current="template enabled=false", suggested="",
                    rationale="scope 引用禁用模板,匹配时被跳过——该 scope 是"
                              "死配置,流量落兜底",
                    evidence=["phase=disabled", "template_enabled=false"],
                ))
            continue

        # ---- active:逐项静态检查
        if v.min_idle_pods > v.max_pods:
            findings.append(Finding(
                id="S-CONTRADICTION-MIN-IDLE", severity=SEV_CRITICAL,
                source=SOURCE_RULE,
                target={"scope_id": v.scope_id, "template_id": v.template_id},
                field="min_idle_pods",
                current=f"{v.min_idle_pods} > max_pods={v.max_pods}",
                suggested=f"≤ {v.max_pods}",
                rationale="config 层只校验下界不拦此矛盾:autoscale 永远"
                          "skip_max 补不满暖池,min_idle 实际无效",
                evidence=[
                    f"min_idle_pods={v.min_idle_pods}",
                    f"max_pods=⌈{v.scope_concurrency}/{v.pod_concurrency}⌉={v.max_pods}",
                ],
                change_class="B", rebuild_cost="即时生效,无重建",
            ))

        if v.scope_concurrency % v.pod_concurrency != 0:
            findings.append(Finding(
                id="S-CEIL-WASTE", severity=SEV_INFO, source=SOURCE_RULE,
                target={"scope_id": v.scope_id, "template_id": v.template_id},
                field="scope_concurrency",
                current=f"sc={v.scope_concurrency}, pc={v.pod_concurrency}",
                suggested=f"sc 对齐 pc 倍数(如 {((v.scope_concurrency // v.pod_concurrency) + 1) * v.pod_concurrency})",
                rationale=f"max_pods=⌈sc/pc⌉ 取整浪费:第 {v.max_pods} 个 Pod"
                          f"仅承载 {v.scope_concurrency % v.pod_concurrency}/"
                          f"{v.pod_concurrency} 并发",
                evidence=[
                    f"max_pods={v.max_pods}",
                    f"tail={v.scope_concurrency % v.pod_concurrency}/{v.pod_concurrency}",
                ],
                change_class="B", rebuild_cost="即时生效,无重建",
            ))

        if v.expires_at is not None:
            now = utc_now()
            if now + timedelta(seconds=th.expiry_soon_sec) >= v.expires_at > now:
                findings.append(Finding(
                    id="S-SCOPE-EXPIRY", severity=SEV_WARN, source=SOURCE_RULE,
                    target={"scope_id": v.scope_id}, field="",
                    current=f"expires at {v.expires_at.isoformat()}",
                    suggested="续期或安排迁移",
                    rationale="生效中的 scope 24h 内到期,届时流量将落兜底 scope",
                    evidence=["phase=active"],
                ))

    # ---- 集群预算(全局单条,不挂 scope)
    if service.pod_budget > 0:
        total_min_idle = sum(
            v.min_idle_pods for v in scopes if v.phase == "active"
        )
        if total_min_idle > service.pod_budget:
            findings.append(Finding(
                id="S-POD-BUDGET", severity=SEV_WARN, source=SOURCE_RULE,
                target={}, field="min_idle_pods",
                current=f"Σmin_idle(active)={total_min_idle}",
                suggested=f"≤ {service.pod_budget}",
                rationale="生效 scope 的热备底数之和超过集群 AgentServer Pod"
                          "预算;多 scope 共享模板不共享热备,每 scope 独占"
                          "min_idle 个 Pod",
                evidence=[
                    f"pod_budget={service.pod_budget}",
                    f"sum_min_idle={total_min_idle}",
                    f"active_scopes={sum(1 for v in scopes if v.phase == 'active')}",
                ],
                change_class="B", rebuild_cost="即时生效,无重建",
            ))

    return findings


# ----------------------------------------------------------------------
# 动态规则(输入 = 采样序列)


def _counter_rate(samples: list[dict[str, Any]], key: str) -> float:
    """窗口内计数器增速/小时(首末差分;计数单调)。"""
    if len(samples) < 2:
        return 0.0
    first, last = samples[0], samples[-1]
    span = max(last["t"] - first["t"], 1)
    return (last.get(key, 0) - first.get(key, 0)) / span * 3600.0


def dynamic_rules(
    view: ScopeConfigView,
    samples: list[dict[str, Any]],
    thresholds: RuleThresholds | None = None,
) -> list[Finding]:
    """从采样序列反推配置是否合理(samples 升序、最多 24h 窗口)。"""
    th = thresholds or RuleThresholds()
    findings: list[Finding] = []
    if view.phase != "active" or not samples:
        return findings

    sc = max(view.scope_concurrency, 1)
    target = {"scope_id": view.scope_id, "template_id": view.template_id}

    # ---- 并发长期顶格 → 升 scope_concurrency
    now = samples[-1]["t"]
    window = [s for s in samples if s["t"] >= now - th.saturation_window_sec]
    if len(window) >= th.saturation_min_samples:
        hits = sum(
            1 for s in window if s.get("s", 0) >= sc * th.saturation_ratio
        )
        share = hits / len(window)
        if share >= th.saturation_share and window[-1].get("rt", 0) > 0:
            findings.append(Finding(
                id="D-CONCURRENCY-SATURATION", severity=SEV_WARN,
                source=SOURCE_RULE, target=target, field="scope_concurrency",
                current=str(view.scope_concurrency),
                suggested=str(max(view.scope_concurrency * 2, 1)),
                rationale=f"窗口 {th.saturation_window_sec}s 内并发使用率 ≥"
                          f"{th.saturation_ratio:.0%} 的采样占 {share:.0%},"
                          "容量长期顶格",
                evidence=[
                    f"saturated_share={share:.2f} (threshold={th.saturation_share})",
                    f"samples={len(window)}",
                    f"scope_concurrency={view.scope_concurrency}",
                ],
                change_class="B", rebuild_cost="即时生效,无重建",
            ))

    # ---- 容量错误频发 → 升 scope_concurrency(标注 max_pods 联动)
    err_rate = _counter_rate(window, "ef") if window else 0.0
    if err_rate > th.capacity_err_per_hour:
        pods_at_max = sum(
            1 for s in window if s.get("p", 0) >= view.max_pods
        ) / max(len(window), 1)
        note = (
            f";pods 长期={view.max_pods}(顶格占比 {pods_at_max:.0%}),"
            "升 sc 会联动抬升 max_pods=⌈sc/pc⌉"
            if pods_at_max >= 0.5 else ""
        )
        findings.append(Finding(
            id="D-CAPACITY-ERRORS", severity=SEV_WARN, source=SOURCE_RULE,
            target=target, field="scope_concurrency",
            current=str(view.scope_concurrency),
            suggested=str(max(view.scope_concurrency * 2, 1)),
            rationale=f"容量错误(SCOPE_FULL 快失败)速率 "
                      f"{err_rate:.1f}/h 超阈值 {th.capacity_err_per_hour}/h"
                      f"{note}",
            evidence=[
                f"err_rate_per_hour={err_rate:.1f}",
                f"threshold={th.capacity_err_per_hour}",
                f"pods_at_max_share={pods_at_max:.2f}" if window else "",
            ],
            change_class="B", rebuild_cost="即时生效,无重建",
        ))

    # ---- 冷启动率高 → 升 min_idle_pods
    deployed = samples[-1].get("ad", 0) - samples[0].get("ad", 0)
    reuse = samples[-1].get("ar", 0) - samples[0].get("ar", 0)
    cold_ratio = deployed / (deployed + reuse) if (deployed + reuse) > 0 else 0.0
    if cold_ratio > th.cold_start_ratio and (deployed + reuse) >= 3:
        findings.append(Finding(
            id="D-COLD-START-RATE", severity=SEV_INFO, source=SOURCE_RULE,
            target=target, field="min_idle_pods",
            current=str(view.min_idle_pods),
            suggested=str(view.min_idle_pods + 1),
            rationale=f"冷部署占比 {cold_ratio:.0%}(deployed={deployed},"
                      f"reuse={reuse})——暖池深度不足,请求在等冷启动",
            evidence=[
                f"cold_start_ratio={cold_ratio:.2f}",
                f"deployed={deployed} reuse={reuse}",
            ],
            change_class="B", rebuild_cost="即时生效,无重建",
        ))

    # ---- 暖池长期满水位且低负载 → 降 min_idle_pods(省资源)
    slack_window = [s for s in samples if s["t"] >= now - th.warm_slack_window_sec]
    if len(slack_window) >= th.warm_slack_min_samples:
        at_floor = sum(
            1 for s in slack_window
            if s.get("i", 0) >= view.min_idle_pods and view.min_idle_pods > 0
        ) / len(slack_window)
        low_load = sum(
            1 for s in slack_window if s.get("s", 0) < sc * th.warm_slack_util
        ) / len(slack_window)
        err_total = samples[-1].get("ef", 0) - samples[0].get("ef", 0)
        if (at_floor >= 0.9 and low_load >= 0.8 and err_total == 0
                and view.min_idle_pods > 0):
            findings.append(Finding(
                id="D-WARM-POOL-SLACK", severity=SEV_INFO, source=SOURCE_RULE,
                target=target, field="min_idle_pods",
                current=str(view.min_idle_pods),
                suggested=str(view.min_idle_pods - 1),
                rationale=f"窗口 {th.warm_slack_window_sec}s 内暖池 {at_floor:.0%}"
                          f"时间满水位且负载低于 {th.warm_slack_util:.0%} 分位、"
                          "零容量错误——热备过剩",
                evidence=[
                    f"at_floor_share={at_floor:.2f}",
                    f"low_load_share={low_load:.2f}",
                    f"capacity_errors={err_total}",
                ],
                change_class="B", rebuild_cost="即时生效,无重建",
            ))

    # ---- 回收-重建 churn → 升 pod_ttl
    if len(samples) >= 2:
        pairs = _reclaim_redeploy_pairs(samples, th.churn_window_sec)
        if pairs >= th.churn_pairs:
            findings.append(Finding(
                id="D-POD-TTL-CHURN", severity=SEV_INFO, source=SOURCE_RULE,
                target=target, field="pod_ttl",
                current=f"{view.pod_ttl}s",
                suggested=f"{view.pod_ttl * 2}s",
                rationale=f"24h 内回收后 {th.churn_window_sec}s 内重建 "
                          f"{pairs} 次——空闲 Pod 被回收后很快又要用,"
                          "pod_ttl 偏短造成部署churn",
                evidence=[f"churn_pairs={pairs}", f"threshold={th.churn_pairs}"],
                change_class="B", rebuild_cost="即时生效,无重建",
            ))

    # (原 D-WAITER-PRESSURE 规则随有界等待队列一起废弃——2026-09 场景 F
    #  快失败后无 waiters/max_waiters 概念,容量压力由 D-CAPACITY-ERRORS
    #  的 SCOPE_FULL 速率承担)

    return findings


def _reclaim_redeploy_pairs(
    samples: list[dict[str, Any]], churn_window_sec: int
) -> int:
    """回收→短窗内重建 的对数(计数单调,差分找事件点)。"""
    pairs = 0
    prev_rc = prev_ad = 0
    reclaim_t: int | None = None
    for s in samples:
        rc, ad = s.get("rc", 0), s.get("ad", 0)
        if rc > prev_rc:
            reclaim_t = s["t"]
        if ad > prev_ad and reclaim_t is not None:
            if s["t"] - reclaim_t <= churn_window_sec:
                pairs += 1
            reclaim_t = None       # 一次重建闭合一个回收点
        prev_rc, prev_ad = rc, ad
    return pairs
