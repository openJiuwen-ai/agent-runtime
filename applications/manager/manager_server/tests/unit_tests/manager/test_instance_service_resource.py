# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例运行时授权：启用优先级唯一与默认列表排序。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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

    handler = MagicMock()
    handler.list_records = AsyncMock(
        return_value=[_row(resource_id="other", priority=10, enabled=True)]
    )
    svc = InstanceServiceResourceService(handler)
    svc._tpl.get = AsyncMock(return_value={"template_id": "tpl-1"})

    with pytest.raises(ValueError, match="priority"):
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

    handler = MagicMock()
    handler.list_records = AsyncMock(
        return_value=[_row(resource_id="res-1", priority=10, enabled=True)]
    )
    svc = InstanceServiceResourceService(handler)
    await svc._assert_unique_enabled_priority(
        jiuwenclaw_id="sp-1",
        priority=10,
        enabled=True,
        exclude_resource_id="res-1",
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
