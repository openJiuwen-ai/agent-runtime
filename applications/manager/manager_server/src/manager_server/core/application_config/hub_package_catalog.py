# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""向 Hub 要专家和专家团清单，供专家包模板勾选。"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from manager_server.infrastructure.config import settings

logger = logging.getLogger(__name__)

_HUB_PLUGIN_TYPES = (
    ("agent_template", "agent-template"),
    ("agent_group", "agent-group"),
)
_HUB_KIND_BY_PLUGIN_TYPE = {
    "agent-template": "agent_template",
    "agent_template": "agent_template",
    "agent-group": "agent_group",
    "agent_group": "agent_group",
}
# 与用户面 HIDDEN_HUB_ASSET_IDS 同一份，企业版目录不展示。
HIDDEN_HUB_ASSET_IDS = frozenset({
    "794db1bfe8884d6897d62d3584894a59",  # 鸿蒙 App 开发专家
    "f633b145c3ab4e42b89e36fa369b2c00",  # 健康生活顾问
    "32c223d69b834c35b40bc3935c6f9f67",  # 旅游规划专家团
    "359f38ea0e77416b8d96d23ae2c12be5",  # 身材管理专家团
})
_HUB_PAGE_SIZE = 100
_HUB_MAX_PAGES = 100
_DEFAULT_HUB_BASE_URL = "https://swarmskills.openjiuwen.com"


def _package_id_is_invalid(package_id: str) -> bool:
    if not package_id or package_id in {".", ".."} or len(package_id) > 128:
        return True
    return "/" in package_id or "\\" in package_id


def _hub_base_url() -> str:
    raw = str(settings.team_skills_hub_base_url or "").strip().rstrip("/")
    if not raw:
        raw = _DEFAULT_HUB_BASE_URL
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Hub base_url must be an absolute HTTP(S) URL")
    return raw


def _catalog_row(kind: str, item: dict[str, Any]) -> dict[str, Any] | None:
    plugin_type = str(item.get("plugin_type") or "").strip().lower()
    if plugin_type and _HUB_KIND_BY_PLUGIN_TYPE.get(plugin_type) != kind:
        return None
    package_id = str(item.get("asset_id") or "").strip()
    if _package_id_is_invalid(package_id) or package_id in HIDDEN_HUB_ASSET_IDS:
        return None
    display_name = str(item.get("display_name") or item.get("name") or package_id).strip()
    version = str(item.get("public_latest_version") or item.get("latest_version") or "").strip()
    return {
        "kind": kind,
        "id": package_id,
        "display_name": display_name or package_id,
        "version": version,
        "description": str(item.get("short_desc") or "").strip(),
    }


async def _hub_plugin_page(
    client: httpx.AsyncClient,
    base_url: str,
    plugin_type: str,
    page: int,
) -> dict[str, Any]:
    response = await client.get(
        f"{base_url}/api/v1/plugins",
        params={
            "plugin_type": plugin_type,
            "page": page,
            "page_size": _HUB_PAGE_SIZE,
        },
    )
    if not response.is_success:
        raise ValueError(f"Hub API HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Hub API response is not an object")
    code = int(payload.get("code", 200))
    if code != 200:
        raise ValueError("Hub API returned a failure code")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Hub API response is missing items")
    return data


async def _list_hub_kind(
    client: httpx.AsyncClient,
    base_url: str,
    kind: str,
    plugin_type: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 1
    while page <= _HUB_MAX_PAGES:
        data = await _hub_plugin_page(client, base_url, plugin_type, page)
        raw_items = data["items"]
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            row = _catalog_row(kind, item)
            if row is None or row["id"] in seen:
                continue
            seen.add(row["id"])
            rows.append(row)
        reported = data.get("total")
        try:
            total = int(reported) if reported is not None and reported != "" else None
        except (TypeError, ValueError):
            total = None
        if not raw_items:
            break
        if total is None:
            if len(raw_items) < _HUB_PAGE_SIZE:
                break
        elif page * _HUB_PAGE_SIZE >= total:
            break
        page += 1
    return rows


async def fetch_platform_package_catalog() -> dict[str, Any]:
    """向 Hub 平台要专家和专家团清单。不经过集群。失败时返回空列表和错误说明。"""
    try:
        base_url = _hub_base_url()
        timeout = float(settings.team_skills_hub_timeout or 30.0)
        items: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            for kind, plugin_type in _HUB_PLUGIN_TYPES:
                items.extend(await _list_hub_kind(client, base_url, kind, plugin_type))
    except Exception:  # noqa: BLE001
        logger.warning("[hub_package_catalog] hub catalog request failed", exc_info=True)
        return {"items": [], "error": "catalog unavailable"}
    items.sort(key=lambda row: (row["kind"], row["id"]))
    return {"items": items, "error": ""}
