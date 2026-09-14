# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""创建/更新实例时探测 Gateway / Runtime / User Web 是否可达。"""

from __future__ import annotations

from typing import Literal

import httpx

from manager_server.security.link_mtls import ManagerLinkMTLSConfig

Side = Literal["gateway", "runtime", "user_web"]

# Gateway Config Receiver：``GET /api/health``（manager_config_receiver）
_GATEWAY_HEALTH_PATH = "/api/health"
# Agent Runtime：``GET /healthz``（agent_runtime main._register_healthz）
_RUNTIME_HEALTH_PATH = "/healthz"
# User Web：无专用 health；探根路径（SPA index / 静态服务）
_USER_WEB_HEALTH_PATH = "/"

_DEFAULT_TIMEOUT = 5.0


def _health_path(side: Side) -> str:
    if side == "gateway":
        return _GATEWAY_HEALTH_PATH
    if side == "runtime":
        return _RUNTIME_HEALTH_PATH
    return _USER_WEB_HEALTH_PATH


async def probe_config_host(
    base_url: str,
    *,
    side: Side,
    timeout: float = _DEFAULT_TIMEOUT,
) -> None:
    """对基址做健康检查；不通则抛 ``ValueError``。

    - gateway → ``{base}/api/health``，期望 2xx/3xx（非 4xx/5xx）
    - runtime → ``{base}/healthz``，期望 2xx/3xx，且 JSON ``ok`` 不为 false
    - user_web → ``{base}/``，期望 2xx/3xx（SPA 入口可达）
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError(f"{side} host is empty")
    link_mtls = ManagerLinkMTLSConfig.from_env()
    client_kwargs: dict = {"timeout": timeout, "trust_env": False}
    if side in ("gateway", "runtime"):
        base = link_mtls.resolve_endpoint(base, role=side)
        client_kwargs.update(link_mtls.health_client_kwargs())
    if not base.startswith(("http://", "https://")):
        raise ValueError(f"{side} host must be an http(s) URL, got {base_url!r}")

    path = _health_path(side)
    url = f"{base}{path}" if path != "/" else f"{base}/"
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{side} health check failed url={url}: {exc}") from exc

    if resp.status_code >= 400:
        detail = resp.text[:300]
        try:
            body = resp.json()
            if isinstance(body, dict):
                detail = str(body.get("detail") or body.get("message") or detail)
        except ValueError:
            pass
        raise ValueError(
            f"{side} health check rejected status={resp.status_code} url={url} detail={detail!r}"
        )

    if side == "runtime":
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("ok") is False:
            raise ValueError(f"runtime health check not ready url={url} body={body!r}")


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
    user_web_host: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> None:
    """对非空的 Gateway / Runtime / User Web host 依次探活；任一失败即抛错。"""
    if gateway_config_host:
        await probe_config_host(gateway_config_host, side="gateway", timeout=timeout)
    if runtime_config_host:
        await probe_config_host(runtime_config_host, side="runtime", timeout=timeout)
    if user_web_host:
        await probe_config_host(user_web_host, side="user_web", timeout=timeout)
