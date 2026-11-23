# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例 Agent 授权：改绑模板并保留 resource_id。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _row(**kwargs):
    defaults = {
        "id": 1,
        "jiuwenclaw_id": "sp-1",
        "resource_id": "res-1",
        "resource_name": "agent",
        "resource_desc": None,
        "ref_template_id": "tpl-old",
        "match_expr": [],
        "granted_by": None,
        "expires_at": None,
        "enabled": True,
        "data": None,
        "created_at": None,
        "updated_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_update_rebinds_agent_template_keeps_resource_id():
    from manager_server.core.instance_resource.instance_agent_resource_service import (
        InstanceAgentResourceService,
    )
    from manager_server.core.template.agent_template import AgentTemplateService

    existing = _row()
    handler = MagicMock()
    handler.list_records = AsyncMock(
        side_effect=[
            [existing],  # update_resource find
            [],  # before_template_refs for tpl-new → was_first
            [existing],  # _write_grants find by resource_id
            [],  # list_grants
        ]
    )
    handler.create = AsyncMock(return_value=existing)
    handler.delete = AsyncMock(return_value=True)
    svc = InstanceAgentResourceService(handler)

    with (
        patch.object(
            AgentTemplateService,
            "exists",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "manager_server.core.instance_resource.instance_agent_resource_service.sync_agent_resource_to_gateway",
            new_callable=AsyncMock,
        ) as sync_gw,
        patch(
            "manager_server.core.instance_resource.instance_agent_resource_service.auto_bind_from_match_expr",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource.instance_agent_resource_service._delete_where",
            new_callable=AsyncMock,
        ) as delete_where,
        patch(
            "manager_server.core.instance_resource."
            "instance_agent_resource_service.unload_agent_template_if_unreferenced",
            new_callable=AsyncMock,
        ) as unload_old,
    ):
        await svc.update_resource(
            "sp-1",
            "res-1",
            [[]],
            ref_template_id="tpl-new",
            resource_name="agent",
            enabled=True,
        )

    delete_where.assert_awaited()
    assert delete_where.await_args.args[2] == {
        "jiuwenclaw_id": "sp-1",
        "resource_id": "res-1",
    }
    created = handler.create.await_args.args[1]
    assert created["resource_id"] == "res-1"
    assert created["ref_template_id"] == "tpl-new"
    sync_gw.assert_awaited()
    assert sync_gw.await_args.args[3] == "tpl-new"
    assert sync_gw.await_args.kwargs["was_first_for_template"] is True
    unload_old.assert_awaited_once_with(handler, "sp-1", "tpl-old")


@pytest.mark.asyncio
async def test_rebind_succeeds_when_unload_old_template_raises():
    """Manager 已落库后，旧模板卸载失败不得让改绑接口失败。"""
    from manager_server.core.instance_resource.instance_agent_resource_service import (
        InstanceAgentResourceService,
    )
    from manager_server.core.template.agent_template import AgentTemplateService

    existing = _row()
    handler = MagicMock()
    handler.list_records = AsyncMock(
        side_effect=[
            [existing],
            [],
            [existing],
            [],
        ]
    )
    handler.create = AsyncMock(return_value=existing)
    handler.delete = AsyncMock(return_value=True)
    svc = InstanceAgentResourceService(handler)

    with (
        patch.object(
            AgentTemplateService,
            "exists",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "manager_server.core.instance_resource.instance_agent_resource_service.sync_agent_resource_to_gateway",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource.instance_agent_resource_service.auto_bind_from_match_expr",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource.instance_agent_resource_service._delete_where",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource."
            "instance_agent_resource_service.unload_agent_template_if_unreferenced",
            new_callable=AsyncMock,
            side_effect=ValueError(
                "gateway HTTP push rejected status=500 detail='Internal Server Error'"
            ),
        ),
    ):
        result = await svc.update_resource(
            "sp-1",
            "res-1",
            [[]],
            ref_template_id="tpl-new",
            resource_name="agent",
            enabled=True,
        )

    assert result == []
    created = handler.create.await_args.args[1]
    assert created["ref_template_id"] == "tpl-new"
