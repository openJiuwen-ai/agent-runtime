# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""从 Runtime ``/healthz`` 采集 namespace（不下发 jiuwenclaw_id）。"""

from __future__ import annotations

from typing import Any

import httpx

from manager_server.security.link_mtls import ManagerLinkMTLSConfig

_DEFAULT_TIMEOUT = 10.0
_HEALTH_PATH = "/healthz"


async def fetch_runtime_identity_from_health(
    runtime_config_host: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """``GET {host}/healthz``，校验 Runtime 配置接收服务可达。

    返回 ``{"namespace"}``（若响应体含该字段，否则 ``default``）；探活失败抛 ``ValueError``。
    """
    base = str(runtime_config_host or "").strip().rstrip("/")
    if not base:
        raise ValueError("runtime_config_host is empty")
    link_mtls = ManagerLinkMTLSConfig.from_env()
    base = link_mtls.resolve_endpoint(base, role="runtime")
    if not base.startswith(("http://", "https://")):
        raise ValueError(f"runtime_config_host must be an http(s) URL, got {runtime_config_host!r}")

    url = f"{base}{_HEALTH_PATH}"
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            **link_mtls.health_client_kwargs(),
        ) as client:
            resp = await client.get(url)
    except Exception as exc:
        raise ValueError(f"runtime health check failed url={url}: {exc}") from exc

    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise ValueError(
            f"runtime health check rejected status={resp.status_code} url={url} detail={detail!r}"
        )

    try:
        body = resp.json()
    except ValueError:
        body = None

    if isinstance(body, dict) and body.get("ok") is False:
        raise ValueError(f"runtime health check not ready url={url} body={body!r}")

    if not isinstance(body, dict):
        return {"namespace": "default"}

    ns = str(body.get("namespace") or "").strip() or "default"
    return {"namespace": ns}
