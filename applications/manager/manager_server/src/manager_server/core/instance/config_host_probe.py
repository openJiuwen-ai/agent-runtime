# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""创建/更新实例时探测 Gateway / Runtime 配置下发地址是否可达。"""

from __future__ import annotations

from typing import Literal

import httpx

Side = Literal["gateway", "runtime"]

# Gateway Config Receiver：``GET /api/v1/health``（manager_config_receiver）
_GATEWAY_HEALTH_PATH = "/api/v1/health"
# Agent Runtime：``GET /healthz``（agent_runtime main._register_healthz）
_RUNTIME_HEALTH_PATH = "/healthz"

_DEFAULT_TIMEOUT = 5.0


def _health_path(side: Side) -> str:
    return _GATEWAY_HEALTH_PATH if side == "gateway" else _RUNTIME_HEALTH_PATH


async def probe_config_host(
    base_url: str,
    *,
    side: Side,
    timeout: float = _DEFAULT_TIMEOUT,
) -> None:
    """对配置下发基址做健康检查；不通则抛 ``ValueError``。

    - gateway → ``{base}/api/v1/health``，期望 HTTP 200
    - runtime → ``{base}/healthz``，期望 HTTP 200 且 ``ok`` 不为 false
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError(f"{side}_config_host is empty")
    if not (base.startswith("http://") or base.startswith("https://")):
        raise ValueError(
            f"{side}_config_host must be an http(s) URL, got {base_url!r}"
        )

    path = _health_path(side)
    url = f"{base}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"{side} health check failed url={url}: {exc}"
        ) from exc

    if resp.status_code >= 400:
        detail = resp.text[:300]
        try:
            body = resp.json()
            if isinstance(body, dict):
                detail = str(body.get("detail") or body.get("message") or detail)
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(
            f"{side} health check rejected status={resp.status_code} "
            f"url={url} detail={detail!r}"
        )

    if side == "runtime":
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = None
        if isinstance(body, dict) and body.get("ok") is False:
            raise ValueError(
                f"runtime health check not ready url={url} body={body!r}"
            )


async def check_config_host_alive(
    base_url: str,
    *,
    side: Side,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bool:
    """健康检查是否成功（失败返回 False，不抛异常）。"""
    try:
        await probe_config_host(base_url, side=side, timeout=timeout)
        return True
    except ValueError:
        return False


async def require_config_hosts_reachable(
    *,
    gateway_config_host: str | None = None,
    runtime_config_host: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> None:
    """对非空的 gateway / runtime host 依次探活；任一失败即抛错。"""
    if gateway_config_host:
        await probe_config_host(
            gateway_config_host, side="gateway", timeout=timeout
        )
    if runtime_config_host:
        await probe_config_host(
            runtime_config_host, side="runtime", timeout=timeout
        )
