# coding: utf-8
"""评估编排(sys_eval job 的 on_tick):采集 → 规则 → LLM → 报告落 Redis。

报告是**全局视角**(选主 job 单副本执行;报告落 Redis,任意副本经
/visualization/evaluation 读到同一份——与 per-instance 端点不同)。

闭环边界(红线):报告只读产出,不动写通道;建议只落 B 类策略字段,
caveat 明示 A 类代价与应用路径(人审 → Claw Manager config_sync)。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..util import to_int
from .collector import EvaluationCollector
from .llm import LLMClient, parse_llm_analysis
from .rules import (
    Finding,
    RuleThresholds,
    ScopeConfigView,
    ServiceView,
    dynamic_rules,
    static_rules,
)
from .state import EvaluationState

logger = logging.getLogger("agent_runtime.evaluation")

# 评估窗口:动态规则回看 24h 采样(= 采样键 TTL 的主体)
EVAL_WINDOW_SEC = 24 * 3600


class Evaluator:
    """一次 evaluate_once = 规则全量评估(可选叠加 LLM 分析)+ 报告落盘。"""

    def __init__(
        self,
        *,
        collector: EvaluationCollector,
        llm: LLMClient,
        state: EvaluationState,
        arc: Any,                         # AgentRuntimeConfig
        instance_id: str,
        thresholds: RuleThresholds | None = None,
    ) -> None:
        self.collector = collector
        self.llm = llm
        self.state = state
        self.arc = arc
        self.instance_id = instance_id
        self.thresholds = thresholds or RuleThresholds()

    # -------------------------------------------------------------- 数据视图

    def _config_view(self, row: dict[str, Any]) -> ScopeConfigView | None:
        routing = row.get("routing")
        template = row.get("template")
        if routing is None or template is None:
            if row["phase"] == "orphan_rm":
                # 孤儿:无快照定义,仅报 RM 侧 min_idle
                return ScopeConfigView(
                    scope_id=row["scope_id"], template_id="",
                    scope_enabled=False, expires_at=None,
                    template_enabled=False,
                    scope_concurrency=0, pod_concurrency=1, session_ttl=0,
                    pod_ttl=0, min_idle_pods=0, max_pods=0,
                    phase=row["phase"],
                    rm_min_idle=to_int(row.get("rm_config", {}).get("min_idle_pods")),
                )
            return None
        return ScopeConfigView(
            scope_id=row["scope_id"],
            template_id=routing.template_id,
            scope_enabled=routing.enabled,
            expires_at=routing.expires_at,
            template_enabled=template.enabled,
            scope_concurrency=template.scope_concurrency,
            pod_concurrency=template.pod_concurrency,
            session_ttl=template.session_ttl,
            pod_ttl=template.pod_ttl,
            min_idle_pods=template.min_idle_pods,
            max_pods=template.max_pods,
            phase=row["phase"],
            rm_min_idle=to_int(row.get("rm_config", {}).get("min_idle_pods")),
        )

    # -------------------------------------------------------------- 主流程

    async def evaluate_once(self) -> None:
        """sys_eval on_tick。任何子步失败留痕不中断(报告可缺 LLM 段)。"""
        t0 = time.monotonic()
        try:
            report = await self._evaluate()
            await self.state.write_report(report)
            logger.info(
                "sys_eval report: findings=%d critical=%d llm=%s duration_ms=%.0f",
                report["summary"]["findings_total"],
                report["summary"]["by_severity"].get("critical", 0),
                report["llm"]["status"],
                (time.monotonic() - t0) * 1000,
            )
        except Exception:  # noqa: BLE001 - job 循环防崩,下拍重试
            logger.exception("sys_eval failed")

    async def _evaluate(self) -> dict[str, Any]:
        rows = await self.collector.scope_inventory()
        service = ServiceView(
            pod_budget=int(getattr(self.arc, "eval_pod_budget", 0) or 0),
        )

        findings: list[Finding] = []
        pairs: list[tuple[ScopeConfigView, dict[str, Any]]] = []
        for row in rows:
            view = self._config_view(row)
            if view is not None:
                pairs.append((view, row))
        views = [v for v, _ in pairs]
        findings.extend(static_rules(views, service, self.thresholds))

        now = to_int(time.time())
        trend_by_scope: dict[str, Any] = {}
        for view, row in pairs:
            samples = await self.state.samples(
                view.scope_id, now - EVAL_WINDOW_SEC
            )
            findings.extend(dynamic_rules(view, samples, self.thresholds))
            trend_by_scope[view.scope_id] = self._trend_summary(view, samples, row)

        report: dict[str, Any] = {
            "generated_at": round(time.time(), 3),
            "instance_id": self.instance_id,
            "window_sec": EVAL_WINDOW_SEC,
            "llm": {"status": "disabled", "model": "", "latency_ms": 0.0,
                    "error": ""},
            "summary": {},
            "findings": [f.to_payload() for f in findings],
            "trend": trend_by_scope,
            "service": {
                "eval_interval": int(getattr(self.arc, "eval_interval", 300)),
                "eval_sample_interval": int(
                    getattr(self.arc, "eval_sample_interval", 30)),
                "pod_budget": service.pod_budget,
            },
            "caveats": [
                "建议均为 B 类策略字段(即时生效,无 Pod 重建);A 类(deploy "
                "子集)变更将触发存量 Pod 日落重建且有 409 CONFIG_SYNC_BUSY "
                "中间态风险,本报告不涉及",
                "建议经人工审阅后由 Claw Manager 经 config_sync 下发应用;"
                "本服务不自动改配置",
            ],
        }

        # ---- LLM 叠加(enabled 才调;失败降级纯规则报告)
        if self.llm.enabled:
            result = await self.llm.analyze({
                "service": report["service"],
                "scopes": [
                    {
                        "scope_id": v.scope_id, "phase": v.phase,
                        "scope_concurrency": v.scope_concurrency,
                        "pod_concurrency": v.pod_concurrency,
                        "session_ttl": v.session_ttl, "pod_ttl": v.pod_ttl,
                        "min_idle_pods": v.min_idle_pods, "max_pods": v.max_pods,
                        **{k: trend_by_scope[v.scope_id].get(k) for k in
                           ("pods", "idle", "session_count")},
                        "trend": trend_by_scope[v.scope_id].get("points", []),
                    }
                    for v in views
                ],
                "findings": report["findings"],
            })
            if result.status == "ok":
                try:
                    analysis = parse_llm_analysis(result.text)
                except ValueError as exc:
                    report["llm"] = {
                        "status": "error", "model": self.llm.model,
                        "latency_ms": round(result.latency_ms, 1),
                        "error": f"parse failed: {exc}",
                    }
                else:
                    report["llm"] = {
                        "status": "ok", "model": self.llm.model,
                        "latency_ms": round(result.latency_ms, 1), "error": "",
                    }
                    report["llm_analysis"] = analysis
                    for extra in analysis.get("additional_findings", []):
                        findings_payload = {
                            "id": extra["id"], "severity": extra["severity"],
                            "source": "llm", "target": extra["target"],
                            "field": extra["field"], "current": extra["current"],
                            "suggested": extra["suggested"],
                            "rationale": extra["rationale"], "evidence": [],
                            "change_class": "B",
                            "rebuild_cost": "即时生效,无重建",
                        }
                        report["findings"].append(findings_payload)
            else:
                report["llm"] = {
                    "status": "error", "model": self.llm.model,
                    "latency_ms": round(result.latency_ms, 1),
                    "error": result.error,
                }

        report["summary"] = self._summary(views, report["findings"])
        return report

    # -------------------------------------------------------------- 聚合

    @staticmethod
    def _trend_summary(
        view: ScopeConfigView, samples: list[dict[str, Any]], row: dict[str, Any]
    ) -> dict[str, Any]:
        """per-scope 趋势聚合(进报告 trend 段与 LLM prompt;紧凑)。"""
        if not samples:
            return {
                "pods": row.get("pods", 0), "idle": row.get("idle", 0),
                "session_count": row.get("session_count", 0),
                "points": [],
            }
        sc = max(view.scope_concurrency, 1)
        last = samples[-1]
        # 点位瘦身:每 10 个采样取 1 个(24h@30s ≈ 2880 → 288 点)
        step = max(len(samples) // 288, 1)
        points = [
            {"t": s["t"], "s": s.get("s", 0), "i": s.get("i", 0),
             "p": s.get("p", 0)}
            for s in samples[::step]
        ]
        return {
            "pods": last.get("p", 0), "idle": last.get("i", 0),
            "session_count": last.get("s", 0),
            "cold_start_ratio": (
                (last.get("ad", 0) - samples[0].get("ad", 0)) /
                max((last.get("ad", 0) - samples[0].get("ad", 0))
                    + (last.get("ar", 0) - samples[0].get("ar", 0)), 1)
            ),
            "err_full_timeout": last.get("ef", 0) - samples[0].get("ef", 0),
            "err_queue_full": last.get("eq", 0) - samples[0].get("eq", 0),
            "saturation_peak": max(s.get("s", 0) for s in samples) / sc,
            "points": points,
        }

    @staticmethod
    def _summary(views: list[ScopeConfigView], findings: list[dict[str, Any]]) -> dict[str, Any]:
        by_severity: dict[str, int] = {"info": 0, "warn": 0, "critical": 0}
        for f in findings:
            sev = str(f.get("severity") or "info")
            by_severity[sev] = by_severity.get(sev, 0) + 1
        return {
            "scopes_total": len(views),
            "active": sum(1 for v in views if v.phase == "active"),
            "findings_total": len(findings),
            "by_severity": by_severity,
        }
