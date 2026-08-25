# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""解析 Gateway HTTP 入口（``instance_info.data.gateway_endpoint``）。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance.instance_service import get_instance_row, list_instance_rows


def resolve_gateway_endpoint(row: Any) -> str | None:
    data = getattr(row, "data", None)
    if not isinstance(data, dict):
        return None
    ep = str(data.get("gateway_endpoint") or "").strip().rstrip("/")
    return ep or None


async def require_gateway_endpoint(jiuwenclaw_id: str) -> str:
    from manager_server.infrastructure.db import get_db_handler

    row = await get_instance_row(get_db_handler(), jiuwenclaw_id)
    if row is None:
        raise ValueError(f"instance not found: {jiuwenclaw_id!r}")
    endpoint = resolve_gateway_endpoint(row)
    if not endpoint:
        raise ValueError(
            f"no gateway_endpoint for jiuwenclaw_id={jiuwenclaw_id!r}; "
            "gateway must register/heartbeat with endpoint"
        )
    return endpoint


async def list_reachable_jiuwenclaw_ids(
    handler: DBHandler | None = None,
    *,
    status: str = "online",
) -> list[str]:
    from manager_server.infrastructure.db import get_db_handler

    h = handler or get_db_handler()
    rows, _ = await list_instance_rows(h, status=status, offset=0, limit=10_000)
    out: list[str] = []
    for row in rows:
        jid = str(getattr(row, "jiuwenclaw_id", "") or "").strip()
        if jid and resolve_gateway_endpoint(row):
            out.append(jid)
    return out
