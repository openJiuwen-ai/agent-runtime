"""Identity 引用计数（IAAA）：用户 / 组织删除或禁用前，检查 manager 侧是否仍被引用。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance_access.instance_grant_service import (
    SUBJECT_ORG,
    SUBJECT_USER,
)
from manager_server.infrastructure.match_expr import match_expr_mentions_value
from manager_server.models.instance_access_models import INSTANCE_GRANT_TABLE_DEF
from manager_server.models.instance_resource_models import (
    INSTANCE_AGENT_RESOURCE_TABLE_DEF,
    INSTANCE_SERVICE_RESOURCE_TABLE_DEF,
)
from manager_server.models.quota_models import (
    TEAM_QUOTA_POLICY_TABLE_DEF,
    WORKSPACE_QUOTA_POLICY_TABLE_DEF,
)

_CAP = 100_000


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


async def _count_match_expr_refs(
    handler: DBHandler, table: str, *, field: str, literal: str
) -> int:
    count = 0
    offset = 0
    while True:
        rows = await handler.list_records(
            table, {}, limit=_CAP, offset=offset, order_by=[("id", False)]
        )
        if not rows:
            break
        for row in rows:
            if match_expr_mentions_value(
                _g(row, "match_expr"), field=field, literal=literal
            ):
                count += 1
        if len(rows) < _CAP:
            break
        offset += _CAP
    return count


async def _count_identity_references(
    handler: DBHandler,
    *,
    subject_type: str,
    subject_id: str,
    match_field: str,
    id_key: str,
) -> dict[str, Any]:
    sid = str(subject_id or "").strip()
    if not sid:
        raise ValueError(f"{id_key} is required")

    grant_rows = await handler.list_records(
        INSTANCE_GRANT_TABLE_DEF.table_name,
        {"subject_type": subject_type, "subject_id": sid},
        limit=_CAP,
        offset=0,
    )
    instance_grants = len(grant_rows)

    agent_resources = await _count_match_expr_refs(
        handler,
        INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name,
        field=match_field,
        literal=sid,
    )
    service_resources = await _count_match_expr_refs(
        handler,
        INSTANCE_SERVICE_RESOURCE_TABLE_DEF.table_name,
        field=match_field,
        literal=sid,
    )
    workspace_quota_policies = await _count_match_expr_refs(
        handler,
        WORKSPACE_QUOTA_POLICY_TABLE_DEF.table_name,
        field=match_field,
        literal=sid,
    )
    team_quota_policies = await _count_match_expr_refs(
        handler,
        TEAM_QUOTA_POLICY_TABLE_DEF.table_name,
        field=match_field,
        literal=sid,
    )

    total = (
        instance_grants
        + agent_resources
        + service_resources
        + workspace_quota_policies
        + team_quota_policies
    )
    return {
        id_key: sid,
        "total": total,
        "instance_grants": instance_grants,
        "agent_resources": agent_resources,
        "service_resources": service_resources,
        "workspace_quota_policies": workspace_quota_policies,
        "team_quota_policies": team_quota_policies,
    }


async def count_org_references(handler: DBHandler, group_id: str) -> dict[str, Any]:
    """统计组织 identity 在 manager 库中的引用（准入绑定 / 授权范围 / 配额策略）。"""
    return await _count_identity_references(
        handler,
        subject_type=SUBJECT_ORG,
        subject_id=group_id,
        match_field="group_id",
        id_key="group_id",
    )


async def count_user_references(handler: DBHandler, user_id: str) -> dict[str, Any]:
    """统计用户 identity 在 manager 库中的引用（准入绑定 / 授权范围 / 配额策略）。"""
    return await _count_identity_references(
        handler,
        subject_type=SUBJECT_USER,
        subject_id=user_id,
        match_field="user_id",
        id_key="user_id",
    )
