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
            resource_name="rt",
            priority=10,
            enabled=True,
        )


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
