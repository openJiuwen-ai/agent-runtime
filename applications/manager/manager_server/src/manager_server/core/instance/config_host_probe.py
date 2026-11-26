# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""创建/更新实例时探测 Gateway / Runtime / User Web 是否可达。"""

from __future__ import annotations

import logging
import os
import re
import ssl
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx

from manager_server.infrastructure.dev_mode import offline_dev_enabled
from manager_server.security.link_mtls import ManagerLinkMTLSConfig

Side = Literal["gateway", "runtime", "user_web"]


def runtime_probe_enabled() -> bool:
    """``AGENT_RUNTIME_DEPLOY_MODE=off`` 时跳过 Runtime 探活（AgentServer 静态预启动，无 Runtime 组件）。

    未设置保持探活开启（全量模式部署工具不注入该变量，行为与历史一致）。
    """
    return os.getenv("AGENT_RUNTIME_DEPLOY_MODE", "").strip().lower() != "off"

# Gateway Config Receiver：``GET /api/health``（manager_config_receiver）
_GATEWAY_HEALTH_PATH = "/api/health"
# Agent Runtime：``GET /healthz``（agent_runtime main._register_healthz）
_RUNTIME_HEALTH_PATH = "/healthz"
# User Web：无专用 health；探根路径（SPA index / 静态服务）
_USER_WEB_HEALTH_PATH = "/"

_DEFAULT_TIMEOUT = 5.0
_WEB_PROBE_CA = Path("/etc/manager/web-probe-ca")
_DNS_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


def _user_web_probe_target(base: str) -> tuple[str, Path | None]:
    """Resolve a Web URL and the CA mount path for a deployment Service."""
    parsed = urlsplit(base)
    host = (parsed.hostname or "").rstrip(".")
    target = urlsplit(os.getenv("MANAGER_WEB_USER_WEB_TARGET", ""))
    service_names = {"jiuwenswarm-web"}
    deployed_target = re.fullmatch(
        rf"({_DNS_LABEL})\.{_DNS_LABEL}\.svc(?:\.cluster\.local)?", (target.hostname or "").rstrip("."),
    )
    if deployed_target:
        service_names.add(deployed_target[1])
    match = re.fullmatch(rf"({_DNS_LABEL})(?:\.({_DNS_LABEL})(?:\.svc(?:\.cluster\.local)?)?)?", host)
    if parsed.scheme not in ("http", "https") or not match or match[1] not in service_names:
        return base, None
    namespace = match[2] or os.getenv("NAMESPACE", "")
    cert = None
    if re.fullmatch(_DNS_LABEL, namespace):
        cert = _WEB_PROBE_CA / f"{match[1]}.{namespace}.crt"
    # Existing registered Service URLs may still record the original HTTP scheme.
    return parsed._replace(scheme="https").geturl(), cert


def _issuer_not_trusted(exc: BaseException | None) -> bool:
    """Inspect the TLS verification error wrapped by HTTPX and its transport."""
    while exc is not None:
        if isinstance(exc, ssl.SSLCertVerificationError):
            # OpenSSL: missing issuer, self-signed chain, unverifiable leaf or untrusted issuer.
            return exc.verify_code in {2, 18, 19, 20, 21, 27}
        exc = exc.__cause__ or exc.__context__
    return False


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

    probe_ca = None
    if side == "user_web":
        try:
            base, probe_ca = _user_web_probe_target(base)
            client_kwargs["verify"] = (
                ssl.create_default_context(cafile=probe_ca) if probe_ca and probe_ca.is_file() else True
            )
        except (OSError, ValueError) as exc:
            raise ValueError(f"user_web probe trust configuration failed: {exc}") from exc

    path = _health_path(side)
    url = f"{base}{path}" if path != "/" else f"{base}/"
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        if probe_ca is not None and _issuer_not_trusted(exc):
            raise ValueError(
                f"user_web health check failed url={url}: {exc}. Check the intermediate certificates in tls.crt; "
                "for a private issuer, supply its public CA in ca.crt of the jiuwenswarm-web-tls Secret "
                "and redeploy Web."
            ) from exc
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
    if offline_dev_enabled():
        return False
    try:
        await probe_config_host(base_url, side=side, timeout=timeout)
        return True
    except ValueError:
        return False


async def require_config_hosts_reachable(
    *,
    gateway_host: str | None = None,
    runtime_host: str | None = None,
    user_web_host: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> None:
    """对非空的 Gateway / Runtime / User Web host 依次探活；任一失败即抛错。"""
    if offline_dev_enabled():
        hosts = (("gateway", gateway_host), ("runtime", runtime_host), ("user_web", user_web_host))
        for side, host in hosts:
            if host:
                parsed = urlsplit(str(host).strip())
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ValueError(f"{side} host must be an http(s) URL")
        logging.getLogger(__name__).warning(
            "[LocalOffline] reachability validation skipped; components remain unverified"
        )
        return
    if gateway_host:
        await probe_config_host(gateway_host, side="gateway", timeout=timeout)
    if runtime_host:
        await probe_config_host(runtime_host, side="runtime", timeout=timeout)
    if user_web_host:
        await probe_config_host(user_web_host, side="user_web", timeout=timeout)
