"""按实例服务资源引用关系，将服务配置模板同步到 Agent Runtime。

与 ``push_template_to_gateway`` 对称：引用索引复用 ``jid_template_ref``，
用 ``slot=service_config`` 与 Gateway 普通模板槽位区分。

Runtime 侧目前只有全量 ``config_sync``（``{containers, templates, scopes}``），
因此模板更新 = 对引用了该模板的每个实例重推全量投影。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance_resource.runtime_config_sync import sync_runtime_config
from manager_server.core.template.push_template_to_gateway import (
    _apply_slot_pair_delta,
    _list_active_jid_template_ref_rows,
)
from manager_server.infrastructure.logger import get_logger
from manager_server.models.instance_resource_models import (
    INSTANCE_SERVICE_RESOURCE_TABLE_DEF,
)
from manager_server.models.jid_template_ref_models import JID_TEMPLATE_REF_TABLE_DEF
from manager_server.models.template_models import SERVICE_CONFIG_TEMPLATE_TABLE_DEF
from manager_server.schemas.template_slot_schemas import SERVICE_CONFIG_SLOT

logger = get_logger(__name__)

_LIST_ALL_CAP = 10_000
_GRANT = INSTANCE_SERVICE_RESOURCE_TABLE_DEF.table_name
_SVC_TPL = SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name
_JID_TEMPLATE_REF = JID_TEMPLATE_REF_TABLE_DEF.table_name

SERVICE_CONFIG_TEMPLATES_KIND = "service_config_templates"


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


def service_config_slot_pair(template_id: str) -> tuple[str, str]:
    return (SERVICE_CONFIG_SLOT, str(template_id or "").strip())


def _collect_nonempty_jiuwenclaw_ids(rows: list[Any]) -> set[str]:
    out: set[str] = set()
    for row in rows:
        jid = str(_g(row, "jiuwenclaw_id") or "").strip()
        if jid:
            out.add(jid)
    return out


async def collect_jiuwenclaw_ids_for_service_template(
    handler: DBHandler,
    template_id: str,
) -> set[str]:
    """查找引用了指定服务配置模板的全部 ``jiuwenclaw_id``（优先索引，回退扫表）。"""
    tid = str(template_id or "").strip()
    if not tid:
        return set()

    indexed = (
        await handler.count_records(
            _JID_TEMPLATE_REF, {"template_id": tid, "slot": SERVICE_CONFIG_SLOT}
        )
    ) > 0
    if indexed:
        rows = await _list_active_jid_template_ref_rows(
            handler, tid, slot_keys=frozenset({SERVICE_CONFIG_SLOT})
        )
        return _collect_nonempty_jiuwenclaw_ids(rows)

    rows = await handler.list_records(
        _GRANT,
        {"ref_template_id": tid},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    return _collect_nonempty_jiuwenclaw_ids(rows)


async def record_service_template_ref_on_runtime(
    handler: DBHandler,
    jiuwenclaw_id: str,
    template_id: str,
) -> None:
    """实例新增一条服务资源对模板的引用时，``jid_template_ref`` ref_count +1。

    不向 Gateway 推送：``_apply_slot_pair_delta`` 对无法解析 kind 的 id 只调索引。
    """
    jid = str(jiuwenclaw_id or "").strip()
    tid = str(template_id or "").strip()
    if not jid or not tid:
        return
    await _apply_slot_pair_delta(
        handler,
        jid,
        added={service_config_slot_pair(tid)},
        removed=set(),
    )


async def unrecord_service_template_ref_on_runtime(
    handler: DBHandler,
    jiuwenclaw_id: str,
    template_id: str,
) -> None:
    """实例移除一条服务资源对模板的引用时，``jid_template_ref`` ref_count -1。"""
    jid = str(jiuwenclaw_id or "").strip()
    tid = str(template_id or "").strip()
    if not jid or not tid:
        return
    await _apply_slot_pair_delta(
        handler,
        jid,
        added=set(),
        removed={service_config_slot_pair(tid)},
    )


async def update_service_template_on_referencing_runtimes(
    handler: DBHandler,
    template_id: str,
) -> None:
    """服务配置模板变更后，对引用了它的在线实例重推 Runtime 全量投影。

    Runtime 无单模板 PATCH，只能 ``config_sync``；须在 Manager 落库**之后**调用，
    以便 ``build_runtime_config`` 读到最新模板行。

    实际 HTTP 仍走全局 ``AGENT_RUNTIME_ENDPOINT``（与现有授权 sync 一致）；
    这里用实例 online 集合做过滤，不依赖 ``gateway_config_host``。
    """
    tid = str(template_id or "").strip()
    if not tid:
        return
    if await handler.get(_SVC_TPL, {"template_id": tid}) is None:
        return

    from manager_server.core.instance.instance_service import list_instance_rows

    all_refs = await collect_jiuwenclaw_ids_for_service_template(handler, tid)
    online_rows, _ = await list_instance_rows(
        handler, gateway_status="online", offset=0, limit=_LIST_ALL_CAP
    )
    online = _collect_nonempty_jiuwenclaw_ids(online_rows)
    for jid in sorted(all_refs):
        if jid not in online:
            logger.info(
                "[push_template_runtime] skip offline instance jiuwenclaw_id=%s "
                "action=update template_id=%s",
                jid,
                tid,
            )
            continue
        try:
            await sync_runtime_config(handler, jid)
        except Exception:
            logger.exception(
                "[push_template_runtime] sync failed jiuwenclaw_id=%s template_id=%s",
                jid,
                tid,
            )
            raise
        logger.info(
            "[push_template_runtime] synced jiuwenclaw_id=%s template_id=%s",
            jid,
            tid,
        )


__all__ = (
    "SERVICE_CONFIG_SLOT",
    "SERVICE_CONFIG_TEMPLATES_KIND",
    "collect_jiuwenclaw_ids_for_service_template",
    "record_service_template_ref_on_runtime",
    "service_config_slot_pair",
    "unrecord_service_template_ref_on_runtime",
    "update_service_template_on_referencing_runtimes",
)
