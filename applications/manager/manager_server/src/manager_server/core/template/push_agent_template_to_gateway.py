"""实例 Agent 资源变更时，将 agent_template / 引用模板 / instance_agent_resource 定向下发到 Gateway。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.agent_template import agent_template_out
from manager_server.core.template.push_template_to_gateway import (
    _apply_slot_pair_delta,
    _build_sync_payloads,
    _create_template_on_gateway,
    _resolve_template_kind,
    slot_template_pairs_from_template_ref,
)
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.template_ref import read_template_ref_from_row
from manager_server.infrastructure.utils import iso_datetime
from manager_server.manager_config_push import gateway_request
from manager_server.models.instance_resource_models import INSTANCE_AGENT_RESOURCE_TABLE_DEF
from manager_server.models.template_models import AGENT_TEMPLATE_TABLE_DEF

logger = get_logger(__name__)

_LIST_ALL_CAP = 10_000
_GRANT = INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name
_AGENT_TPL = AGENT_TEMPLATE_TABLE_DEF.table_name

_AGENT_TEMPLATE_PATH = "/api/v1/agent-templates"
_AGENT_RESOURCE_PATH = "/api/v1/instance-agent-resources"
_PUSH_DROP_KEYS = frozenset({"id", "created_at", "updated_at", "jiuwenclaw_id"})


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


def _clean_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in _PUSH_DROP_KEYS}


def _clean_agent_template(row: Any) -> dict[str, Any]:
    return _clean_payload(agent_template_out(row))


async def _count_distinct_resources_for_template(
    handler: DBHandler,
    jiuwenclaw_id: str,
    template_id: str,
) -> int:
    rows = await handler.list_records(
        _GRANT,
        {"jiuwenclaw_id": jiuwenclaw_id, "ref_template_id": template_id},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    return len({str(_g(r, "resource_id") or "").strip() for r in rows if _g(r, "resource_id")})


async def _build_agent_resource_payload(
    handler: DBHandler,
    jiuwenclaw_id: str,
    resource_id: str,
) -> dict[str, Any]:
    rid = str(resource_id or "").strip()
    if not rid:
        raise ValueError("resource_id is required")
    rows = await handler.list_records(
        _GRANT,
        {"jiuwenclaw_id": jiuwenclaw_id, "resource_id": rid},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    if not rows:
        raise ValueError(f"instance agent resource not found: {rid}")
    primary = rows[0]
    exprs: list[Any] = []
    for row in rows:
        expr = _g(row, "match_expr")
        if expr is None or expr == [] or expr == "":
            continue
        if isinstance(expr, list):
            exprs.extend(expr)
        else:
            exprs.append(expr)
    if not exprs:
        match_expr: Any = []
    elif len(exprs) == 1:
        match_expr = exprs[0]
    else:
        match_expr = exprs
    return build_agent_resource_gateway_payload(
        resource_id=rid,
        ref_template_id=str(_g(primary, "ref_template_id") or ""),
        resource_name=_g(primary, "resource_name"),
        resource_desc=_g(primary, "resource_desc"),
        match_expr=match_expr,
        granted_by=_g(primary, "granted_by"),
        enabled=bool(_g(primary, "enabled", True)),
        expires_at=iso_datetime(_g(primary, "expires_at")),
        data=_g(primary, "data"),
    )


def build_agent_resource_gateway_payload(
    *,
    resource_id: str,
    ref_template_id: str,
    resource_name: Any,
    resource_desc: Any,
    match_expr: Any = None,
    granted_by: Any = None,
    enabled: bool = True,
    expires_at: Any = None,
    data: Any = None,
) -> dict[str, Any]:
    """构造下发 Gateway 的 agent resource payload（字段对齐 Manager 行，无 jiuwenclaw_id）。"""
    return {
        "resource_id": str(resource_id or "").strip(),
        "ref_template_id": str(ref_template_id or "").strip(),
        "resource_name": resource_name,
        "resource_desc": resource_desc,
        "match_expr": [] if match_expr is None else match_expr,
        "granted_by": granted_by,
        "enabled": bool(enabled),
        "expires_at": expires_at,
        "data": data,
    }


async def _upsert_agent_template_on_gateway(
    jiuwenclaw_id: str,
    template: dict[str, Any],
) -> None:
    await gateway_request(
        jiuwenclaw_id,
        "POST",
        _AGENT_TEMPLATE_PATH,
        template,
    )


async def _delete_agent_template_on_gateway(
    jiuwenclaw_id: str,
    template_id: str,
) -> None:
    tid = str(template_id or "").strip()
    if not tid:
        return
    await gateway_request(
        jiuwenclaw_id,
        "DELETE",
        f"{_AGENT_TEMPLATE_PATH}/{tid}",
        {},
    )


async def _upsert_agent_resource_on_gateway(
    jiuwenclaw_id: str,
    payload: dict[str, Any],
) -> None:
    await gateway_request(
        jiuwenclaw_id,
        "POST",
        _AGENT_RESOURCE_PATH,
        payload,
    )


async def _delete_agent_resource_on_gateway(
    jiuwenclaw_id: str,
    resource_id: str,
) -> None:
    rid = str(resource_id or "").strip()
    if not rid:
        return
    await gateway_request(
        jiuwenclaw_id,
        "DELETE",
        f"{_AGENT_RESOURCE_PATH}/{rid}",
        {},
    )


async def _ensure_referenced_templates_on_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
    template_ref: Any,
) -> None:
    pairs = slot_template_pairs_from_template_ref(template_ref)
    if not pairs:
        return
    by_kind: dict[str, set[str]] = {}
    for _slot, tid in pairs:
        kind = await _resolve_template_kind(handler, tid)
        if kind is None:
            continue
        by_kind.setdefault(kind, set()).add(tid)
    for kind, template_ids in sorted(by_kind.items()):
        payloads = await _build_sync_payloads(handler, kind, template_ids)
        for idx, tmpl in enumerate(payloads):
            await _create_template_on_gateway(
                jiuwenclaw_id,
                kind,
                tmpl,
            )


async def sync_agent_resource_to_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
    resource_id: str,
    template_id: str,
    *,
    was_first_for_template: bool | None = None,
    resource_payload: dict[str, Any] | None = None,
) -> None:
    """向 Gateway 推送 agent 模板、引用模板与授权行。

    调用方应在 Manager 落库前调用；``resource_payload`` 可直接传入待写内容，
    避免依赖尚未写入的 Manager 行。
    """
    jid = str(jiuwenclaw_id or "").strip()
    tid = str(template_id or "").strip()
    rid = str(resource_id or "").strip()
    if not jid or not tid or not rid:
        raise ValueError("jiuwenclaw_id, template_id and resource_id are required")

    tpl_row = await handler.get(_AGENT_TPL, {"template_id": tid})
    if tpl_row is None:
        raise ValueError(f"agent_template not found: {tid}")

    if was_first_for_template is None:
        was_first_for_template = (
            await _count_distinct_resources_for_template(handler, jid, tid) == 0
            if resource_payload is not None
            else await _count_distinct_resources_for_template(handler, jid, tid) == 1
        )

    template_ref = read_template_ref_from_row(tpl_row)
    if was_first_for_template:
        await _apply_slot_pair_delta(
            handler,
            jid,
            added=slot_template_pairs_from_template_ref(template_ref),
            removed=set(),
        )
    else:
        await _ensure_referenced_templates_on_gateway(handler, jid, template_ref)

    await _upsert_agent_template_on_gateway(
        jid,
        _clean_agent_template(tpl_row),
    )
    payload = resource_payload or await _build_agent_resource_payload(handler, jid, rid)
    await _upsert_agent_resource_on_gateway(
        jid,
        payload,
    )
    logger.info(
        "[push_agent_template] synced resource jiuwenclaw_id=%s resource_id=%s template_id=%s",
        jid,
        rid,
        tid,
    )


async def delete_agent_resource_from_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
    resource_id: str,
    *,
    ref_template_id: str,
    remaining_resources_after: int | None = None,
) -> None:
    """从 Gateway 移除授权；必要时清理 agent 模板与引用模板。

    调用方应在 Manager 删库前调用。若尚未删库，须传入删除后仍剩余的
    ``remaining_resources_after``（不含当前 resource）。
    """
    jid = str(jiuwenclaw_id or "").strip()
    rid = str(resource_id or "").strip()
    tid = str(ref_template_id or "").strip()
    if not jid or not rid:
        return

    await _delete_agent_resource_on_gateway(
        jid,
        rid,
    )

    if not tid:
        return

    if remaining_resources_after is None:
        remaining = await _count_distinct_resources_for_template(handler, jid, tid)
    else:
        remaining = max(0, int(remaining_resources_after))
    if remaining > 0:
        logger.info(
            "[push_agent_template] resource removed jiuwenclaw_id=%s resource_id=%s "
            "template_id=%s still referenced by %d resource(s)",
            jid,
            rid,
            tid,
            remaining,
        )
        return

    tpl_row = await handler.get(_AGENT_TPL, {"template_id": tid})
    if tpl_row is not None:
        await _apply_slot_pair_delta(
            handler,
            jid,
            added=set(),
            removed=slot_template_pairs_from_template_ref(
                read_template_ref_from_row(tpl_row)
            ),
        )
    await _delete_agent_template_on_gateway(
        jid,
        tid,
    )
    logger.info(
        "[push_agent_template] removed resource and agent_template "
        "jiuwenclaw_id=%s resource_id=%s template_id=%s",
        jid,
        rid,
        tid,
    )


async def push_agent_resources_sync_to_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """Gateway 注册后：bulk 同步该实例全部 Agent 资源及相关模板。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")

    rows = await handler.list_records(
        _GRANT,
        {"jiuwenclaw_id": jid},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    resource_ids: list[str] = []
    seen_resources: set[str] = set()
    template_ids: set[str] = set()
    for row in rows:
        rid = str(_g(row, "resource_id") or "").strip()
        tid = str(_g(row, "ref_template_id") or "").strip()
        if tid:
            template_ids.add(tid)
        if rid and rid not in seen_resources:
            seen_resources.add(rid)
            resource_ids.append(rid)

    synced_templates = 0
    for tid in sorted(template_ids):
        tpl_row = await handler.get(_AGENT_TPL, {"template_id": tid})
        if tpl_row is None:
            continue
        await _ensure_referenced_templates_on_gateway(
            handler,
            jid,
            read_template_ref_from_row(tpl_row),
        )
        await _upsert_agent_template_on_gateway(
            jid,
            _clean_agent_template(tpl_row),
        )
        synced_templates += 1

    synced_resources = 0
    for idx, rid in enumerate(resource_ids):
        payload = await _build_agent_resource_payload(handler, jid, rid)
        await _upsert_agent_resource_on_gateway(
            jid,
            payload,
        )
        synced_resources += 1

    return {
        "success_flag": True,
        "result": {
            "agent_templates": synced_templates,
            "agent_resources": synced_resources,
        },
        "transport": "http",
    }


__all__ = (
    "build_agent_resource_gateway_payload",
    "delete_agent_resource_from_gateway",
    "push_agent_resources_sync_to_gateway",
    "sync_agent_resource_to_gateway",
)
