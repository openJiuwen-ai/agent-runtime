# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""绑/重绑 agent 资源与改 template_ref 时应 ensure extension 等到 Gateway。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_sync_agent_resource_always_ensures_referenced_templates():
    """首次绑定走 delta 后仍会 ensure，避免引用计数已存在时跳过 create。"""
    import manager_server.core.template.push_agent_template_to_gateway as mod

    handler = AsyncMock()
    tpl_row = SimpleNamespace(
        template_id="agent-1",
        template_name="A1",
        description=None,
        agent_tags=None,
        template_ref={"extension_config": ["ext-1"], "mcp": ["mcp-1"]},
        enabled=True,
        data=None,
        created_at=None,
        updated_at=None,
        id=1,
    )
    handler.get = AsyncMock(return_value=tpl_row)

    with (
        patch.object(mod, "_apply_slot_pair_delta", new_callable=AsyncMock) as delta,
        patch.object(
            mod, "ensure_referenced_templates_on_gateway", new_callable=AsyncMock
        ) as ensure,
        patch.object(
            mod, "upsert_agent_template_on_gateway", new_callable=AsyncMock
        ) as upsert_tpl,
        patch.object(
            mod, "_upsert_agent_resource_on_gateway", new_callable=AsyncMock
        ) as upsert_res,
    ):
        await mod.sync_agent_resource_to_gateway(
            handler,
            "jid-1",
            "res-1",
            "agent-1",
            was_first_for_template=True,
            resource_payload={
                "resource_id": "res-1",
                "ref_template_id": "agent-1",
                "resource_name": "r",
                "resource_desc": None,
                "match_expr": [],
                "granted_by": None,
                "enabled": True,
                "expires_at": None,
                "data": None,
            },
        )

    delta.assert_awaited_once()
    ensure.assert_awaited_once()
    assert ensure.await_args.args[1] == "jid-1"
    assert ensure.await_args.args[2]["extension_config"] == ["ext-1"]
    upsert_tpl.assert_awaited_once()
    upsert_res.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_creates_extension_config_templates():
    """ensure 应按 template_ref.extension_config 解析 kind 并 POST create。"""
    import manager_server.core.template.push_template_to_gateway as mod

    handler = AsyncMock()
    create = AsyncMock()
    with (
        patch.object(
            mod,
            "_resolve_template_kind",
            new_callable=AsyncMock,
            side_effect=lambda _h, tid: (
                "extension_config_templates" if tid == "ext-1" else "mcp_templates"
            ),
        ),
        patch.object(
            mod,
            "_build_sync_payloads",
            new_callable=AsyncMock,
            side_effect=lambda _h, kind, ids: [
                {"template_id": sorted(ids)[0], "kind": kind}
            ],
        ),
        patch.object(mod, "_create_template_on_gateway", new=create),
    ):
        await mod.ensure_referenced_templates_on_gateway(
            handler,
            "jid-1",
            {"extension_config": ["ext-1"], "mcp": ["mcp-1"]},
        )

    kinds = {call.args[1] for call in create.await_args_list}
    assert "extension_config_templates" in kinds
    assert "mcp_templates" in kinds
    ext_calls = [
        c for c in create.await_args_list if c.args[1] == "extension_config_templates"
    ]
    assert ext_calls[0].args[0] == "jid-1"
    assert ext_calls[0].args[2]["template_id"] == "ext-1"


@pytest.mark.asyncio
async def test_template_ref_change_pushes_to_bound_gateways():
    """改挂载后应对已绑定实例做 delta + ensure + upsert agent。"""
    import manager_server.core.template.push_agent_template_to_gateway as mod

    handler = AsyncMock()
    handler.list_records = AsyncMock(
        return_value=[
            SimpleNamespace(jiuwenclaw_id="jid-a"),
            SimpleNamespace(jiuwenclaw_id="jid-a"),
            SimpleNamespace(jiuwenclaw_id="jid-b"),
        ]
    )

    with (
        patch.object(
            mod, "sync_gateway_templates_after_template_ref_change", new_callable=AsyncMock
        ) as delta,
        patch.object(
            mod, "ensure_referenced_templates_on_gateway", new_callable=AsyncMock
        ) as ensure,
        patch.object(
            mod, "upsert_agent_template_on_gateway", new_callable=AsyncMock
        ) as upsert,
    ):
        await mod.sync_agent_template_ref_change_to_bound_gateways(
            handler,
            "agent-1",
            old_template_ref={"mcp": ["mcp-1"]},
            new_template_ref={
                "mcp": ["mcp-1"],
                "extension_config": ["ext-1"],
            },
            agent_template_payload={
                "template_id": "agent-1",
                "template_name": "A1",
                "template_ref": {
                    "mcp": ["mcp-1"],
                    "extension_config": ["ext-1"],
                },
                "id": 9,
            },
        )

    assert delta.await_count == 2
    assert ensure.await_count == 2
    assert upsert.await_count == 2
    jids = {c.args[1] for c in upsert.await_args_list}
    assert jids == {"jid-a", "jid-b"}
    for call in ensure.await_args_list:
        assert call.args[2]["extension_config"] == ["ext-1"]
