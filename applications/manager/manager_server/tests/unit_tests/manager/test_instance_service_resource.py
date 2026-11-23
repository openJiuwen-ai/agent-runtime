# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例运行时授权：启用优先级唯一与默认列表排序。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _row(**kwargs):
    defaults = {
        "id": 1,
        "jiuwenclaw_id": "sp-1",
        "resource_id": "res-1",
        "resource_name": "rt",
        "resource_desc": None,
        "ref_template_id": "tpl-1",
        "match_expr": [],
        "priority": 10,
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
async def test_create_rejects_duplicate_enabled_priority():
    from manager_server.core.instance_resource.instance_service_resource_service import (
        InstanceServiceResourceService,
    )
    from manager_server.core.template.service_config_template import (
        ServiceConfigTemplateService,
    )

    handler = MagicMock()
    handler.list_records = AsyncMock(
        return_value=[_row(resource_id="other", priority=10, enabled=True)]
    )
    svc = InstanceServiceResourceService(handler)

    with (
        patch.object(
            ServiceConfigTemplateService,
            "get",
            new_callable=AsyncMock,
            return_value={"template_id": "tpl-1"},
        ),
        pytest.raises(ValueError, match="priority"),
    ):
        await svc.create_resource(
            "sp-1",
            "tpl-1",
            [[]],
            resource_name="dup",
            priority=10,
            enabled=True,
        )


@pytest.mark.asyncio
async def test_assert_allows_same_priority_on_self():
    from manager_server.core.instance_resource.instance_service_resource_service import (
        InstanceServiceResourceService,
    )
    from manager_server.core.template.service_config_template import (
        ServiceConfigTemplateService,
    )

    existing = _row(resource_id="res-1", priority=10, enabled=True)
    handler = MagicMock()
    handler.list_records = AsyncMock(return_value=[existing])
    handler.create = AsyncMock(return_value=existing)
    handler.delete = AsyncMock(return_value=True)
    svc = InstanceServiceResourceService(handler)

    with (
        patch.object(
            ServiceConfigTemplateService,
            "get",
            new_callable=AsyncMock,
            return_value={"template_id": "tpl-1"},
        ),
        patch(
            "manager_server.core.instance_resource.instance_service_resource_service.sync_runtime_config",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource.instance_service_resource_service.auto_bind_from_match_expr",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource.instance_service_resource_service._delete_where",
            new_callable=AsyncMock,
        ),
    ):
        # 更新自身：同 priority 应通过唯一性校验（exclude 自身 resource_id）
        await svc.update_resource(
            "sp-1",
            "res-1",
            [[]],
            ref_template_id="tpl-1",
            resource_name="rt",
            priority=10,
            enabled=True,
        )


@pytest.mark.asyncio
async def test_update_rebinds_template_keeps_resource_id():
    """改绑运行时模板：保留 resource_id，并对新旧模板做 ref 增减。"""
    from manager_server.core.instance_resource.instance_service_resource_service import (
        InstanceServiceResourceService,
    )
    from manager_server.core.template.service_config_template import (
        ServiceConfigTemplateService,
    )

    existing = _row(resource_id="res-1", ref_template_id="tpl-old", priority=10)
    handler = MagicMock()
    # update_resource 查 existing；_write_grants 再查 existing / list instance /
    # assert priority / list_grants — 用 side_effect 覆盖
    handler.list_records = AsyncMock(
        side_effect=[
            [existing],  # update_resource: find by resource_id
            [existing],  # _write_grants: find by resource_id
            [existing],  # _assert_unique_enabled_priority
            [existing],  # _list_instance_rows (current)
            [],  # remaining refs for old template (none → unrecord)
            [],  # list_grants after write
        ]
    )
    handler.create = AsyncMock(return_value=existing)
    handler.delete = AsyncMock(return_value=True)
    svc = InstanceServiceResourceService(handler)

    with (
        patch.object(
            ServiceConfigTemplateService,
            "get",
            new_callable=AsyncMock,
            return_value={"template_id": "tpl-new"},
        ),
        patch(
            "manager_server.core.instance_resource.instance_service_resource_service.sync_runtime_config",
            new_callable=AsyncMock,
        ) as sync_mock,
        patch(
            "manager_server.core.instance_resource.instance_service_resource_service.auto_bind_from_match_expr",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource.instance_service_resource_service._delete_where",
            new_callable=AsyncMock,
        ) as delete_where,
        patch(
            "manager_server.core.instance_resource."
            "instance_service_resource_service.record_service_template_ref_on_runtime",
            new_callable=AsyncMock,
        ) as record_ref,
        patch(
            "manager_server.core.instance_resource."
            "instance_service_resource_service.unrecord_service_template_ref_on_runtime",
            new_callable=AsyncMock,
        ) as unrecord_ref,
    ):
        await svc.update_resource(
            "sp-1",
            "res-1",
            [[]],
            ref_template_id="tpl-new",
            resource_name="rt",
            priority=10,
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
    record_ref.assert_awaited_once_with(handler, "sp-1", "tpl-new")
    unrecord_ref.assert_awaited_once_with(handler, "sp-1", "tpl-old")
    sync_mock.assert_awaited()
    projected = sync_mock.await_args.kwargs["resource_rows"]
    assert any(
        isinstance(r, dict)
        and r.get("resource_id") == "res-1"
        and r.get("ref_template_id") == "tpl-new"
        for r in projected
    )


@pytest.mark.asyncio
async def test_rebind_skips_unrecord_when_sibling_still_uses_old_template():
    """同实例另有 resource 仍绑旧模板时，改绑不得 unrecord 旧模板。"""
    from manager_server.core.instance_resource.instance_service_resource_service import (
        InstanceServiceResourceService,
    )
    from manager_server.core.template.service_config_template import (
        ServiceConfigTemplateService,
    )

    existing = _row(resource_id="res-1", ref_template_id="tpl-old", priority=10)
    sibling = _row(id=2, resource_id="res-2", ref_template_id="tpl-old", priority=20)
    handler = MagicMock()
    handler.list_records = AsyncMock(
        side_effect=[
            [existing],  # update_resource find
            [existing],  # _write_grants find
            [existing, sibling],  # priority assert
            [existing, sibling],  # current instance rows
            [sibling],  # remaining refs for old template after write
            [],  # list_grants
        ]
    )
    handler.create = AsyncMock(return_value=existing)
    handler.delete = AsyncMock(return_value=True)
    svc = InstanceServiceResourceService(handler)

    with (
        patch.object(
            ServiceConfigTemplateService,
            "get",
            new_callable=AsyncMock,
            return_value={"template_id": "tpl-new"},
        ),
        patch(
            "manager_server.core.instance_resource.instance_service_resource_service.sync_runtime_config",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource.instance_service_resource_service.auto_bind_from_match_expr",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource.instance_service_resource_service._delete_where",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource."
            "instance_service_resource_service.record_service_template_ref_on_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance_resource."
            "instance_service_resource_service.unrecord_service_template_ref_on_runtime",
            new_callable=AsyncMock,
        ) as unrecord_ref,
    ):
        await svc.update_resource(
            "sp-1",
            "res-1",
            [[]],
            ref_template_id="tpl-new",
            resource_name="rt",
            priority=10,
            enabled=True,
        )

    unrecord_ref.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_defaults_to_priority_ascending():
    from manager_server.core.instance_resource.instance_service_resource_service import (
        InstanceServiceResourceService,
    )

    handler = MagicMock()
    handler.list_records = AsyncMock(
        return_value=[
            _row(id=1, resource_id="hi", priority=50, resource_name="hi"),
            _row(id=2, resource_id="lo", priority=10, resource_name="lo"),
        ]
    )
    svc = InstanceServiceResourceService(handler)
    result = await svc.list_instance_resources("sp-1")
    assert [item["resource_id"] for item in result["items"]] == ["lo", "hi"]
