"""部署任务服务：任务生命周期、默认参数、前置建库、成功后自动注册实例。

执行模型：``run_deployment_executor_loop`` 为常驻后台协程（app lifespan 挂载，
模式对齐 heartbeat_scanner），周期认领 pending 任务；认领采用条件更新
（``status=pending → running``，DB 层原子，天然多副本安全）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.deployment import env_builder
from manager_server.core.deployment.env_builder import (
    build_env_custom,
    derive_db_connection,
    derive_instance_hosts,
    derive_product_databases,
    mask_sensitive_env,
    merge_env,
    validate_merged_env,
)
from manager_server.core.deployment.runner import DeployRunner
from manager_server.core.deployment.toolkit import (
    check_tool_available,
    parse_env_custom,
    parse_tool_defaults,
    read_tool_version,
)
from manager_server.core.instance.instance_service import InstanceService
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.utils import iso_datetime, utc_now
from manager_server.models.deployment_models import (
    DEPLOYMENT_DEFAULT_ENV_TABLE_DEF,
    DEPLOYMENT_LOG_TABLE_DEF,
    DEPLOYMENT_TASK_TABLE_DEF,
)
from manager_server.schemas.instance_schemas import CreateInstanceBody

_log = get_logger(__name__)
_TASK_TABLE = DEPLOYMENT_TASK_TABLE_DEF.table_name
_LOG_TABLE = DEPLOYMENT_LOG_TABLE_DEF.table_name
_DEFAULTS_TABLE = DEPLOYMENT_DEFAULT_ENV_TABLE_DEF.table_name
_DEFAULTS_ROW_ID = "default"

ACTIVE_STATUSES = ("pending", "running")
_TERMINAL_STATUSES = ("success", "failed", "cancelled", "interrupted")

# 认领轮询 / 孤儿对账间隔
_CLAIM_INTERVAL = 2.0
_RECONCILE_INTERVAL = 60.0


class DeploymentError(Exception):
    """业务错误（router 按 ``status_code`` 映射 HTTP 状态）。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _row_to_dict(row: Any) -> dict:
    # 注意：defaults_snapshot 含明文密码，仅执行器内部消费（_task_fields），不进 API 视图；
    # params.env 为用户请求覆盖，同样可能带密码，出参前掩码
    params = dict(getattr(row, "params", None) or {})
    if isinstance(params.get("env"), dict):
        params["env"] = mask_sensitive_env(params["env"])
    return {
        "deployment_id": getattr(row, "deployment_id", ""),
        "action": str(params.get("action") or "up"),
        "namespace": getattr(row, "namespace", ""),
        "jiuwenclaw_name": getattr(row, "jiuwenclaw_name", ""),
        "deploy_mode": getattr(row, "deploy_mode", ""),
        "status": getattr(row, "status", ""),
        "phase": getattr(row, "phase", ""),
        "progress": dict(getattr(row, "progress", None) or {}),
        "params": params,
        "tool_version": getattr(row, "tool_version", ""),
        "instance_jid": getattr(row, "instance_jid", None),
        "exit_code": getattr(row, "exit_code", None),
        "error_summary": getattr(row, "error_summary", None),
        "cancel_requested": bool(getattr(row, "cancel_requested", False)),
        "created_by": getattr(row, "created_by", ""),
        "created_at": iso_datetime(getattr(row, "created_at", None)),
        "updated_at": iso_datetime(getattr(row, "updated_at", None)),
        "started_at": iso_datetime(getattr(row, "started_at", None)),
        "finished_at": iso_datetime(getattr(row, "finished_at", None)),
    }


def _summary_of(row_dict: dict) -> dict:
    return {key: row_dict[key] for key in (
        "deployment_id", "action", "namespace", "jiuwenclaw_name", "deploy_mode", "status",
        "phase", "instance_jid", "error_summary", "created_by",
        "created_at", "started_at", "finished_at",
    )}


class DeploymentService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    # ---------- 任务生命周期 ----------

    async def create_task(self, body, actor: str) -> dict:
        available, reason = check_tool_available()
        if not available:
            raise DeploymentError(f"部署功能不可用: {reason}", status_code=503)
        namespace = str(body.namespace or "").strip()
        defaults = await self.get_defaults()
        overrides = dict(body.env or {})
        deploy_mode = str(body.deploy_mode or "full").strip()
        overrides.setdefault("AGENT_RUNTIME_DEPLOY_MODE", deploy_mode)
        merged = merge_env(defaults, overrides)
        validate_merged_env(merged, namespace=namespace)

        active = await self._handler.list_records(
            _TASK_TABLE,
            {"namespace": namespace, "status": list(ACTIVE_STATUSES)},
            limit=10,
            offset=0,
        )
        if active:
            raise DeploymentError(
                f"命名空间 {namespace} 已有进行中的部署任务 "
                f"({getattr(active[0], 'deployment_id', '?')})，待其结束后再发",
                status_code=409,
            )

        now = utc_now()
        deployment_id = f"dp-{uuid.uuid4().hex[:20]}"
        # deploy_mode 必须持久化进 params.env：_execute_task 认领时按
        # defaults_snapshot + params.env 重新 merge 生成 .env.custom，
        # 只存顶层字段会导致实际执行回落到 defaults 里的模式（E2E 实测回归）
        params = body.model_dump(exclude_none=True)
        params.setdefault("env", {})["AGENT_RUNTIME_DEPLOY_MODE"] = deploy_mode
        params["action"] = str(getattr(body, "action", "up") or "up")
        row_data = {
            "deployment_id": deployment_id,
            "namespace": namespace,
            "jiuwenclaw_name": str(body.jiuwenclaw_name or "").strip() or namespace,
            "deploy_mode": deploy_mode,
            "status": "pending",
            "phase": "pending",
            "params": params,
            "defaults_snapshot": defaults,
            "tool_version": read_tool_version(),
            "created_by": actor or "system",
            "created_at": now,
            "updated_at": now,
        }
        await self._handler.create(_TASK_TABLE, row_data)
        _log.info(
            "deployment_created",
            deployment_id=deployment_id,
            namespace=namespace,
            deploy_mode=deploy_mode,
            actor=actor,
        )
        return {"deployment_id": deployment_id, "status": "pending"}

    async def list_tasks(self, query) -> dict:
        page = max(int(query.page or 1), 1)
        page_size = min(max(int(query.page_size or 20), 1), 200)
        filters: dict[str, Any] = {}
        if query.status:
            filters["status"] = query.status
        if query.namespace:
            filters["namespace"] = query.namespace
        rows = list(
            await self._handler.list_records(
                _TASK_TABLE, filters or None, limit=10_000, offset=0,
                order_by=[("created_at", True)],
            )
        )
        search = (query.search or "").strip().lower()
        if search:
            rows = [
                r for r in rows
                if search in str(getattr(r, "deployment_id", "")).lower()
                or search in str(getattr(r, "namespace", "")).lower()
                or search in str(getattr(r, "jiuwenclaw_name", "")).lower()
                or search in str(getattr(r, "status", "")).lower()
            ]
        total = len(rows)
        offset = (page - 1) * page_size
        items = [_summary_of(_row_to_dict(r)) for r in rows[offset:offset + page_size]]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def get_task(self, deployment_id: str) -> dict | None:
        row = await self._handler.get(_TASK_TABLE, {"deployment_id": deployment_id})
        if row is None:
            return None
        return _row_to_dict(row)

    async def list_logs(self, deployment_id: str, *, after_seq: int = 0, limit: int = 500) -> dict | None:
        row = await self._handler.get(_TASK_TABLE, {"deployment_id": deployment_id})
        if row is None:
            return None
        limit = min(max(limit, 1), 2000)
        logs = list(
            await self._handler.list_records(
                _LOG_TABLE,
                {"deployment_id": deployment_id},
                limit=10_000,
                offset=0,
                order_by=[("seq", False)],
            )
        )
        items = [
            {
                "seq": getattr(r, "seq", 0),
                "content": getattr(r, "content", ""),
                "created_at": iso_datetime(getattr(r, "created_at", None)),
            }
            for r in logs
            if int(getattr(r, "seq", 0) or 0) > after_seq
        ][:limit]
        last_seq = items[-1]["seq"] if items else after_seq
        progress = dict(getattr(row, "progress", None) or {})
        return {
            "items": items,
            "last_seq": last_seq,
            "truncated": bool(progress.get("log_truncated", False)),
        }

    async def request_cancel(self, deployment_id: str) -> dict:
        row = await self._handler.get(_TASK_TABLE, {"deployment_id": deployment_id})
        if row is None:
            raise DeploymentError("任务不存在", status_code=404)
        status = str(getattr(row, "status", ""))
        if status == "pending":
            updated = await self._handler.update(
                _TASK_TABLE,
                {"deployment_id": deployment_id, "status": "pending"},
                {"status": "cancelled", "finished_at": utc_now(), "updated_at": utc_now()},
            )
            if updated is None:   # 并发被认领，按 running 处理
                status = "running"
            else:
                return {"deployment_id": deployment_id, "status": "cancelled"}
        if status == "running":
            await self._handler.update(
                _TASK_TABLE,
                {"deployment_id": deployment_id},
                {"cancel_requested": True, "updated_at": utc_now()},
            )
            return {"deployment_id": deployment_id, "status": "cancelling"}
        raise DeploymentError(f"任务已处于终态（{status}），无法取消", status_code=409)

    # ---------- 默认部署参数 ----------

    async def get_defaults(self) -> dict:
        row = await self._handler.get(_DEFAULTS_TABLE, {"id": _DEFAULTS_ROW_ID})
        if row is not None:
            stored = getattr(row, "env_json", None)
            if isinstance(stored, dict):
                return dict(stored)
        # 首次播种：.env.custom（版本化部署配置，含本版镜像 tag）是唯一真源——
        # global_vars 工具默认 / .env.example 占位值与版本化清单混排会产生看似完整
        # 实则版本不一致的默认参数，宁缺毋滥：.env.custom 缺失或未覆盖的键不播种，
        # 由操作员经 defaults 接口显式补全（deploy.sh 执行时对未设键按自身默认派生）。
        # 空值不入库：写了 KEY="" 会覆盖 deploy 工具的内置默认（如 root 密码）。
        seeded: dict[str, str] = {}
        for key, value in parse_env_custom().items():
            if key not in env_builder.ALLOWED_ENV_KEYS or not str(value).strip():
                continue
            try:
                seeded[key] = env_builder.validate_env_value(key, value)
            except ValueError:
                continue
        now = utc_now()
        try:
            await self._handler.create(
                _DEFAULTS_TABLE,
                {
                    "id": _DEFAULTS_ROW_ID,
                    "env_json": seeded,
                    "updated_at": now,
                    "updated_by": "system-seed",
                },
            )
        except Exception:  # noqa: BLE001
            pass   # 并发播种唯一键冲突：以先写者为准
        return seeded

    async def update_defaults(self, patch: dict, actor: str) -> dict:
        current = await self.get_defaults()
        # 前端回显掩码值原样传回时不覆盖
        incoming = {
            str(k).strip().upper(): v
            for k, v in (patch or {}).items()
            if v is not None and v != "******"
        }
        validated = merge_env({}, incoming)
        merged = {**current, **validated}
        # 强制键由服务端在每次部署时写死，不得落入存储（历史 bug：merge_env 的返回值
        # 含 FORCED_ENV，曾随本接口写库，导致后续任务创建报"不支持的配置键"）
        for forced_key in env_builder.FORCED_ENV:
            merged.pop(forced_key, None)
        await self._handler.update(
            _DEFAULTS_TABLE,
            {"id": _DEFAULTS_ROW_ID},
            {"env_json": merged, "updated_at": utc_now(), "updated_by": actor},
        )
        return merged


# ---------- 启动同步：manager up 注入的 .env.custom 快照 ----------

async def sync_defaults_from_snapshot(handler: DBHandler) -> int:
    """启动时按 ``MANAGER_DEPLOY_ENV_JSON``（manager up 渲染注入的 .env.custom 快照，
    非敏感键）刷新部署默认参数。

    注入键覆盖存量值——.env.custom 是这些键的权威来源，版本升级重新 manager up 后
    重启即自动跟进新镜像 tag；操作员经 defaults 接口配置的其他键保留。
    快照未注入（如本地源码运行）时不做任何事。
    """
    raw = (settings.deploy_env_snapshot or "").strip()
    if not raw:
        return 0
    try:
        snapshot = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log.warning("deployment_snapshot_invalid_json", error=str(exc))
        return 0
    updates: dict[str, str] = {}
    for raw_key, raw_value in snapshot.items():
        key = str(raw_key).strip().upper()
        if (
            key not in env_builder.ALLOWED_ENV_KEYS
            or env_builder.is_sensitive_key(key)   # 与注入侧同策：敏感键不入 defaults（Deployment env 明文）
            or raw_value is None
            or not str(raw_value).strip()
        ):
            continue
        try:
            updates[key] = env_builder.validate_env_value(key, str(raw_value))
        except ValueError:
            continue
    if not updates:
        return 0
    current = await DeploymentService(handler).get_defaults()
    merged = {**current, **updates}
    await handler.update(
        _DEFAULTS_TABLE,
        {"id": _DEFAULTS_ROW_ID},
        {"env_json": merged, "updated_at": utc_now(), "updated_by": "env-snapshot"},
    )
    _log.info("deployment_defaults_synced", keys=len(updates))
    return len(updates)


# ---------- 前置步骤：预建产品库 ----------

async def ensure_product_databases(env: dict, *, namespace: str) -> None:
    """部署前预建 gateway/web 库（deploy.sh 不建库；runtime 库由框架自建）。

    复用 foundation 的 ``init_database``（幂等；PG 走 AUTOCOMMIT + 23505 处理）：
    mysql/mariadb 按库隔离 ``<module>_<ns>``；postgresql 库共享、按 schema 隔离。
    """
    conn = derive_db_connection(env, tool_defaults=parse_tool_defaults())
    targets = derive_product_databases(env, namespace=namespace)
    if conn["db_type"] == "postgresql":
        from openjiuwen_runtime.foundation.db.postgresql_handler import (
            PostgreSQLHandler,
        )

        for item in targets:
            handler = PostgreSQLHandler(
                host=conn["host"],
                port=conn["port"],
                database=item["database"],
                schema=item["schema"] or "public",
                user=conn["user"],
                password=conn["password"],
            )
            try:
                await handler.init_database()
            finally:
                await handler.disconnect()
    else:
        from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler

        for item in targets:
            handler = MySQLHandler(
                host=conn["host"],
                port=conn["port"],
                database=item["database"],
                user=conn["user"],
                password=conn["password"],
            )
            try:
                await handler.init_database()
            finally:
                await handler.disconnect()


# ---------- 成功后置：自动注册实例 ----------

async def register_instance_for_namespace(handler: DBHandler, task: dict) -> tuple[str | None, str | None]:
    """部署成功后按命名空间推导 host 注册实例；返回 ``(instance_jid, note)``。

    复用 ``InstanceService.create``（含创建时探活；探活/注册失败不影响任务终态，
    记 note 提示手工登记）。同 host 实例已存在则直接复用（幂等重跑场景）。
    """
    namespace = task["namespace"]
    hosts = derive_instance_hosts(namespace)
    existing = await handler.list_records(
        "instance_info", {"gateway_host": hosts["gateway_host"]}, limit=1, offset=0
    )
    if existing:
        return str(getattr(existing[0], "jiuwenclaw_id", "")), "命中已有实例（同 Gateway host），跳过重复注册"
    body = CreateInstanceBody(
        jiuwenclaw_name=task.get("jiuwenclaw_name") or namespace,
        namespace=namespace,
        created_by=task.get("created_by") or "manager-deploy",
        gateway_host=hosts["gateway_host"],
        runtime_host=hosts["runtime_host"],
        user_web_host=hosts["user_web_host"],
        gateway_web_http_host=hosts["gateway_web_http_host"],
        gateway_web_ws_host=hosts["gateway_web_ws_host"],
        data={
            "deploy_source": "manager-deploy",
            "deployment_id": task["deployment_id"],
        },
    )
    try:
        row = await InstanceService(handler).create(body)
    except ValueError as exc:
        return None, f"部署成功但实例自动注册失败: {exc}（可手工登记）"
    # InstanceService.create 返回 dict（或行对象），两种形态都取 jiuwenclaw_id
    if isinstance(row, dict):
        jid = str(row.get("jiuwenclaw_id", "") or "")
    else:
        jid = str(getattr(row, "jiuwenclaw_id", "") or "")
    return jid, None


# ---------- 执行器循环 ----------

def _task_fields(row: Any) -> dict:
    """执行器内部使用的原始字段视图（datetime 保留原生类型）。"""
    return {
        "deployment_id": getattr(row, "deployment_id", ""),
        "namespace": getattr(row, "namespace", ""),
        "jiuwenclaw_name": getattr(row, "jiuwenclaw_name", ""),
        "deploy_mode": getattr(row, "deploy_mode", "full"),
        "params": dict(getattr(row, "params", None) or {}),
        "defaults_snapshot": dict(getattr(row, "defaults_snapshot", None) or {}),
        "created_by": getattr(row, "created_by", "system"),
    }


async def _execute_task(handler: DBHandler, task: dict) -> None:
    """单任务执行：前置建库（仅 up）→ 子进程部署/卸载 → 成功后自动注册（仅 up）。"""
    deployment_id = task["deployment_id"]
    namespace = task["namespace"]
    action = str((task.get("params") or {}).get("action") or "up")
    try:
        merged = merge_env(
            task.get("defaults_snapshot") or {},
            dict((task.get("params") or {}).get("env") or {}),
        )
        if action != "down":
            await ensure_product_databases(merged, namespace=namespace)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "deployment_precheck_failed", deployment_id=deployment_id, error=str(exc)
        )
        await handler.update(
            _TASK_TABLE,
            {"deployment_id": deployment_id},
            {
                "status": "failed",
                "phase": "preflight",
                "error_summary": f"部署前置失败: {exc}",
                "finished_at": utc_now(),
                "updated_at": utc_now(),
            },
        )
        return

    env_content = build_env_custom(merged, namespace=namespace)
    wait_timeout = (task.get("params") or {}).get("wait_timeout_secs")
    runner = DeployRunner(
        handler, task, env_content, wait_timeout_secs=wait_timeout, action=action
    )
    result = await runner.run()

    if result.get("status") != "success":
        return
    if action == "down":
        # 卸载成功：不动实例记录与数据库（数据保留），提示后续手工清理
        row = await handler.get(_TASK_TABLE, {"deployment_id": deployment_id})
        progress = dict(getattr(row, "progress", None) or {}) if row is not None else {}
        progress["undeploy"] = "卸载完成；实例记录与业务数据库未自动清理，可在集群管理删除实例"
        await handler.update(
            _TASK_TABLE,
            {"deployment_id": deployment_id},
            {"progress": progress, "updated_at": utc_now()},
        )
        return
    try:
        jid, note = await register_instance_for_namespace(handler, task)
    except Exception as exc:  # noqa: BLE001
        jid, note = None, f"实例自动注册异常: {exc}"
    # 不能用 result 重建 progress：runner.run() 只返回状态三元组，
    # 终态进度已由 runner 落库——此处读回现有进度仅附加 auto_register 备注，
    # 否则会把组件时间线/日志截断标记清空（E2E 实测回归）
    row = await handler.get(_TASK_TABLE, {"deployment_id": deployment_id})
    progress = dict(getattr(row, "progress", None) or {}) if row is not None else {}
    if note:
        progress["auto_register"] = note
    await handler.update(
        _TASK_TABLE,
        {"deployment_id": deployment_id},
        {
            "instance_jid": jid or None,
            "progress": progress,
            "updated_at": utc_now(),
        },
    )


async def reconcile_stale(handler: DBHandler) -> int:
    """running 任务心跳超时 → interrupted（owner 副本死亡/重启兜底；幂等可重发）。"""
    threshold = max(60, int(settings.deploy_heartbeat_stale_seconds))
    now = utc_now()
    interrupted = 0
    rows = await handler.list_records(
        _TASK_TABLE, {"status": "running"}, limit=10_000, offset=0
    )
    for row in rows:
        stamp: datetime | None = getattr(row, "heartbeat_at", None) or getattr(
            row, "started_at", None
        )
        if stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if (now - stamp).total_seconds() <= threshold:
            continue
        await handler.update(
            _TASK_TABLE,
            {"deployment_id": getattr(row, "deployment_id", "")},
            {
                "status": "interrupted",
                "error_summary": (
                    "执行方心跳超时（manager-server 重启或副本死亡），任务中断；"
                    "deploy.sh 幂等，重新发起部署即可恢复"
                ),
                "finished_at": now,
                "updated_at": now,
            },
        )
        interrupted += 1
    if interrupted:
        _log.warning("deployment_reconcile_interrupted", count=interrupted)
    return interrupted


async def run_deployment_executor_loop(stop: asyncio.Event, handler: DBHandler, redis) -> None:  # noqa: ARG001
    """后台执行循环：认领 pending 任务（条件更新原子认领），并发受 Semaphore 限制。

    同命名空间同时只跑一个任务（认领前 DB 复查 + 本副本 inflight 集合）；
    redis 参数仅为对齐其他后台循环签名，认领不依赖 redis。
    """
    sem = asyncio.Semaphore(max(1, int(settings.deploy_max_concurrent)))
    inflight: dict[str, asyncio.Task] = {}
    last_reconcile = 0.0
    _log.info("deployment_executor_started")
    while not stop.is_set():
        try:
            for namespace, task in list(inflight.items()):
                if task.done():
                    inflight.pop(namespace, None)
            now = asyncio.get_running_loop().time()
            if now - last_reconcile >= _RECONCILE_INTERVAL:
                last_reconcile = now
                await reconcile_stale(handler)
            if len(inflight) < max(1, int(settings.deploy_max_concurrent)):
                pending = list(
                    await handler.list_records(
                        _TASK_TABLE,
                        {"status": "pending"},
                        limit=50,
                        offset=0,
                        order_by=[("created_at", False)],
                    )
                )
                for row in pending:
                    if len(inflight) >= max(1, int(settings.deploy_max_concurrent)):
                        break
                    task_fields = _task_fields(row)
                    namespace = task_fields["namespace"]
                    if namespace in inflight:
                        continue
                    running = await handler.list_records(
                        _TASK_TABLE,
                        {"namespace": namespace, "status": "running"},
                        limit=1,
                        offset=0,
                    )
                    if running:
                        continue   # 其他副本正在跑该 ns
                    claimed = await handler.update(
                        _TASK_TABLE,
                        {"deployment_id": task_fields["deployment_id"], "status": "pending"},
                        {
                            "status": "running",
                            "started_at": utc_now(),
                            "heartbeat_at": utc_now(),
                            "updated_at": utc_now(),
                        },
                    )
                    if claimed is None:
                        continue   # 被其他副本抢先认领/已取消

                    async def _run(t: dict = task_fields) -> None:
                        async with sem:
                            await _execute_task(handler, t)

                    inflight[namespace] = asyncio.create_task(_run())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _log.warning("deployment_executor_error", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=_CLAIM_INTERVAL)
        except asyncio.TimeoutError:
            pass
    if inflight:
        await asyncio.gather(*inflight.values(), return_exceptions=True)
    _log.info("deployment_executor_stopped")
