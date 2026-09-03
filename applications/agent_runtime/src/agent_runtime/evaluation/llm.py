# coding: utf-8
"""LLM 分析层(OpenAI 兼容 chat completions;env 未配置自动禁用)。

定位:**LLM 不做原始数据分析**——确定性规则引擎已产结构化 findings;
LLM 拿到的是「规则产物 + 趋势聚合 + 配置快照」的白名单 JSON,只做汇总
叙述、风险补充与置信度标注。输出严格 JSON(防御式解析,失败降级纯规则
报告);补充建议逐项过策略字段白名单,防越界到 A 类(deploy 子集)。

安全:prompt payload 构造期白名单(绝不含 agent_env/kubeconfig/pod_spec/
api_key/base_url);服务自有网络边界内调用,每 5min 一次短连接。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("agent_runtime.evaluation")

# prompt 体积护栏:超限截断 trend 段(保最新 + 总量计数)
PAYLOAD_MAX_BYTES = 48 * 1024

# LLM 补充建议只许引用的策略字段(与 rules.POLICY_FIELD_WHITELIST 同源)
_ALLOWED_FIELDS = frozenset({
    "scope_concurrency", "pod_concurrency", "session_ttl", "pod_ttl",
    "min_idle_pods",
})

_ALLOWED_SEVERITIES = frozenset({"info", "warn", "critical"})

_SYSTEM_PROMPT = (
    "你是 agent-runtime 会话编排服务的容量配置评审员。你只依据用户消息中"
    "给出的 JSON 数据分析,不得编造数据。规则引擎已产出 findings;你的任务:"
    "1) 用一段话总结系统整体健康与主要风险;2) 评估规则建议的合理性与优先级;"
    "3) 可补充规则未覆盖的建议,但只允许引用这些配置字段: "
    + ", ".join(sorted(_ALLOWED_FIELDS))
    + ";4) 不确定的判断标注低置信度。输出严格 JSON(无围栏无多余文本),"
    "schema: {\"summary\": str, \"risk_notes\": [str], "
    "\"additional_findings\": [{\"id\": str, \"severity\": "
    "\"info|warn|critical\", \"target\": {\"scope_id\": str, "
    "\"template_id\": str}, \"field\": str, \"current\": str, "
    "\"suggested\": str, \"rationale\": str}], \"confidence\": "
    "\"low|medium|high\"}"
)

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.MULTILINE)


@dataclass
class LLMResult:
    """一次 LLM 调用的结果(不抛异常;error 字段留痕)。"""

    status: str            # ok | error
    text: str = ""
    latency_ms: float = 0.0
    error: str = ""


class LLMClient:
    """OpenAI 兼容 chat completions 客户端(每调用一次短连接)。"""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout: float = 60.0,
        transport: Any = None,       # httpx transport 注入口(测试 MockTransport)
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport

    @classmethod
    def from_arc(cls, arc: Any) -> "LLMClient":
        """从 AgentRuntimeConfig 构造(AGENT_RUNTIME_EVAL_LLM_* 环境变量)。"""
        return cls(
            base_url=getattr(arc, "eval_llm_base_url", "") or "",
            api_key=getattr(arc, "eval_llm_api_key", "") or "",
            model=getattr(arc, "eval_llm_model", "") or "",
            timeout=float(getattr(arc, "eval_llm_timeout", 60.0) or 60.0),
        )

    @property
    def enabled(self) -> bool:
        """base_url + model 均非空才启用(api_key 可选:内网免鉴权端点)。"""
        return bool(self.base_url and self.model)

    async def analyze(self, prompt_payload: dict[str, Any]) -> LLMResult:
        """调用 LLM 分析(任何失败返回 status=error,绝不抛)。"""
        if not self.enabled:
            return LLMResult(status="error", error="llm disabled")
        import httpx

        user_text = json.dumps(
            build_prompt(prompt_payload), ensure_ascii=False, separators=(",", ":")
        )
        t0 = time.monotonic()
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            ) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": user_text},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 1024,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            text = str(data["choices"][0]["message"]["content"] or "")
            return LLMResult(
                status="ok", text=text,
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 - 降级路径,绝不反噬评估
            return LLMResult(
                status="error", error=f"{type(exc).__name__}: {exc}"[:300],
                latency_ms=(time.monotonic() - t0) * 1000,
            )


# ----------------------------------------------------------------------
# prompt 构造与解析(纯函数,单测覆盖)


def build_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    """白名单投影 + 体积护栏(超限截断 per-scope trend 段)。

    payload 由 evaluator 组装:{service, scopes, findings};此处只做
    防御性收窄——键白名单逐层过滤,序列化超限砍 trend。
    """
    out = _filter_service(payload.get("service"))
    scopes = [_filter_scope(s) for s in payload.get("scopes", [])]
    findings = payload.get("findings", [])
    text = json.dumps(
        {"service": out, "scopes": scopes, "findings": findings},
        ensure_ascii=False, separators=(",", ":"),
    )
    if len(text.encode()) <= PAYLOAD_MAX_BYTES:
        return {"service": out, "scopes": scopes, "findings": findings}
    # 截断:每 scope 只留最新一个 trend 点
    for s in scopes:
        trend = s.get("trend")
        if isinstance(trend, list) and trend:
            s["trend"] = trend[-1:]
    return {"service": out, "scopes": scopes, "findings": findings,
            "_truncated": True}


def _filter_service(service: Any) -> dict[str, Any]:
    src = service if isinstance(service, dict) else {}
    return {k: src.get(k) for k in (
        "eval_interval", "eval_sample_interval",
        "pod_budget",
    ) if k in src}


def _filter_scope(scope: Any) -> dict[str, Any]:
    src = scope if isinstance(scope, dict) else {}
    keys = ("scope_id", "phase", "scope_concurrency", "pod_concurrency",
            "session_ttl", "pod_ttl", "min_idle_pods", "max_pods",
            "pods", "idle", "session_count", "trend")
    return {k: src.get(k) for k in keys if k in src}


def parse_llm_analysis(text: str) -> dict[str, Any]:
    """解析 LLM 输出为受控结构;任何不符 → ValueError(调用方降级)。

    剥 ```json 围栏 → json.loads → 结构校验 → additional_findings 逐项
    白名单过滤(字段只许策略字段;违规项整条丢弃,不猜不改)。
    """
    stripped = _FENCE_RE.sub("", (text or "").strip()).strip()
    if not stripped.startswith("{"):
        raise ValueError("llm output is not a JSON object")
    try:
        data = json.loads(stripped)
    except ValueError as exc:
        raise ValueError(f"llm output not parseable: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("llm output is not a JSON object")

    out: dict[str, Any] = {
        "summary": str(data.get("summary") or "")[:1000],
        "risk_notes": [
            str(n)[:300] for n in (data.get("risk_notes") or [])
            if isinstance(n, str)
        ][:10],
        "confidence": (
            str(data.get("confidence"))
            if str(data.get("confidence")) in ("low", "medium", "high")
            else "low"
        ),
        "additional_findings": [],
    }
    raw_findings = data.get("additional_findings")
    if isinstance(raw_findings, list):
        for item in raw_findings:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "")
            if field not in _ALLOWED_FIELDS:
                continue        # 越界建议(A 类/未知字段)整条丢弃
            severity = str(item.get("severity") or "info")
            out["additional_findings"].append({
                "id": str(item.get("id") or "LLM-ADVICE")[:80],
                "severity": severity if severity in _ALLOWED_SEVERITIES else "info",
                "target": {
                    "scope_id": str((item.get("target") or {}).get("scope_id") or ""),
                    "template_id": str((item.get("target") or {}).get("template_id") or ""),
                },
                "field": field,
                "current": str(item.get("current") or "")[:200],
                "suggested": str(item.get("suggested") or "")[:200],
                "rationale": str(item.get("rationale") or "")[:500],
            })
    return out
