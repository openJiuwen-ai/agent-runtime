# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway Config Receiver HTTP 客户端。"""

from __future__ import annotations

from typing import Any

import httpx

from manager_server.infrastructure.logger import get_logger
from manager_server.manager_config_push.endpoint import require_gateway_endpoint

logger = get_logger(__name__)


async def gateway_request(
    jiuwenclaw_id: str,
    method: str,
    path: str,
    business: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """向 Gateway Config Receiver 发一次同步写请求。

    ``path`` 为绝对路径，如 ``/api/v1/logging``；``business`` 为业务字段 JSON。
    """
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")
    if not path.startswith("/"):
        raise ValueError(f"path must start with /: {path!r}")

    endpoint = await require_gateway_endpoint(jid)
    payload = dict(business or {})

    url = f"{endpoint}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.request(method.upper(), url, json=payload)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"gateway HTTP push failed jiuwenclaw_id={jid!r} url={url}: {exc}"
        ) from exc

    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            detail = resp.json().get("detail") or detail
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(
            f"gateway HTTP push rejected status={resp.status_code} detail={detail!r}"
        )

    result = None
    try:
        data = resp.json()
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, dict) and "result" in inner:
                result = inner.get("result")
            else:
                result = inner
    except Exception:  # noqa: BLE001
        pass

    logger.info(
        "[ManagerConfigPush] ok jiuwenclaw_id=%s %s %s", jid, method.upper(), path
    )
    return {
        "success_flag": True,
        "result": result,
        "transport": "http",
    }
