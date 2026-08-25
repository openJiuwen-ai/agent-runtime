# coding: utf-8
"""进程内请求指标注册表 + 请求汇总日志中间件（/debug/stats 数据源）。

设计要点：

- 零依赖、纯内存；挂在 ``app.asgi.state.metrics``（非模块级可变状态，
  双实例测试 harness 各自独立一份）。
- 中间件经 ``App.use()`` 进入 router 派发链（最外层），四个对外端点每请求
  恰好一条 INFO 汇总（含 touch/cleanup——此前两侧路径零日志）。router 已把
  异常转为错误信封，这里读 ``UnaryResult.response.ok/error_code`` 即最终
  客户端可见结果；中间件自身的 except 分支仅防御（信封构造前抛错等）。
- 延迟分位数读取时才计算（bounded deque，窗口 1024）；错误环形缓冲 200 条，
  新在前。单进程视角——多副本各自统计，LB 后看到的是命中实例的那份。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .logsetup import reset_request_log_context, set_request_log_context

logger = logging.getLogger("agent_runtime.metrics")

_LATENCY_WINDOW = 1024        # 每端点保留的最近延迟样本数（分位数窗口）
_ERROR_CAPACITY = 200         # recent_errors 环形容量
_DETAIL_MAX = 300             # 错误 detail 截断长度


@dataclass
class _EndpointStats:
    total: int = 0
    ok: int = 0
    error: int = 0
    by_error_code: dict[str, int] = field(default_factory=dict)
    durations: deque[float] = field(default_factory=lambda: deque(maxlen=_LATENCY_WINDOW))
    duration_ms_max: float = 0.0


def _percentile(sorted_values: list[float], ratio: float) -> float:
    """有序列表取分位数（空列表返回 0；线性插值无需——运维看量级即可）。"""
    if not sorted_values:
        return 0.0
    idx = min(int(len(sorted_values) * ratio), len(sorted_values) - 1)
    return sorted_values[idx]


class MetricsRegistry:
    """per-endpoint 请求计数 / 延迟窗口 / 最近错误环形缓冲。"""

    def __init__(
        self,
        *,
        latency_window: int = _LATENCY_WINDOW,
        error_capacity: int = _ERROR_CAPACITY,
    ) -> None:
        self.started_at = time.time()
        self._latency_window = latency_window
        self._error_capacity = error_capacity
        self._endpoints: dict[str, _EndpointStats] = {}
        self._recent_errors: deque[dict[str, Any]] = deque(maxlen=error_capacity)

    # -------------------------------------------------------------- 写入

    def observe_request(
        self,
        *,
        endpoint: str,
        ok: bool,
        error_code: str | None,
        duration_ms: float,
        request_id: str | None = None,
        session_id: str | None = None,
        detail: str = "",
    ) -> None:
        stats = self._endpoints.setdefault(
            endpoint, _EndpointStats(
                durations=deque(maxlen=self._latency_window),
            )
        )
        stats.total += 1
        if ok:
            stats.ok += 1
        else:
            stats.error += 1
            code = error_code or "UNKNOWN"
            stats.by_error_code[code] = stats.by_error_code.get(code, 0) + 1
            self._recent_errors.append({
                "ts": round(time.time(), 3),
                "endpoint": endpoint,
                "error_code": code,
                "request_id": request_id or "",
                "session_id": session_id or "",
                "duration_ms": round(duration_ms, 1),
                "detail": (detail or "")[:_DETAIL_MAX],
            })
        stats.durations.append(duration_ms)
        stats.duration_ms_max = max(stats.duration_ms_max, duration_ms)

    # -------------------------------------------------------------- 读取

    def snapshot(self) -> dict[str, Any]:
        """只读快照（分位数读取时计算；允许瞬时不一致）。"""
        endpoints: dict[str, Any] = {}
        total_req = total_ok = total_err = 0
        for name, stats in sorted(self._endpoints.items()):
            ordered = sorted(stats.durations)
            endpoints[name] = {
                "total": stats.total,
                "ok": stats.ok,
                "error": stats.error,
                "by_error_code": dict(sorted(stats.by_error_code.items())),
                "p50_ms": round(_percentile(ordered, 0.50), 1),
                "p95_ms": round(_percentile(ordered, 0.95), 1),
                "max_ms": round(stats.duration_ms_max, 1),
            }
            total_req += stats.total
            total_ok += stats.ok
            total_err += stats.error
        return {
            "started_at": self.started_at,
            "uptime_sec": round(time.time() - self.started_at, 1),
            "latency_window": self._latency_window,
            "requests": {"total": total_req, "ok": total_ok, "error": total_err},
            "endpoints": endpoints,
        }

    def recent_errors(self, limit: int = 50) -> list[dict[str, Any]]:
        """最近错误（新在前）。"""
        if limit <= 0:
            return []
        return list(self._recent_errors)[-limit:][::-1]


def request_metrics_middleware(
    registry: MetricsRegistry,
) -> Callable[[Any, Any, Any], Any]:
    """router 中间件工厂：请求上下文注入 + 一条 INFO 汇总 + 指标记录。"""

    async def middleware(ctx: Any, env: Any, nxt: Any) -> Any:
        metadata = getattr(env, "metadata", None)
        request_id = getattr(metadata, "request_id", "") if metadata else ""
        session_id = getattr(metadata, "session_id", "") if metadata else ""
        instance_id = getattr(getattr(ctx, "sysctx", None), "instance_id", "") or ""
        token = set_request_log_context(
            request_id=request_id,
            session_id=session_id,
            endpoint=getattr(env, "type", ""),
            instance_id=instance_id,
        )
        t0 = time.monotonic()
        ok, error_code, detail = True, None, ""
        try:
            result = await nxt(ctx, env)
            response = getattr(result, "response", None)
            if response is not None:
                ok = bool(getattr(response, "ok", True))
                error_code = getattr(response, "error_code", None)
                if not ok:
                    detail = str(getattr(response, "error_message", "") or "")
            return result
        except Exception as exc:  # noqa: BLE001 - 防御：信封构造前抛错的极端路径
            ok, error_code, detail = False, "INTERNAL", f"{type(exc).__name__}: {exc}"
            raise
        finally:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "request: endpoint=%s outcome=%s error_code=%s duration_ms=%.1f",
                getattr(env, "type", "?"),
                "ok" if ok else "error",
                error_code or "-",
                duration_ms,
            )
            registry.observe_request(
                endpoint=getattr(env, "type", "?"),
                ok=ok,
                error_code=error_code,
                duration_ms=duration_ms,
                request_id=request_id,
                session_id=session_id,
                detail=detail,
            )
            reset_request_log_context(token)   # 必须在汇总日志之后

    return middleware
