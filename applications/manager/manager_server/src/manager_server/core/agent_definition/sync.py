"""Agent 模板（agent_definition）派生同步。

内容 = 有效 Agent 配置模板 × 有效运行时模板。同步是派生数据维护：
失败只记 ``warning``，不阻塞模板增删改；下次变更或启动对齐自愈。
进程内 ``asyncio.Lock`` 串行，跨副本靠 ``agent_id`` / 组合唯一键兜底。
"""

from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.user_console.agent_identity import try_build_agent_identity
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.utils import utc_now
from manager_server.models.agent_definition_models import AGENT_DEFINITION_TABLE_DEF
from manager_server.models.template_models import (
    AGENT_TEMPLATE_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
)

_log = get_logger(__name__)
_TABLE = AGENT_DEFINITION_TABLE_DEF.table_name
_CFG = AGENT_TEMPLATE_TABLE_DEF.table_name
_RT = SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name
_MAX_ROWS = 100_000
_DISABLED_REASON = "upstream_unavailable"

_LOCK = asyncio.Lock()


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


def _pair(row: Any) -> tuple[str, str]:
    return (
        str(_g(row, "agent_config_template_id") or ""),
        str(_g(row, "runtime_template_id") or ""),
    )


def _build_valid(
    cfg_rows: list[Any], rt_rows: list[Any]
) -> dict[tuple[str, str], dict[str, str]]:
    valid: dict[tuple[str, str], dict[str, str]] = {}
    for cfg in cfg_rows:
        cfg_id = str(_g(cfg, "template_id") or "").strip()
        if not cfg_id:
            continue
        cfg_name = str(_g(cfg, "template_name") or "").strip() or None
        for rt in rt_rows:
            rt_id = str(_g(rt, "template_id") or "").strip()
            if not rt_id:
                continue
            identity = try_build_agent_identity(rt_id, cfg_id)
            if identity is None:
                continue
            agent_id, default_name = identity
            valid[(cfg_id, rt_id)] = {
                "agent_id": agent_id,
                "default_name": default_name,
                "agent_config_template_name": cfg_name,
                "runtime_template_name": str(_g(rt, "template_name") or "").strip()
                or None,
            }
    return valid


async def reconcile_agent_definitions(handler: DBHandler) -> dict[str, int]:
    """全量对齐：补行、恢复、失效、刷新快照。幂等，失败不抛出。"""
    counts = {
        "created": 0,
        "restored": 0,
        "renamed": 0,
        "updated": 0,
        "disabled": 0,
        "conflicted": 0,
        "failed": 0,
    }
    async with _LOCK:
        try:
            await _reconcile(handler, counts)
        except Exception as exc:  # 派生数据：失败只告警
            _log.warning("agent_definition.reconcile_failed", error=str(exc))
            counts["failed"] += 1
    return counts


async def _reconcile(handler: DBHandler, counts: dict[str, int]) -> None:
    cfg_rows = await handler.list_records(
        _CFG, {"enabled": True}, limit=_MAX_ROWS, offset=0
    )
    rt_rows = await handler.list_records(
        _RT, {"enabled": True}, limit=_MAX_ROWS, offset=0
    )
    valid = _build_valid(cfg_rows, rt_rows)
    existing_rows = await handler.list_records(_TABLE, {}, limit=_MAX_ROWS, offset=0)
    existing = {_pair(row): row for row in existing_rows}
    active_names = {
        str(_g(row, "agent_name") or "").casefold()
        for row in existing_rows
        if not bool(_g(row, "deleted", False))
    }
    now = utc_now()

    for key, spec in valid.items():
        row = existing.get(key)
        cfg_id, rt_id = key
        if row is None:
            agent_id = spec["agent_id"]
            try:
                await handler.create(
                    _TABLE,
                    {
                        "agent_id": agent_id,
                        "agent_name": spec["default_name"],
                        "agent_config_template_id": cfg_id,
                        "runtime_template_id": rt_id,
                        "agent_config_template_name": spec[
                            "agent_config_template_name"
                        ],
                        "runtime_template_name": spec["runtime_template_name"],
                        "deleted": False,
                        "deleted_reason": None,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                active_names.add(spec["default_name"].casefold())
                counts["created"] += 1
            except Exception as exc:
                _log.warning(
                    "agent_definition.create_failed",
                    agent_id=agent_id,
                    error=str(exc),
                )
                counts["failed"] += 1
            continue

        agent_id = str(_g(row, "agent_id") or "")
        name = str(_g(row, "agent_name") or "").strip() or spec["default_name"]
        updates: dict[str, Any] = {}
        if bool(_g(row, "deleted", False)):
            if name.casefold() in active_names:
                # 自定义名已被有效行占用：不抢别人的名字，回退默认名后恢复。
                fallback = spec["default_name"]
                if fallback.casefold() in active_names:
                    _log.warning(
                        "agent_definition.restore_conflict",
                        agent_id=agent_id,
                        agent_name=name,
                    )
                    counts["conflicted"] += 1
                    continue
                _log.warning(
                    "agent_definition.restore_name_reset",
                    agent_id=agent_id,
                    agent_name=name,
                    default_name=fallback,
                )
                updates["agent_name"] = fallback
                name = fallback
                counts["renamed"] += 1
            updates["deleted"] = False
            updates["deleted_reason"] = None
            active_names.add(name.casefold())
            counts["restored"] += 1
        else:
            if (
                _g(row, "agent_config_template_name")
                == spec["agent_config_template_name"]
                and _g(row, "runtime_template_name")
                == spec["runtime_template_name"]
            ):
                continue
            counts["updated"] += 1
        updates["agent_config_template_name"] = spec["agent_config_template_name"]
        updates["runtime_template_name"] = spec["runtime_template_name"]
        updates["updated_at"] = now
        await handler.update(_TABLE, {"agent_id": agent_id}, updates)

    for key, row in existing.items():
        if key in valid or bool(_g(row, "deleted", False)):
            continue
        await handler.update(
            _TABLE,
            {"agent_id": str(_g(row, "agent_id") or "")},
            {
                "deleted": True,
                "deleted_reason": _DISABLED_REASON,
                "updated_at": now,
            },
        )
        counts["disabled"] += 1

    _log.info("agent_definition.reconcile", **counts)
