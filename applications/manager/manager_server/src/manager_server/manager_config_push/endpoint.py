# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""解析 Gateway HTTP 入口（``instance_info.gateway_config_host``）。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance.instance_service import get_instance_row, list_instance_rows


def resolve_gateway_endpoint(row: Any) -> str | None:
    """解析 Gateway 配置下发基址。

    优先读正式列 ``gateway_config_host``；兼容旧数据 ``data.gateway_endpoint``。
    """
    host = str(getattr(row, "gateway_config_host", None) or "").strip().rstrip("/")
    if host:
        return host
    data = getattr(row, "data", None)
    if isinstance(data, dict):
        ep = str(data.get("gateway_endpoint") or "").strip().rstrip("/")
        return ep or None
    return None


async def require_gateway_endpoint(jiuwenclaw_id: str) -> str:
    from manager_server.infrastructure.db import get_db_handler

    row = await get_instance_row(get_db_handler(), jiuwenclaw_id)
    if row is None:
        raise ValueError(f"instance not found: {jiuwenclaw_id!r}")
    endpoint = resolve_gateway_endpoint(row)
    if not endpoint:
        raise ValueError(
            f"no gateway_config_host for jiuwenclaw_id={jiuwenclaw_id!r}; "
            "set gateway_config_host on the instance"
        )
    return endpoint


async def list_reachable_jiuwenclaw_ids(
    handler: DBHandler | None = None,
    *,
    status: str = "online",
) -> list[str]:
    from manager_server.infrastructure.db import get_db_handler

    h = handler or get_db_handler()
    rows, _ = await list_instance_rows(
        h,
        gateway_status=status,
        offset=0,
        limit=10_000,
    )
    out: list[str] = []
    for row in rows:
        jid = str(getattr(row, "jiuwenclaw_id", "") or "").strip()
        if jid and resolve_gateway_endpoint(row):
            out.append(jid)
    return out
