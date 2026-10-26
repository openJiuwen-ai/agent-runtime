# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""专家包目录只问选中的集群，下载地址不会进入允许名单。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from manager_server.core.application_config.package_allowlist import (
    fetch_hub_catalog,
    normalize_packages,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_fetch_catalog_queries_only_the_selected_cluster() -> None:
    calls: list[tuple[str, str, str]] = []

    async def _gateway(jiuwenclaw_id, method, path, business=None, timeout=30.0):
        del business, timeout
        calls.append((jiuwenclaw_id, method, path))
        return {
            "success_flag": True,
            "result": {
                "items": [
                    {
                        "kind": "agent_template",
                        "id": "asset-1",
                        "display_name": "专家甲",
                        "version": "1.2.0",
                        "package_url": "https://example.invalid/pkg",
                        "sha256": "abc",
                    }
                ],
                "error": "",
            },
            "transport": "http",
        }

    with patch(
        "manager_server.core.application_config.package_allowlist.gateway_request",
        new=AsyncMock(side_effect=_gateway),
    ):
        payload = await fetch_hub_catalog("cluster-a")

    assert calls == [("cluster-a", "GET", "/api/v1/package-allowlists/catalog")]
    assert payload["items"] == [
        {
            "kind": "agent_template",
            "id": "asset-1",
            "display_name": "专家甲",
            "version": "1.2.0",
        }
    ]
    granted = normalize_packages(payload["items"])
    assert granted == [{"kind": "agent_template", "id": "asset-1"}]
    assert "package_url" not in granted[0]
