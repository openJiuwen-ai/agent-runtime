# coding: utf-8
"""SM 对外 HTTP handler（对外仅 4 个端点，prefix /api/session）：

- route        同步路由 + 占额度（幂等键 = metadata.request_id）
- touch        保活 / EOS
- config_sync  Claw Manager 配置全量下发（templates + scopes）
- cleanup      运维批删 Pod（handler 在 SM，委托 rm_facade.cleanup）

服务对象（orchestrator / config_store / rm_facade）挂在 sysctx 上（main 注入），
handler 无模块级可变状态。业务异常在此映射为带 retry_after 的错误信封。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from openjiuwen_runtime.service.envelope import Envelope, ResponseEnvelope

from ..errors import AgentRuntimeError
from ..util import now_ts

logger = logging.getLogger("agent_runtime.session_manager")

IDEMPOTENCY_WINDOW = 60  # route 结果缓存窗口（框架默认一致）


def _services(ctx: Any) -> tuple[Any, Any, Any]:
    """(orchestrator, config_store, rm_facade) —— 由 OrchestratorSystemContext 注入。"""
    sysctx = ctx.sysctx
    return sysctx.sm_orchestrator, sysctx.sm_config_store, sysctx.rm_facade


def _error_envelope(env: Envelope, exc: AgentRuntimeError) -> ResponseEnvelope:
    """业务异常 → 错误信封（error_code / error_message / retry_after）。"""
    return ResponseEnvelope(
        type=env.type,
        metadata=env.metadata,
        rawdata={},
        ok=False,
        error_code=exc.code,
        error_message=str(exc),
        retry_after=exc.retry_after,
    )


def _fail(
    env: Envelope,
    exc: AgentRuntimeError,
    *,
    endpoint: str,
    duration_ms: float,
    **fields: Any,
) -> ResponseEnvelope:
    """业务失败统一留痕（WARNING + 异常链堆栈）并回错误信封。"""
    extras = "".join(f" {k}={v}" for k, v in fields.items() if v)
    logger.warning(
        "%s failed:%s error=%s duration_ms=%.1f detail=%s",
        endpoint, extras, exc.code, duration_ms, exc,
        exc_info=True,
    )
    return _error_envelope(env, exc)


async def handle_route(ctx, env: Envelope) -> ResponseEnvelope | dict:
    """POST /api/session/route：{pod_sse_url, pod_id}；幂等回放优先。"""
    orchestrator, _, _ = _services(ctx)
    metadata = env.metadata
    session_id = metadata.session_id or ""
    group_id = str((metadata.extra or {}).get("group_id") or "")
    bot_id = metadata.bot_id or ""

    guard = await ctx.idempotency.acquire(metadata.request_id, IDEMPOTENCY_WINDOW)
    if not guard.acquired and guard.cached_result is not None:
        logger.info("route idempotent replay: request_id=%s", metadata.request_id)
        cached = guard.cached_result
        return ResponseEnvelope(
            type=env.type, metadata=env.metadata, rawdata=dict(cached.rawdata),
            ok=True, retry_after=None,
        )
    t0 = time.monotonic()
    try:
        result = await orchestrator.route(
            request_id=metadata.request_id,
            session_id=session_id,
            group_id=group_id,
            bot_id=bot_id,
            user_id=metadata.user_id,
        )
    except AgentRuntimeError as exc:
        return _fail(env, exc, endpoint="route", duration_ms=(time.monotonic() - t0) * 1000,
                     session=session_id, request_id=metadata.request_id)
    response = ResponseEnvelope(
        type=env.type, metadata=env.metadata, rawdata=result, ok=True,
    )
    if guard.acquired:
        await guard.succeed(response)
    return result


async def handle_touch(ctx, env: Envelope) -> dict:
    """POST /api/session/touch：{touched: bool}（False = 会话已过期/不存在）。"""
    orchestrator, _, _ = _services(ctx)
    t0 = time.monotonic()
    try:
        touched = await orchestrator.touch(env.metadata.session_id or "")
    except AgentRuntimeError as exc:
        return _fail(env, exc, endpoint="touch", duration_ms=(time.monotonic() - t0) * 1000,
                     session=env.metadata.session_id or "",
                     request_id=env.metadata.request_id)
    return {"touched": touched}


async def handle_config_sync(ctx, env: Envelope) -> dict:
    """POST /api/session/config_sync：{ok, *_synced/*_deleted, affected_scopes?}。"""
    _, config_store, _ = _services(ctx)
    t0 = time.monotonic()
    try:
        result = await config_store.config_sync(dict(env.rawdata or {}))
    except AgentRuntimeError as exc:
        return _fail(env, exc, endpoint="config_sync",
                     duration_ms=(time.monotonic() - t0) * 1000,
                     templates=len((env.rawdata or {}).get("templates") or []),
                     scopes=len((env.rawdata or {}).get("scopes") or []),
                     request_id=env.metadata.request_id)
    logger.info("config_sync ok: result=%s at=%s duration_ms=%.1f",
                result, now_ts(), (time.monotonic() - t0) * 1000)
    return result


async def handle_cleanup(ctx, env: Envelope) -> dict:
    """POST /api/session/cleanup：运维批删 AgentServer Pod，委托 rm_facade。"""
    _, _, rm_facade = _services(ctx)
    rawdata = dict(env.rawdata or {})
    namespace = rawdata.get("namespace") or None
    label_selector = rawdata.get("label_selector") or None
    t0 = time.monotonic()
    try:
        cleaned = await rm_facade.cleanup(namespace=namespace, label_selector=label_selector)
    except AgentRuntimeError as exc:
        return _fail(env, exc, endpoint="cleanup", duration_ms=(time.monotonic() - t0) * 1000,
                     namespace=namespace or "-", label_selector=label_selector or "-",
                     request_id=env.metadata.request_id)
    logger.info("cleanup ok: namespace=%s label_selector=%s cleaned=%s duration_ms=%.1f",
                namespace or "-", label_selector or "-", cleaned,
                (time.monotonic() - t0) * 1000)
    return {"cleaned": cleaned}


def register_handlers(app) -> None:
    """把 4 个 handler 注册到 App（msg_type 即 REST 路径段 /api/session/{type}）。"""
    app.handle("route", summary="同步路由 + 占额度")(handle_route)
    app.handle("touch", summary="保活 / EOS，刷新老化")(handle_touch)
    app.handle("config_sync", summary="配置全量下发（templates + scopes）")(handle_config_sync)
    app.handle("cleanup", summary="运维批删 AgentServer Pod")(handle_cleanup)
