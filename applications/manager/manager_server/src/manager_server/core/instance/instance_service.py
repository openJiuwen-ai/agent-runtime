"""实例纳管与 Gateway / Runtime 心跳。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance.config_host_probe import (
    require_config_hosts_reachable,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.models.instance_models import INSTANCE_INFO_TABLE_DEF
from manager_server.models.link_binding_models import INSTANCE_LINK_BINDING_TABLE_DEF
from manager_server.schemas.instance_schemas import (
    CreateInstanceBody,
    InstanceDetail,
    InstanceListQuery,
    InstanceSummary,
    InstanceUpdateBody,
)

logger = logging.getLogger(__name__)

_INSTANCE_TABLE = INSTANCE_INFO_TABLE_DEF.table_name
_LOG_MASKING_SEEDED_KEY = "log_masking_seeded"
# 仍留在 instance_info.data 中的用户面反代上游（user_web_* 已独立成列）
_USER_FACE_HOST_KEYS = (
    "gateway_web_http_host",
    "gateway_web_ws_host",
)
_PROMOTED_USER_WEB_KEYS = (
    "user_web_host",
    "user_web_status",
    "user_web_last_alive",
)

ServiceSide = Literal["gateway", "runtime", "user_web"]

_DB_INSTANCE_SORT_FIELDS = frozenset({
    "jiuwenclaw_name",
    "namespace",
    "gateway_status",
    "gateway_last_alive",
    "runtime_status",
    "runtime_last_alive",
    "user_web_status",
    "user_web_last_alive",
    "updated_at",
})
_DEFAULT_INSTANCE_ORDER_BY: list[tuple[str, bool]] = [("updated_at", True)]

_MAX_JIUWENCLAW_ID_ATTEMPTS = 10
_HOST_MAX_LEN = 512
_NAMESPACE_MAX_LEN = 64

# gateway_status / runtime_status 从这些状态切到 online 时触发全量配置下发
_GATEWAY_STATUS_NEEDS_FULL_SYNC = frozenset({"pending", "offline"})
_RUNTIME_STATUS_NEEDS_FULL_SYNC = frozenset({"pending", "offline"})


def _norm_namespace(value: str | None) -> str:
    text = str(value or "").strip()
    return (text[:_NAMESPACE_MAX_LEN] if text else "default")


def _norm_host(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().rstrip("/")
    return text[:_HOST_MAX_LEN] if text else None


async def _find_config_host_conflict(
    handler: DBHandler,
    host: str,
    *,
    column: str,
    exclude_jiuwenclaw_id: str | None = None,
) -> Any | None:
    """若 host 已被其它实例同列 config_host 占用则返回冲突行。"""
    normalized = _norm_host(host)
    if not normalized:
        return None
    exclude = str(exclude_jiuwenclaw_id or "").strip() or None
    rows = await handler.list_records(
        _INSTANCE_TABLE,
        {column: normalized},
        limit=10,
        offset=0,
    )
    for row in rows:
        jid = str(getattr(row, "jiuwenclaw_id", "") or "")
        if exclude and jid == exclude:
            continue
        return row
    return None


async def _assert_config_host_available(
    handler: DBHandler,
    host: str,
    *,
    column: str,
    exclude_jiuwenclaw_id: str | None = None,
) -> None:
    conflict = await _find_config_host_conflict(
        handler,
        host,
        column=column,
        exclude_jiuwenclaw_id=exclude_jiuwenclaw_id,
    )
    if conflict is not None:
        raise ValueError(
            f"{column} already in use by instance "
            f"{getattr(conflict, 'jiuwenclaw_id', '')}"
        )


async def _find_user_face_host_conflict(
    handler: DBHandler,
    host: str,
    *,
    key: str,
    exclude_jiuwenclaw_id: str | None = None,
) -> Any | None:
    """若 host 已被其它实例 data 中同名字段占用则返回冲突行。"""
    if key not in _USER_FACE_HOST_KEYS:
        raise ValueError(f"unsupported user-face host key: {key}")
    normalized = _norm_host(host)
    if not normalized:
        return None
    exclude = str(exclude_jiuwenclaw_id or "").strip() or None
    rows = await handler.list_records(
        _INSTANCE_TABLE,
        {},
        limit=10_000,
        offset=0,
    )
    for row in rows:
        jid = str(getattr(row, "jiuwenclaw_id", "") or "")
        if exclude and jid == exclude:
            continue
        face = _user_face_hosts_from_data(_instance_data_dict(row))
        if face.get(key) == normalized:
            return row
    return None


async def _assert_user_face_host_available(
    handler: DBHandler,
    host: str,
    *,
    key: str,
    exclude_jiuwenclaw_id: str | None = None,
) -> None:
    conflict = await _find_user_face_host_conflict(
        handler,
        host,
        key=key,
        exclude_jiuwenclaw_id=exclude_jiuwenclaw_id,
    )
    if conflict is not None:
        raise ValueError(
            f"{key} already in use by instance "
            f"{getattr(conflict, 'jiuwenclaw_id', '')}"
        )


async def _assert_user_face_hosts_available(
    handler: DBHandler,
    hosts: dict[str, Any],
    *,
    exclude_jiuwenclaw_id: str | None = None,
) -> None:
    """对传入的用户面 host 做同字段去重（空值跳过）。"""
    for key in _USER_FACE_HOST_KEYS:
        if key not in hosts:
            continue
        value = _norm_host(hosts.get(key))
        if not value:
            continue
        await _assert_user_face_host_available(
            handler,
            value,
            key=key,
            exclude_jiuwenclaw_id=exclude_jiuwenclaw_id,
        )


def _matches_instance_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(getattr(row, "jiuwenclaw_id", "") or ""),
        str(getattr(row, "jiuwenclaw_name", "") or ""),
        str(getattr(row, "gateway_status", "") or ""),
        str(getattr(row, "runtime_status", "") or ""),
        str(getattr(row, "user_web_status", "") or ""),
        str(getattr(row, "namespace", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


def _instance_row_to_summary(row: Any) -> dict:
    data = _instance_data_dict(row)
    face = _user_face_hosts_from_data(data)
    summary = InstanceSummary(
        jiuwenclaw_id=row.jiuwenclaw_id,
        jiuwenclaw_name=row.jiuwenclaw_name,
        namespace=getattr(row, "namespace", None) or "default",
        space_id=row.space_id,
        gateway_host=str(getattr(row, "gateway_host", "") or ""),
        gateway_status=row.gateway_status,
        gateway_last_alive=iso_datetime(
            getattr(row, "gateway_last_alive", None)
        ),
        runtime_host=str(getattr(row, "runtime_host", "") or ""),
        runtime_status=row.runtime_status,
        runtime_last_alive=iso_datetime(
            getattr(row, "runtime_last_alive", None)
        ),
        user_web_host=_norm_host(getattr(row, "user_web_host", None)),
        user_web_status=str(getattr(row, "user_web_status", None) or "pending").strip()
        or "pending",
        user_web_last_alive=iso_datetime(
            getattr(row, "user_web_last_alive", None)
        ),
        gateway_web_http_host=face["gateway_web_http_host"],
        gateway_web_ws_host=face["gateway_web_ws_host"],
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(getattr(row, "updated_at", None)),
    )
    return summary.model_dump()


def _instance_row_to_detail(row: Any) -> InstanceDetail:
    return InstanceDetail(
        **_instance_row_to_summary(row),
        description=row.description,
        data=row.data if isinstance(row.data, dict) else row.data,
        created_by=row.created_by,
        updated_by=getattr(row, "updated_by", None),
    )


def _instance_data_dict(row: Any | None) -> dict[str, Any]:
    data = getattr(row, "data", None) if row is not None else None
    return dict(data) if isinstance(data, dict) else {}


def _strip_promoted_user_web_keys(data: dict[str, Any]) -> dict[str, Any]:
    """避免已提列字段继续滞留在 data JSON。"""
    cleaned = dict(data)
    for key in _PROMOTED_USER_WEB_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _user_face_hosts_from_data(data: dict[str, Any] | None) -> dict[str, str | None]:
    src = data if isinstance(data, dict) else {}
    return {key: _norm_host(src.get(key)) for key in _USER_FACE_HOST_KEYS}


def _merge_user_face_hosts_into_data(
    data: dict[str, Any] | None,
    hosts: dict[str, Any],
) -> dict[str, Any]:
    """把 gateway_web_* host 写入 data；空字符串删除对应键。"""
    merged = _strip_promoted_user_web_keys(dict(data or {}))
    for key in _USER_FACE_HOST_KEYS:
        if key not in hosts:
            continue
        value = _norm_host(hosts.get(key))
        if value:
            merged[key] = value
        else:
            merged.pop(key, None)
    return merged


async def is_log_masking_seeded(handler: DBHandler, jiuwenclaw_id: str) -> bool:
    """``instance_info.data.log_masking_seeded`` 为真时表示 builtin 种子已执行过。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return False
    row = await get_instance_row(handler, jid)
    return bool(_instance_data_dict(row).get(_LOG_MASKING_SEEDED_KEY))


async def create_instance_row(handler: DBHandler, row_data: dict[str, Any]) -> Any:
    now = utc_now()
    payload = dict(row_data)
    payload.setdefault("created_at", now)
    payload.setdefault("updated_at", now)
    payload.setdefault("namespace", "default")
    payload.setdefault("gateway_status", "pending")
    payload.setdefault("gateway_last_alive", None)
    payload.setdefault("runtime_status", "pending")
    payload.setdefault("runtime_last_alive", None)
    payload.setdefault("user_web_host", "")
    payload.setdefault("user_web_status", "pending")
    payload.setdefault("user_web_last_alive", None)
    payload.setdefault("space_id", "default")
    return await handler.create(_INSTANCE_TABLE, payload)


async def get_instance_row(handler: DBHandler, jiuwenclaw_id: str) -> Any | None:
    return await handler.get(_INSTANCE_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})


async def generate_unique_jiuwenclaw_id(handler: DBHandler) -> str:
    """生成 ``instance_info`` 中尚未占用的 ``jiuwenclaw_id``。"""
    for _ in range(_MAX_JIUWENCLAW_ID_ATTEMPTS):
        jiuwenclaw_id = new_uuid4()
        if await get_instance_row(handler, jiuwenclaw_id) is None:
            return jiuwenclaw_id
    raise RuntimeError("failed to generate unique jiuwenclaw_id after retries")


async def bootstrap_gateway_log_masking(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> None:
    """Gateway 首次上线：MDB builtin 种子 + bulk push 到 GDB（``op=sync``）。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return
    try:
        from manager_server.core.application_config.log_masking_rule import (
            push_log_masking_rules_sync_to_gateway,
            seed_builtin_log_masking_rules,
        )

        if not await is_log_masking_seeded(handler, jid):
            seeded = await seed_builtin_log_masking_rules(handler, jid)
            await merge_instance_data(handler, jid, {_LOG_MASKING_SEEDED_KEY: True})
            if seeded:
                logger.info(
                    "[Instance] seeded %d builtin log_masking_rule row(s) for %s",
                    seeded,
                    jid,
                )
            else:
                logger.info(
                    "[Instance] log_masking builtin seed completed for %s (no new rows)",
                    jid,
                )
        sync_ack = await push_log_masking_rules_sync_to_gateway(handler, jid)
        logger.info(
            "[Instance] log_masking_rule sync on gateway online jiuwenclaw_id=%s "
            "success=%s",
            jid,
            sync_ack.get("success_flag"),
        )
    except Exception:
        logger.warning(
            "[Instance] log_masking_rule bootstrap failed for %s",
            jid,
            exc_info=True,
        )


def _resolve_service_side(service_type: str | None) -> ServiceSide:
    st = str(service_type or "gateway").strip().lower()
    if st in ("", "gateway"):
        return "gateway"
    if st == "runtime":
        return "runtime"
    if st in ("user_web", "web"):
        return "user_web"
    raise ValueError(f"unsupported service_type: {service_type!r}")


async def maybe_full_sync_gateway_on_online(
    handler: DBHandler,
    jiuwenclaw_id: str,
    *,
    previous_gateway_status: str | None,
) -> None:
    """``gateway_status`` 从 pending/offline → online 时全量下发配置。

    失败只记日志，不影响心跳 / 状态更新本身。
    """
    prev = str(previous_gateway_status or "").strip().lower()
    if prev not in _GATEWAY_STATUS_NEEDS_FULL_SYNC:
        return
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return

    from manager_server.manager_config_push.endpoint import require_gateway_endpoint
    from manager_server.core.instance.instance_data_lifecycle import (
        sync_data_to_gateway_on_register,
    )

    try:
        await require_gateway_endpoint(jid)
    except ValueError:
        logger.info(
            "[Instance] skip full sync on online: no gateway_host "
            "jiuwenclaw_id=%s prev_status=%s",
            jid,
            prev,
        )
        return
    try:
        await sync_data_to_gateway_on_register(handler, jid)
        logger.info(
            "[Instance] full sync after gateway online jiuwenclaw_id=%s "
            "prev_status=%s",
            jid,
            prev,
        )
    except Exception:
        logger.warning(
            "[Instance] full sync failed after gateway online jiuwenclaw_id=%s "
            "prev_status=%s",
            jid,
            prev,
            exc_info=True,
        )


async def maybe_full_sync_runtime_on_online(
    handler: DBHandler,
    jiuwenclaw_id: str,
    *,
    previous_runtime_status: str | None,
) -> None:
    """``runtime_status`` 从 pending/offline → online 时全量 ``config_sync``。

    与 Gateway 的 ``maybe_full_sync_gateway_on_online`` 对称：离线期间 Manager
    对服务配置模板的变更不会推到 Runtime，须在 Runtime 重新上线时补齐。

    失败只记日志，不影响探活 / 状态更新本身。
    """
    prev = str(previous_runtime_status or "").strip().lower()
    if prev not in _RUNTIME_STATUS_NEEDS_FULL_SYNC:
        return
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return

    from manager_server.core.instance_resource.runtime_config_sync import (
        sync_runtime_config,
    )

    try:
        result = await sync_runtime_config(handler, jid)
        if isinstance(result, dict) and result.get("skipped"):
            logger.info(
                "[Instance] skip runtime full sync on online: "
                "no runtime endpoint jiuwenclaw_id=%s prev_status=%s",
                jid,
                prev,
            )
            return
        logger.info(
            "[Instance] full sync after runtime online jiuwenclaw_id=%s "
            "prev_status=%s",
            jid,
            prev,
        )
    except Exception:
        logger.warning(
            "[Instance] full sync failed after runtime online jiuwenclaw_id=%s "
            "prev_status=%s",
            jid,
            prev,
            exc_info=True,
        )


async def apply_health_probe_result(
    handler: DBHandler,
    *,
    jiuwenclaw_id: str,
    service_type: str = "gateway",
    alive: bool,
) -> bool:
    """根据 Manager 主动探活结果更新对应侧 status / last_alive。

    - alive：置 online，刷新 last_alive；Gateway / Runtime 从 pending/offline → online
      时各自全量下发（Gateway → WS bootstrap；Runtime → config_sync）
    - 失败：仅当当前为 online 时置 offline；pending 保持不变
    """
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return False
    row = await get_instance_row(handler, jid)
    if row is None:
        return False
    side = _resolve_service_side(service_type)
    now = utc_now()

    status_key = f"{side}_status"
    prev_status = str(getattr(row, status_key, "") or "")

    if alive:
        updates: dict[str, Any] = {
            status_key: "online",
            f"{side}_last_alive": now,
            "updated_at": now,
            "updated_by": "health-probe",
        }
        await handler.update(_INSTANCE_TABLE, {"jiuwenclaw_id": jid}, updates)
        if side == "gateway":
            await maybe_full_sync_gateway_on_online(
                handler,
                jid,
                previous_gateway_status=prev_status,
            )
        elif side == "runtime":
            await maybe_full_sync_runtime_on_online(
                handler,
                jid,
                previous_runtime_status=prev_status,
            )
        return True

    if prev_status == "online":
        await handler.update(
            _INSTANCE_TABLE,
            {"jiuwenclaw_id": jid},
            {
                status_key: "offline",
                "updated_at": now,
                "updated_by": "health-probe",
            },
        )
        return True
    return False


async def mark_instance_offline(
    handler: DBHandler,
    jiuwenclaw_id: str,
    *,
    side: ServiceSide = "gateway",
) -> None:
    """将对应侧标记为 offline（探活失败时也可直接调用）。"""
    await apply_health_probe_result(
        handler,
        jiuwenclaw_id=jiuwenclaw_id,
        service_type=side,
        alive=False,
    )


async def list_instance_rows(
    handler: DBHandler,
    *,
    gateway_status: str | None = None,
    runtime_status: str | None = None,
    user_web_status: str | None = None,
    offset: int,
    limit: int,
    sort_by: str | None = None,
    sort_order: str | None = None,
) -> tuple[Sequence[Any], int]:
    filters: dict[str, Any] = {}
    if gateway_status:
        filters["gateway_status"] = gateway_status
    if runtime_status:
        filters["runtime_status"] = runtime_status
    if user_web_status:
        filters["user_web_status"] = user_web_status
    total = await handler.count_records(_INSTANCE_TABLE, filters)
    db_sort = sort_by if sort_by in _DB_INSTANCE_SORT_FIELDS else None
    order_by = resolve_order_by(
        db_sort,
        sort_order,
        allowed_sort_fields=_DB_INSTANCE_SORT_FIELDS,
        default_order_by=_DEFAULT_INSTANCE_ORDER_BY,
    )
    rows = await handler.list_records(
        _INSTANCE_TABLE,
        filters,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )
    return rows, int(total)


async def delete_instance_row(handler: DBHandler, jiuwenclaw_id: str) -> None:
    await handler.delete(
        INSTANCE_LINK_BINDING_TABLE_DEF.table_name,
        {"jiuwenclaw_id": jiuwenclaw_id},
    )
    await handler.delete(_INSTANCE_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
    from manager_server.security.keys import delete_instance_enc_pubkey

    await delete_instance_enc_pubkey(handler, jiuwenclaw_id)


async def merge_instance_data(
    handler: DBHandler, jiuwenclaw_id: str, patch: dict
) -> Any | None:
    """仅合并写入 ``instance_info.data`` JSON 列。"""
    row = await get_instance_row(handler, jiuwenclaw_id)
    if row is None:
        return None
    merged = _strip_promoted_user_web_keys(dict(getattr(row, "data", None) or {}))
    merged.update(patch)
    merged = _strip_promoted_user_web_keys(merged)
    now = utc_now()
    return await handler.update(
        _INSTANCE_TABLE,
        {"jiuwenclaw_id": jiuwenclaw_id},
        {"data": merged, "updated_at": now, "updated_by": "system"},
    )


class InstanceService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(self, body: CreateInstanceBody) -> dict:
        jiuwenclaw_id = await generate_unique_jiuwenclaw_id(self._handler)
        gateway_host = _norm_host(body.gateway_host)
        runtime_host = _norm_host(body.runtime_host)
        if not gateway_host:
            raise ValueError("gateway_host is required")
        if not runtime_host:
            raise ValueError("runtime_host is required")
        user_web_host = _norm_host(body.user_web_host)
        gateway_web_http_host = _norm_host(body.gateway_web_http_host)
        if not user_web_host:
            raise ValueError("user_web_host is required")
        if not gateway_web_http_host:
            raise ValueError("gateway_web_http_host is required")
        await _assert_config_host_available(
            self._handler,
            gateway_host,
            column="gateway_host",
        )
        await _assert_config_host_available(
            self._handler,
            runtime_host,
            column="runtime_host",
        )
        await _assert_config_host_available(
            self._handler,
            user_web_host,
            column="user_web_host",
        )
        face_hosts = {
            "gateway_web_http_host": gateway_web_http_host,
            "gateway_web_ws_host": body.gateway_web_ws_host,
        }
        await _assert_user_face_hosts_available(self._handler, face_hosts)
        data = _merge_user_face_hosts_into_data(
            dict(body.data) if isinstance(body.data, dict) else {},
            face_hosts,
        )
        await require_config_hosts_reachable(
            gateway_host=gateway_host,
            runtime_host=runtime_host,
            user_web_host=user_web_host,
        )
        namespace = _norm_namespace(body.namespace)
        row_data = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "jiuwenclaw_name": body.jiuwenclaw_name.strip(),
            "description": body.description,
            "namespace": namespace,
            "gateway_host": gateway_host,
            "gateway_status": "pending",
            "runtime_host": runtime_host,
            "runtime_status": "pending",
            "user_web_host": user_web_host,
            "user_web_status": "pending",
            "data": data or None,
            "space_id": (body.space_id or "default").strip() or "default",
            "created_by": (body.created_by or "system").strip() or "system",
            "updated_by": (body.created_by or "system").strip() or "system",
        }
        row = await create_instance_row(self._handler, row_data)

        if gateway_host:
            # 创建时已探活成功：立即置 online（不等待周期扫描）
            await apply_health_probe_result(
                self._handler,
                jiuwenclaw_id=jiuwenclaw_id,
                service_type="gateway",
                alive=True,
            )
            row = await get_instance_row(self._handler, jiuwenclaw_id) or row

        if runtime_host:
            from manager_server.core.instance.runtime_identity import (
                fetch_runtime_identity_from_health,
            )

            try:
                # 仅校验 Runtime 可达
                await fetch_runtime_identity_from_health(runtime_host)
            except ValueError:
                await delete_instance_row(self._handler, jiuwenclaw_id)
                raise
            await apply_health_probe_result(
                self._handler,
                jiuwenclaw_id=jiuwenclaw_id,
                service_type="runtime",
                alive=True,
            )
            row = await get_instance_row(self._handler, jiuwenclaw_id) or row

        if user_web_host:
            await apply_health_probe_result(
                self._handler,
                jiuwenclaw_id=jiuwenclaw_id,
                service_type="user_web",
                alive=True,
            )
            row = await get_instance_row(self._handler, jiuwenclaw_id) or row

        return {
            "jiuwenclaw_id": jiuwenclaw_id,
            "namespace": getattr(row, "namespace", namespace),
            "gateway_host": getattr(row, "gateway_host", gateway_host),
            "gateway_status": getattr(row, "gateway_status", "pending"),
            "runtime_host": getattr(row, "runtime_host", runtime_host),
            "runtime_status": getattr(row, "runtime_status", "pending"),
            "user_web_host": getattr(row, "user_web_host", user_web_host),
            "user_web_status": getattr(row, "user_web_status", "pending"),
        }

    async def list_instances(self, query: InstanceListQuery) -> dict:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        search_query = (query.search or "").strip()
        user_web_filter = str(query.user_web_status or "").strip() or None
        sort_by = str(query.sort_by or "").strip() or None
        needs_memory = bool(search_query)

        filters: dict[str, Any] = {}
        if query.gateway_status:
            filters["gateway_status"] = query.gateway_status
        if query.runtime_status:
            filters["runtime_status"] = query.runtime_status
        if user_web_filter:
            filters["user_web_status"] = user_web_filter

        if needs_memory:
            order_by = resolve_order_by(
                sort_by if sort_by in _DB_INSTANCE_SORT_FIELDS else None,
                query.sort_order,
                allowed_sort_fields=_DB_INSTANCE_SORT_FIELDS,
                default_order_by=_DEFAULT_INSTANCE_ORDER_BY,
            )
            rows = list(
                await self._handler.list_records(
                    _INSTANCE_TABLE,
                    filters,
                    limit=10_000,
                    offset=0,
                    order_by=order_by,
                )
            )
            if search_query:
                rows = [r for r in rows if _matches_instance_search(r, search_query)]
            total = len(rows)
            offset = (page - 1) * page_size
            page_rows = rows[offset: offset + page_size]
            return {
                "items": [_instance_row_to_summary(r) for r in page_rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        offset = (page - 1) * page_size
        rows, total = await list_instance_rows(
            self._handler,
            gateway_status=query.gateway_status,
            runtime_status=query.runtime_status,
            user_web_status=user_web_filter,
            offset=offset,
            limit=page_size,
            sort_by=sort_by,
            sort_order=query.sort_order,
        )
        return {
            "items": [_instance_row_to_summary(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get(self, jiuwenclaw_id: str) -> InstanceDetail | None:
        row = await get_instance_row(self._handler, jiuwenclaw_id)
        if row is None:
            return None
        return _instance_row_to_detail(row)

    async def delete(self, jiuwenclaw_id: str) -> bool:
        from manager_server.core.instance.instance_data_lifecycle import (
            purge_instance_all_data,
        )

        row = await get_instance_row(self._handler, jiuwenclaw_id)
        if row is None:
            return False
        try:
            await purge_instance_all_data(self._handler, jiuwenclaw_id)
        except Exception:
            logger.warning(
                "[Instance] purge instance data failed for %s",
                jiuwenclaw_id,
                exc_info=True,
            )
        await delete_instance_row(self._handler, jiuwenclaw_id)
        return True

    async def update(
        self, jiuwenclaw_id: str, body: InstanceUpdateBody
    ) -> InstanceDetail | None:
        jid = str(jiuwenclaw_id or "").strip()
        if not jid:
            raise ValueError("jiuwenclaw_id is required")
        updates = body.model_dump(exclude_unset=True)
        if not updates:
            return await self.get(jid)
        row = await get_instance_row(self._handler, jid)
        if row is None:
            return None

        strip_fields = (
            "jiuwenclaw_name",
            "description",
            "namespace",
            "space_id",
            "updated_by",
        )
        for field in strip_fields:
            if field in updates and updates[field] is not None:
                updates[field] = str(updates[field]).strip()
        if "namespace" in updates and updates["namespace"] is not None:
            updates["namespace"] = _norm_namespace(updates["namespace"])
        if "gateway_host" in updates:
            updates["gateway_host"] = _norm_host(updates["gateway_host"])
        if "runtime_host" in updates:
            updates["runtime_host"] = _norm_host(updates["runtime_host"])
        if "user_web_host" in updates:
            updates["user_web_host"] = _norm_host(updates["user_web_host"])
            if not updates["user_web_host"]:
                raise ValueError("user_web_host is required")

        face_host_updates = {
            key: updates.pop(key) for key in _USER_FACE_HOST_KEYS if key in updates
        }
        if "gateway_web_http_host" in face_host_updates and not _norm_host(
            face_host_updates.get("gateway_web_http_host")
        ):
            raise ValueError("gateway_web_http_host is required")
        if face_host_updates or "data" in updates:
            base_data = _instance_data_dict(row)
            if "data" in updates:
                incoming = updates["data"]
                base_data = dict(incoming) if isinstance(incoming, dict) else {}
            updates["data"] = _merge_user_face_hosts_into_data(
                base_data, face_host_updates
            ) or None

        if "data" in updates and isinstance(updates["data"], dict):
            old_face = _user_face_hosts_from_data(_instance_data_dict(row))
            new_face = _user_face_hosts_from_data(updates["data"])
            changed_hosts = {
                key: new_face.get(key)
                for key in _USER_FACE_HOST_KEYS
                if new_face.get(key) and new_face.get(key) != old_face.get(key)
            }
            await _assert_user_face_hosts_available(
                self._handler,
                changed_hosts,
                exclude_jiuwenclaw_id=jid,
            )

        if "gateway_host" in updates:
            gateway_host = updates["gateway_host"]
            if gateway_host and _norm_host(
                getattr(row, "gateway_host", None)
            ) != gateway_host:
                await _assert_config_host_available(
                    self._handler,
                    gateway_host,
                    column="gateway_host",
                    exclude_jiuwenclaw_id=jid,
                )
        if "runtime_host" in updates:
            runtime_host = updates["runtime_host"]
            if runtime_host and _norm_host(
                getattr(row, "runtime_host", None)
            ) != runtime_host:
                await _assert_config_host_available(
                    self._handler,
                    runtime_host,
                    column="runtime_host",
                    exclude_jiuwenclaw_id=jid,
                )
        if "user_web_host" in updates:
            user_web_host = updates["user_web_host"]
            if user_web_host and _norm_host(
                getattr(row, "user_web_host", None)
            ) != user_web_host:
                await _assert_config_host_available(
                    self._handler,
                    user_web_host,
                    column="user_web_host",
                    exclude_jiuwenclaw_id=jid,
                )

        await require_config_hosts_reachable(
            gateway_host=(
                updates["gateway_host"]
                if "gateway_host" in updates
                else None
            ),
            runtime_host=(
                updates["runtime_host"]
                if "runtime_host" in updates
                else None
            ),
            user_web_host=(
                updates["user_web_host"] if "user_web_host" in updates else None
            ),
        )

        updates["updated_at"] = utc_now()
        if not updates.get("updated_by"):
            updates["updated_by"] = "api"
        if await self._handler.update(
            _INSTANCE_TABLE, {"jiuwenclaw_id": jid}, updates
        ) is None:
            return None
        return await self.get(jid)
