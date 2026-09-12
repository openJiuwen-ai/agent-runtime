"""实例生命周期：注册 bootstrap、删除时清理 Manager MDB / Gateway GDB / Runtime。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.application_config.log_masking_rule import (
    push_log_masking_rules_sync_to_gateway,
    seed_builtin_log_masking_rules,
)
from manager_server.core.application_config.logging_config import (
    push_logging_config_sync_to_gateway,
)
from manager_server.core.application_config.memory_config import (
    push_memory_config_sync_to_gateway,
)
from manager_server.core.application_config.task_memory_config import (
    push_task_memory_config_sync_to_gateway,
)
from manager_server.core.instance.instance_service import (
    _LOG_MASKING_SEEDED_KEY,
    get_instance_row,
    is_log_masking_seeded,
    merge_instance_data,
)
from manager_server.manager_config_push import gateway_request, resolve_gateway_endpoint
from manager_server.core.template.push_agent_template_to_gateway import (
    push_agent_resources_sync_to_gateway,
)
from manager_server.core.template.push_template_to_gateway import (
    rebuild_jid_template_ref_for_gateway,
    sync_referenced_templates_to_gateway,
)
from manager_server.models.instance_access_models import INSTANCE_GRANT_TABLE_DEF
from manager_server.models.instance_resource_models import (
    INSTANCE_AGENT_RESOURCE_TABLE_DEF,
    INSTANCE_SERVICE_RESOURCE_TABLE_DEF,
)
from manager_server.models.jid_template_ref_models import (
    JID_TEMPLATE_REF_TABLE_DEF,
)
from manager_server.models.application_config_models import (
    LOG_MASKING_RULE_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
    _MEMORY_CONFIG_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
)

logger = logging.getLogger(__name__)

_LIST_ALL_CAP = 10_000

# 按 jiuwenclaw_id 归属的实例级数据（不含 instance_info 本身）。
_MANAGER_INSTANCE_TABLES = (
    LOG_MASKING_RULE_TABLE_DEF.table_name,
    LOGGING_CONFIG_TABLE_DEF.table_name,
    _TASK_MEMORY_CONFIG_TABLE_DEF.table_name,
    _MEMORY_CONFIG_TABLE_DEF.table_name,
    INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name,
    INSTANCE_SERVICE_RESOURCE_TABLE_DEF.table_name,
    INSTANCE_GRANT_TABLE_DEF.table_name,
)

_JID_TEMPLATE_REF_TABLE = JID_TEMPLATE_REF_TABLE_DEF.table_name


async def _seed_log_masking_if_needed(handler: DBHandler, jiuwenclaw_id: str) -> None:
    if await is_log_masking_seeded(handler, jiuwenclaw_id):
        return
    seeded = await seed_builtin_log_masking_rules(handler, jiuwenclaw_id)
    await merge_instance_data(handler, jiuwenclaw_id, {_LOG_MASKING_SEEDED_KEY: True})
    if seeded:
        logger.info(
            "[GatewayBootstrap] seeded %d builtin log_masking_rule row(s) for %s",
            seeded,
            jiuwenclaw_id,
        )


async def sync_data_to_gateway_on_register(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """向 Gateway 全量同步 Manager 权威配置。

    典型触发：``gateway_status`` 从 pending/offline → online
    （见 ``maybe_full_sync_gateway_on_online``）。

    顺序说明：
    1. 模板（含 permissions_template；Agent 资源依赖）
    2. Agent 资源（template_ref.permissions 绑定安全护栏）
    3. 应用配置（logging / task-memory / memory / 日志脱敏）
    4. 重建 Manager 侧 jid_template_ref 索引

    注：实例级 ``permissions_config`` 已废弃，企业权限走 ``permissions_template``，
    由步骤 1/2 随模板与 Agent 资源下发，不再单独 push。
    """
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return {}

    results: dict[str, Any] = {}
    try:
        results["templates"] = await sync_referenced_templates_to_gateway(handler, jid)
    except Exception:
        logger.warning(
            "[GatewayBootstrap] template sync failed jiuwenclaw_id=%s",
            jid,
            exc_info=True,
        )
        raise

    try:
        results["agent_resources"] = await push_agent_resources_sync_to_gateway(handler, jid)
    except Exception:
        logger.warning(
            "[GatewayBootstrap] agent resource sync failed jiuwenclaw_id=%s",
            jid,
            exc_info=True,
        )
        raise

    for name, push_fn, before_fn in (
        ("logging", push_logging_config_sync_to_gateway, None),
        ("task_memory", push_task_memory_config_sync_to_gateway, None),
        ("memory", push_memory_config_sync_to_gateway, None),
        (
            "log_masking_rule",
            push_log_masking_rules_sync_to_gateway,
            _seed_log_masking_if_needed,
        ),
    ):
        try:
            if before_fn is not None:
                await before_fn(handler, jid)
            results[name] = await push_fn(handler, jid)
        except Exception:
            logger.warning(
                "[GatewayBootstrap] %s sync failed jiuwenclaw_id=%s",
                name,
                jid,
                exc_info=True,
            )
            raise

    try:
        await rebuild_jid_template_ref_for_gateway(handler, jid)
        results["jid_template_ref_rebuilt"] = True
    except Exception:
        logger.warning(
            "[GatewayBootstrap] jid_template_ref rebuild failed jiuwenclaw_id=%s",
            jid,
            exc_info=True,
        )
        raise

    logger.info("[GatewayBootstrap] completed jiuwenclaw_id=%s sections=%s", jid, list(results))
    return results


def _delete_pk_for_row(table: str, row: Any, jiuwenclaw_id: str) -> dict[str, Any]:
    if table == _JID_TEMPLATE_REF_TABLE:
        return {
            "jiuwenclaw_id": jiuwenclaw_id,
            "slot": getattr(row, "slot"),
            "template_id": getattr(row, "template_id"),
        }
    return {"id": getattr(row, "id")}


async def _purge_table_rows(
    handler: DBHandler,
    table: str,
    jiuwenclaw_id: str,
) -> int:
    rows = await handler.list_records(
        table,
        {"jiuwenclaw_id": jiuwenclaw_id},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    deleted = 0
    for row in rows:
        pk = _delete_pk_for_row(table, row, jiuwenclaw_id)
        if await handler.delete(table, pk):
            deleted += 1
    return deleted


async def purge_manager_instance_data(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, int]:
    """删除 Manager MDB 中指定实例的全部配置数据（不含 ``instance_info``）。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return {}

    deleted_counts: dict[str, int] = {}
    for table in _MANAGER_INSTANCE_TABLES:
        count = await _purge_table_rows(handler, table, jid)
        if count:
            deleted_counts[table] = count

    count = await _purge_table_rows(handler, _JID_TEMPLATE_REF_TABLE, jid)
    if count:
        deleted_counts[_JID_TEMPLATE_REF_TABLE] = count

    logger.info(
        "[InstanceDataLifecycle] purged manager instance data jiuwenclaw_id=%s counts=%s",
        jid,
        deleted_counts,
    )
    return deleted_counts


async def _purge_gateway_via_http(jiuwenclaw_id: str) -> bool:
    """有 gateway_endpoint 则 HTTP purge；否则返回 False（不抛错）。"""
    from manager_server.infrastructure.db import get_db_handler

    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return False
    try:
        row = await get_instance_row(get_db_handler(), jid)
        if row is None or not resolve_gateway_endpoint(row):
            return False
        await gateway_request(
            jid,
            "POST",
            "/api/v1/instance-data-lifecycle",
            {"op": "purge"},
        )
        return True
    except ValueError:
        logger.warning(
            "[InstanceDataLifecycle] gateway purge failed jiuwenclaw_id=%s",
            jid,
            exc_info=True,
        )
        return False


async def purge_gateway_instance_data(jiuwenclaw_id: str) -> dict[str, Any]:
    """通过 HTTP 通知 Gateway 清理 GDB；无 endpoint 则跳过。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return {"purged": False}

    if await _purge_gateway_via_http(jid):
        return {"purged": True}

    logger.info(
        "[InstanceDataLifecycle] gateway purge skipped jiuwenclaw_id=%s (no endpoint)",
        jid,
    )
    return {"purged": False}


async def purge_runtime_instance_data(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """向 Runtime 推空 ``config_sync``，清掉该实例投影的 template/scope。

    Runtime 只有全量快照替换，无按实例 purge API；空投影
    ``{containers:[], templates:[], scopes:[]}`` 即删除该实例此前下发的配置。
    ``AGENT_RUNTIME_ENDPOINT`` 未配置或推送失败时跳过（不抛错）。
    """
    from manager_server.core.instance_resource.runtime_config_sync import (
        sync_runtime_config,
    )

    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return {"purged": False}
    from manager_server.core.instance_resource.runtime_config_sync import resolve_runtime_endpoint
    if not await resolve_runtime_endpoint(handler, jid):
        logger.info(
            "[InstanceDataLifecycle] runtime purge skipped jiuwenclaw_id=%s "
            "(AGENT_RUNTIME_ENDPOINT empty)",
            jid,
        )
        return {"purged": False, "skipped": True}
    try:
        # 显式空投影：不依赖 Manager 侧资源是否已删。
        await sync_runtime_config(handler, jid, resource_rows=[])
        logger.info(
            "[InstanceDataLifecycle] runtime config cleared jiuwenclaw_id=%s",
            jid,
        )
        return {"purged": True}
    except Exception:
        logger.warning(
            "[InstanceDataLifecycle] runtime purge failed jiuwenclaw_id=%s",
            jid,
            exc_info=True,
        )
        return {"purged": False}


async def purge_instance_all_data(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """删除实例关联数据：先清 Runtime / Gateway，再清 Manager MDB。

    顺序：Remote 先于 local，避免 Manager 已删行后远程仍残留旧投影。
    """
    jid = str(jiuwenclaw_id or "").strip()
    runtime_result = await purge_runtime_instance_data(handler, jid)
    gateway_result = await purge_gateway_instance_data(jid)
    manager_counts = await purge_manager_instance_data(handler, jid)
    return {
        "runtime": runtime_result,
        "gateway": gateway_result,
        "manager": manager_counts,
    }


__all__ = (
    "sync_data_to_gateway_on_register",
    "purge_instance_all_data",
    "purge_manager_instance_data",
    "purge_gateway_instance_data",
    "purge_runtime_instance_data",
)
