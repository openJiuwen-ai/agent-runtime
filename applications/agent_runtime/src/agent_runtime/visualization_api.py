# coding: utf-8
"""可视化只读端点（GET /visualization/*，与 /healthz 同款裸 FastAPI 路由）。

前缀 2026-08 由 /debug 更名（对外名称去敏感化，行为零变化）。

定位问题用：实例/依赖/后台任务总览、单会话与 scope 池的 Redis 状态、DB 配置、
进程内请求统计与最近错误、per-scope 历史趋势采样与系统评估报告（后两者与
stats.scopes 段为 2026-09 自评估数据层视图，读 Redis、全局视角）。全部**只读**，
不写任何 Redis/DB/K8s 状态。

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

logger = logging.getLogger("agent_runtime.visualization")

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


def _visualization_endpoint(func: Callable[..., Any]) -> Callable[..., Any]:
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
        except _VisualizationNotFound as exc:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "detail": str(exc)},
            )
        except _VisualizationBadRequest as exc:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "detail": str(exc)},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("visualization endpoint failed: path=%s", request.url.path)
            return JSONResponse(
                status_code=503,
                content={"ok": False,
                         "detail": f"{type(exc).__name__}: {exc}"[:300]},
            )

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


class _VisualizationNotFound(Exception):
    pass


class _VisualizationBadRequest(Exception):
    pass


# ---------------------------------------------------------------- 端点实现


@_visualization_endpoint
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
            "default_session_ttl": arc.default_session_ttl,
            "eval_sample_interval": arc.eval_sample_interval,
            "eval_interval": arc.eval_interval,
            "eval_llm_enabled": bool(arc.eval_llm_base_url and arc.eval_llm_model),
            "eval_pod_budget": arc.eval_pod_budget,
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


@_visualization_endpoint
async def _session(request: Request, sysctx: Any) -> dict[str, Any]:
    """单会话状态：HASH / 到期 / 所属 scope 会话数 / 绑定 Pod。"""
    session_id = (request.query_params.get("session_id") or "").strip()
    if not session_id:
        raise _VisualizationBadRequest("session_id is required")
    sm_state = sysctx.sm_sweeper.state
    data = await sm_state.session_hash(session_id)
    if not data:
        raise _VisualizationNotFound(f"session not found: {session_id}")
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


@_visualization_endpoint
async def _scope(request: Request, sysctx: Any) -> dict[str, Any]:
    """单 scope 池状态：RM 池/逐 Pod 详情 + SM 容量闸门/会话/路由定义。"""
    scope_id = (request.query_params.get("scope_id") or "").strip()
    if not scope_id:
        raise _VisualizationBadRequest("scope_id is required")
    limit = _clamp_limit(request, default=50, lo=1, hi=500)
    rm_state = sysctx.rm_sweeper.state
    sm_state = sysctx.sm_sweeper.state
    config_store = sysctx.sm_config_store

    snapshot = await config_store.routing_snapshot_view()
    scope_def = next(
        (s for s in snapshot.scopes if s.scope_id == scope_id), None
    )
    routing = scope_def.to_payload() if scope_def else None
    template = snapshot.templates.get(scope_def.template_id) if scope_def else None
    cfg = await rm_state.load_scope_config(scope_id)
    has_any = cfg or await rm_state.pod_count(scope_id) > 0
    session_count = await sm_state.scope_session_count(scope_id)
    if (not has_any and not session_count and routing is None):
        raise _VisualizationNotFound(f"scope not found: {scope_id}")

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
    # 生效分类(collector 同款规则;单 scope 详情不整表扫描)
    if scope_def is None:
        phase = "orphan_rm"
    elif not scope_def.is_active() or template is None or not template.enabled:
        phase = "disabled"
    elif not cfg:
        phase = "missing_rm_cfg"
    else:
        phase = "active"
    # SM 容量闸门(模板策略字段 + 派生值;orchestrator.py 同式)。
    # 2026-09 场景 F 快失败后无等待队列:max_waiters/waiter_utilization 已废,
    # route 总预算 = ready_timeout + ROUTE_BUDGET_MARGIN_SEC(10)
    capacity: dict[str, Any] | None = None
    if template is not None:
        scope_concurrency = template.scope_concurrency
        capacity = {
            "template_id": scope_def.template_id,
            "template_enabled": bool(template.enabled),
            "scope_enabled": bool(scope_def.enabled),
            "expires_at": routing["expires_at"] if routing else None,
            "scope_concurrency": scope_concurrency,
            "pod_concurrency": template.pod_concurrency,
            "session_ttl": template.session_ttl,
            "pod_ttl": template.pod_ttl,
            "min_idle_pods": template.min_idle_pods,
            "max_pods": template.max_pods,   # ⌈sc/pc⌉(Template 派生)
            "session_utilization": (
                round(session_count / scope_concurrency, 3)
                if scope_concurrency else None
            ),
            "route_budget_sec": round(
                float(template.ready_timeout or 0) + 10.0, 1
            ),
        }
    return {
        "scope_id": scope_id,
        "phase": phase,
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
            "session_count": session_count,
            "candidate_pods": await sm_state.scope_pod_ids(scope_id),
            "capacity": capacity,     # 生效容量闸门(None=快照无此 scope 的孤儿)
            "routing": routing,   # 快照里的定义（index/模板/规则）；不在快照为 None
        },
    }


@_visualization_endpoint
async def _scopes(request: Request, sysctx: Any) -> dict[str, Any]:
    """全部 scope 清单（RM 键 ∪ 路由快照）+ 每 scope 一行容量摘要。

    2026-09 起：摘要补 SM 侧容量字段（scope_concurrency/session_count/phase/
    template 归属）——scope 重构后这些只在模板列表可见，容量推导链
    （max_pods=⌈sc/pc⌉）在 scope 维度断了，本端点补齐。行读放大 ~5 读/scope，
    大规模部署调小 limit。
    """
    limit = _clamp_limit(request, default=100, lo=1, hi=500)
    rows = await sysctx.eval_collector.scope_inventory()
    scopes = []
    for row in rows[:limit]:
        routing, template = row.get("routing"), row.get("template")
        scopes.append({
            "scope_id": row["scope_id"],
            "phase": row["phase"],
            "template_id": routing.template_id if routing else None,
            "scope_enabled": routing.enabled if routing else None,
            "expires_at": (
                routing.expires_at.isoformat()
                if routing is not None and routing.expires_at is not None else None
            ),
            "pods": row["pods"],
            "idle": row["idle"],
            "deploying": row["deploying"],
            "session_count": row["session_count"],
            "max_pods": template.max_pods if template
            else row["rm_config"].get("max_pods"),
            "min_idle_pods": template.min_idle_pods if template
            else row["rm_config"].get("min_idle_pods"),
            "scope_concurrency": template.scope_concurrency if template else None,
            "pod_concurrency": template.pod_concurrency if template else None,
            "session_ttl": template.session_ttl if template else None,
        })
    return {
        "scopes": scopes,
        "total": len(rows),
        "truncated": len(rows) > limit,
    }


@_visualization_endpoint
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


@_visualization_endpoint
async def _stats(request: Request, sysctx: Any) -> dict[str, Any]:
    """请求统计：per-endpoint（本进程视角）+ per-scope（Redis 全副本聚合）。

    scopes 段是 2026-09 自评估数据层的只读视图：route/acquire 计数与扩缩容
    事件经每副本 5s 批量 flush 到 ``{agent_runtime:eval}:ct:scope:{sid}``，
    任意副本读到的都是全局聚合——与 endpoints 段（命中实例视角）不同。
    """
    snapshot = request.app.state.metrics.snapshot()
    snapshot["pid"] = os.getpid()
    snapshot["scopes"] = await _scope_counters(sysctx)
    return snapshot


async def _scope_counters(sysctx: Any) -> dict[str, dict[str, int]]:
    """全 scope 计数聚合（枚举源 = RM 已知 ∪ 快照 scope，不 SCAN eval 键）。"""
    snapshot = await sysctx.sm_config_store.routing_snapshot_view()
    scope_ids = sorted(
        set(await sysctx.rm_sweeper.state.known_scope_ids())
        | {s.scope_id for s in snapshot.scopes}
    )
    out: dict[str, dict[str, int]] = {}
    for scope_id in scope_ids:
        counters = await sysctx.eval_state.read_counters(scope_id)
        if counters:
            out[scope_id] = counters
    return out


@_visualization_endpoint
async def _recent_errors(request: Request, sysctx: Any) -> dict[str, Any]:
    """最近错误环形缓冲（新在前；单进程视角）。"""
    limit = _clamp_limit(request, default=50, lo=1, hi=200)
    return {"errors": request.app.state.metrics.recent_errors(limit)}


@_visualization_endpoint
async def _history(request: Request, sysctx: Any) -> dict[str, Any]:
    """单 scope 历史趋势采样（sys_sample 30s 一拍；窗口默认 1h，新在前）。

    数据在 Redis（25h TTL）——重启不丢、全局一致；points 字段为紧凑短键
    （t/p/i/d/s/w/rt/ef/eq/en/ad/ar/rc/dd，语义见 evaluation/state.py）。
    """
    scope_id = (request.query_params.get("scope_id") or "").strip()
    if not scope_id:
        raise _VisualizationBadRequest("scope_id is required")
    try:
        window_sec = int(request.query_params.get("window_sec", "3600"))
    except ValueError:
        window_sec = 3600
    window_sec = max(60, min(86400, window_sec))
    limit = _clamp_limit(request, default=240, lo=1, hi=1440)
    since = time.time() - window_sec
    points = await sysctx.eval_state.samples(scope_id, since, limit=limit)
    return {
        "scope_id": scope_id,
        "window_sec": window_sec,
        "points": list(reversed(points)),   # 新在前
        "counters_current": await sysctx.eval_state.read_counters(scope_id),
    }


@_visualization_endpoint
async def _evaluation(request: Request, sysctx: Any) -> dict[str, Any]:
    """系统评估报告（sys_eval job 周期产出；**全局视角**，读 Redis）。

    latest 为完整报告（findings/trend/caveats）；history 为瘦身条目（去
    findings 只留 summary）。无报告（评估间隔未到/从未跑过）返回
    latest=null——正常态不 404。LLM 段只含 status/model/latency，无凭证。
    """
    limit = _clamp_limit(request, default=10, lo=1, hi=50)
    latest = await sysctx.eval_state.latest_report()
    history = await sysctx.eval_state.list_reports(limit)
    return {
        "latest": redact(latest) if latest else None,
        "history": redact(history),
    }


# ---------------------------------------------------------------- 注册


def register_visualization_api(app: Any, *, registry: Any) -> None:
    """把 /visualization/* 挂到 App 的裸 FastAPI 上（create_app 里调用，紧邻 healthz）。"""
    asgi = app.asgi
    asgi.get("/visualization/overview", summary="diagnostics: instance overview")(_overview)
    asgi.get("/visualization/session", summary="diagnostics: one session state")(_session)
    asgi.get("/visualization/scope", summary="diagnostics: one scope pool state")(_scope)
    asgi.get("/visualization/scopes", summary="diagnostics: all scopes summary")(_scopes)
    asgi.get("/visualization/config", summary="diagnostics: db config + redis caches")(_config)
    asgi.get("/visualization/stats", summary="diagnostics: request metrics")(_stats)
    asgi.get("/visualization/recent_errors", summary="diagnostics: recent errors")(_recent_errors)
    asgi.get("/visualization/history", summary="diagnostics: per-scope trend samples")(_history)
    asgi.get("/visualization/evaluation", summary="diagnostics: system evaluation report")(_evaluation)
