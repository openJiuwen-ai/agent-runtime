# coding: utf-8
"""调试诊断只读端点（GET /debug/*，与 /healthz 同款裸 FastAPI 路由）。

定位问题用：实例/依赖/后台任务总览、单会话与 scope 池的 Redis 状态、DB 配置、
进程内请求统计与最近错误。全部**只读**，不写任何 Redis/DB/K8s 状态。

访问控制：默认开放（与业务端点一致，靠网络边界——Service ClusterIP 仅集群内
可达）。输出对 secrets 脱敏（redact()：敏感 key → "***"，URL 剥 userinfo）。

注意（main.py get_type_hints 陷阱同样适用）：``Request``/``JSONResponse``
必须顶层导入；query 参数一律 ``request.query_params.get(...)`` 读取，不在
签名里声明，避免被 FastAPI 当成必填参数。

多副本 LB 后命中哪个实例就是哪个实例的数据——响应带 ``instance_id`` 标识
应答者；要看指定副本请直连 Pod IP。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import time
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("agent_runtime.debug")

# redact(): key（小写后）含下列标记 → 值打码
SECRET_KEY_MARKERS = ("password", "secret", "token", "kubeconfig",
                      "credential", "api_key")
_REDACTED = "***"
_URL_CRED_RE = re.compile(r"(?<=//)[^/@:]+:[^/@]+@")


# ---------------------------------------------------------------- 脱敏


def _redact_url(url: str) -> str:
    """URL 里的 userinfo 凭据打码：redis://u:p@h/2 → redis://***@h/2。"""
    return _URL_CRED_RE.sub(_REDACTED + "@", url)


def redact(value: Any, *, max_depth: int = 6) -> Any:
    """递归脱敏：敏感 key → "***"；URL 值剥凭据；JSON 字符串深入内部；其余原样。"""
    if max_depth <= 0:
        return _REDACTED
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if any(m in key.lower() for m in SECRET_KEY_MARKERS):
                out[key] = _REDACTED if v not in (None, "") else v
            else:
                out[key] = redact(v, max_depth=max_depth - 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, max_depth=max_depth - 1) for v in value]
    if isinstance(value, str):
        if "://" in value:
            return _redact_url(value)
        # 嵌套 JSON 字符串（如 pod_spec_json / resolve 缓存里的 template JSON）
        if value[:1] in ("{", "["):
            try:
                parsed = json.loads(value)
            except ValueError:
                return value
            return json.dumps(redact(parsed, max_depth=max_depth - 1),
                              ensure_ascii=False)
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return repr(value)[:200]


def _clamp_limit(request: Request, default: int, lo: int, hi: int) -> int:
    raw = request.query_params.get("limit", "")
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


# ---------------------------------------------------------------- 基础设施


def _sysctx_or_503(request: Request) -> Any:
    return getattr(request.app.state, "sysctx", None)


def _debug_endpoint(func: Callable[..., Any]) -> Callable[..., Any]:
    """统一错误面：任何异常 → 503 JSON + 服务端堆栈（绝不裸 500）。

    注意：**不用 functools.wraps**——wraps 会把内层 (request, sysctx) 的
    annotations/signature 复制到包装层，FastAPI 据此会把 sysctx 当 query
    参数（main.py 记载的 get_type_hints 陷阱同源）。这里只拷贝名字与 doc。
    """
    async def wrapper(request: Request) -> Any:
        sysctx = _sysctx_or_503(request)
        if sysctx is None:
            return JSONResponse(
                status_code=503, content={"ok": False, "detail": "sysctx not ready"},
            )
        try:
            content = await func(request, sysctx)
            content.setdefault("ok", True)
            content.setdefault("instance_id", sysctx.instance_id)
            content.setdefault("generated_at", round(time.time(), 3))
            return content
        except _DebugNotFound as exc:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "detail": str(exc)},
            )
        except _DebugBadRequest as exc:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "detail": str(exc)},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("debug endpoint failed: path=%s", request.url.path)
            return JSONResponse(
                status_code=503,
                content={"ok": False,
                         "detail": f"{type(exc).__name__}: {exc}"[:300]},
            )

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


class _DebugNotFound(Exception):
    pass


class _DebugBadRequest(Exception):
    pass


# ---------------------------------------------------------------- 端点实现


@_debug_endpoint
async def _overview(request: Request, sysctx: Any) -> dict[str, Any]:
    """实例总览：版本/配置摘要（脱敏）+ 依赖 readiness + 后台任务与 leader。"""
    arc = sysctx.arc
    settings = sysctx.settings
    try:
        readiness = await sysctx.readiness()
    except Exception:  # noqa: BLE001 - readiness 自身失败也展示
        readiness = {"error": "readiness() raised"}
    return {
        "mode": arc.mode,
        "uptime_sec": round(time.time() - request.app.state.metrics.started_at, 1),
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "config": redact({
            "mode": arc.mode,
            "default_namespace": arc.default_namespace,
            "sweep_interval": arc.sweep_interval,
            "autoscale_interval": arc.autoscale_interval,
            "reclaim_interval": arc.reclaim_interval,
            "watch_interval": arc.watch_interval,
            "reconcile_interval": arc.reconcile_interval,
            "scope_full_timeout": arc.scope_full_timeout,
            "default_session_ttl": arc.default_session_ttl,
            "kubeconfig": arc.kubeconfig,
            "service_host": getattr(settings, "host", None),
            "service_port": getattr(settings, "port", None),
            "redis_url": getattr(settings, "redis_url", None),
            "db_type": getattr(settings, "db_type", None),
            "db_host": getattr(settings, "db_host", None),
            "db_name": getattr(settings, "db_name", None),
        }),
        "readiness": readiness,
        "jobs": await sysctx.jobs_snapshot(),
    }


@_debug_endpoint
async def _session(request: Request, sysctx: Any) -> dict[str, Any]:
    """单会话状态：HASH / 到期 / 所属 scope 等待队列 / 绑定 Pod。"""
    session_id = (request.query_params.get("session_id") or "").strip()
    if not session_id:
        raise _DebugBadRequest("session_id is required")
    sm_state = sysctx.sm_sweeper.state
    data = await sm_state.session_hash(session_id)
    if not data:
        raise _DebugNotFound(f"session not found: {session_id}")
    scope_id = data.get("scope_id", "")
    pod_id = data.get("pod_id", "")
    score = await sm_state.session_expiry_score(session_id)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "session": data,
        "expiry_score": score,
        "ttl_remaining_s": (round(score - time.time(), 1)
                            if score is not None else None),
    }
    if scope_id:
        payload["scope"] = {
            "scope_id": scope_id,
            "waiters": await sm_state.waiter_count(scope_id),
            "session_count": await sm_state.scope_session_count(scope_id),
            "candidate_pods": await sm_state.scope_pod_ids(scope_id),
        }
    if scope_id and pod_id:
        payload["pod"] = {
            "pod_id": pod_id,
            "sse_url": await sm_state.pod_sse_url(scope_id, pod_id),
            "deploy_ver": await sm_state.pod_deploy_ver(scope_id, pod_id),
            "session_ids_on_pod": await sm_state.pod_session_ids(scope_id, pod_id),
        }
    return payload


@_debug_endpoint
async def _scope(request: Request, sysctx: Any) -> dict[str, Any]:
    """单 scope 池状态：RM 池/逐 Pod 详情 + SM 等待队列/路由定义。"""
    scope_id = (request.query_params.get("scope_id") or "").strip()
    if not scope_id:
        raise _DebugBadRequest("scope_id is required")
    limit = _clamp_limit(request, default=50, lo=1, hi=500)
    rm_state = sysctx.rm_sweeper.state
    sm_state = sysctx.sm_sweeper.state
    config_store = sysctx.sm_config_store

    snapshot = await config_store.routing_snapshot_view()
    routing = next(
        (s.to_payload() for s in snapshot.scopes if s.scope_id == scope_id), None
    )
    cfg = await rm_state.load_scope_config(scope_id)
    has_any = cfg or await rm_state.pod_count(scope_id) > 0
    if (not has_any and not await sm_state.scope_session_count(scope_id)
            and routing is None):
        raise _DebugNotFound(f"scope not found: {scope_id}")

    pod_ids = await rm_state.pod_ids(scope_id)
    idle = set(await rm_state.idle_pods(scope_id))
    pods = []
    for pod_id in pod_ids[:limit]:
        info = await rm_state.pod_info(pod_id)
        pods.append({
            "pod_id": pod_id,
            "idle": pod_id in idle,
            "health_fails": await rm_state.health_fails(pod_id),
            "idle_since": await rm_state.idle_since(pod_id) or None,
            **redact(info),
        })
    return {
        "scope_id": scope_id,
        "rm": {
            "pod_count": await rm_state.pod_count(scope_id),
            "idle_count": len(idle),
            "deploying_count": await rm_state.deploying_count(scope_id),
            "deploy_followers": await rm_state.deploy_follower_count(scope_id),
            "scope_config": redact(cfg),
            "pods": pods,
            "total_pods": len(pod_ids),
            "truncated": len(pod_ids) > limit,
        },
        "sm": {
            "waiters": await sm_state.waiter_count(scope_id),
            "session_count": await sm_state.scope_session_count(scope_id),
            "candidate_pods": await sm_state.scope_pod_ids(scope_id),
            "routing": routing,   # 快照里的定义（index/模板/规则）；不在快照为 None
        },
    }


@_debug_endpoint
async def _scopes(request: Request, sysctx: Any) -> dict[str, Any]:
    """全部 scope 枚举（SCAN）+ 每 scope 一行摘要。"""
    limit = _clamp_limit(request, default=100, lo=1, hi=500)
    rm_state = sysctx.rm_sweeper.state
    scope_ids = await rm_state.known_scope_ids()
    scopes = []
    for scope_id in scope_ids[:limit]:
        cfg = await rm_state.load_scope_config(scope_id)
        scopes.append({
            "scope_id": scope_id,
            "pods": await rm_state.pod_count(scope_id),
            "idle": len(await rm_state.idle_pods(scope_id)),
            "deploying": await rm_state.deploying_count(scope_id),
            "max_pods": cfg.get("max_pods"),
            "min_idle_pods": cfg.get("min_idle_pods"),
        })
    return {
        "scopes": scopes,
        "total": len(scope_ids),
        "truncated": len(scope_ids) > limit,
    }


@_debug_endpoint
async def _config(request: Request, sysctx: Any) -> dict[str, Any]:
    """DB 配置（routing scopes + templates，脱敏）+ 路由快照观测。"""
    config_store = sysctx.sm_config_store
    sm_state = sysctx.sm_sweeper.state
    rm_state = sysctx.rm_sweeper.state

    snapshot_exists = bool(await sm_state.routing_snapshot_raw())
    snapshot = await config_store.routing_snapshot_view()

    return {
        "routing_scopes": [s.to_payload() for s in await config_store.list_scopes()],
        "templates": redact(await config_store.list_templates()),
        "routing_snapshot": {
            "exists": snapshot_exists,
            "ver": snapshot.ver,
            "scope_count": len(snapshot.scopes),
            "template_count": len(snapshot.templates),
        },
        "redis": {
            "rm_scope_configs": len(await rm_state.known_scope_ids()),
            "rm_registered_pods": len(await rm_state.all_pod_ids()),
        },
    }


@_debug_endpoint
async def _stats(request: Request, sysctx: Any) -> dict[str, Any]:
    """进程内请求统计（per-endpoint 计数 / 延迟分位 / 错误码分布）。"""
    snapshot = request.app.state.metrics.snapshot()
    snapshot["pid"] = os.getpid()
    return snapshot


@_debug_endpoint
async def _recent_errors(request: Request, sysctx: Any) -> dict[str, Any]:
    """最近错误环形缓冲（新在前；单进程视角）。"""
    limit = _clamp_limit(request, default=50, lo=1, hi=200)
    return {"errors": request.app.state.metrics.recent_errors(limit)}


# ---------------------------------------------------------------- 注册


def register_debug_api(app: Any, *, registry: Any) -> None:
    """把 /debug/* 挂到 App 的裸 FastAPI 上（create_app 里调用，紧邻 healthz）。"""
    asgi = app.asgi
    asgi.get("/debug/overview", summary="diagnostics: instance overview")(_overview)
    asgi.get("/debug/session", summary="diagnostics: one session state")(_session)
    asgi.get("/debug/scope", summary="diagnostics: one scope pool state")(_scope)
    asgi.get("/debug/scopes", summary="diagnostics: all scopes summary")(_scopes)
    asgi.get("/debug/config", summary="diagnostics: db config + redis caches")(_config)
    asgi.get("/debug/stats", summary="diagnostics: request metrics")(_stats)
    asgi.get("/debug/recent_errors", summary="diagnostics: recent errors")(_recent_errors)
