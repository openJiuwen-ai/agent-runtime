# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""用户面动态上游解析（nginx auth_request 子请求）。

按 Cookie ``jiuwenclaw_id`` 解析 Manager 可达的 Gateway 上游（HTTP/WS 均取
``instance_info.gateway_host``）；缺 Cookie 或实例未配置时回退
``MANAGER_WEB_GATEWAY_*_TARGET``。

nginx 变量 ``proxy_pass`` 的 ``resolver`` **不会**使用 ``/etc/resolv.conf`` 的
search 域，因此短名 ``svc`` / ``svc.ns`` 会 Host not found；下发前扩成
``*.svc.cluster.local`` FQDN。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance.instance_service import get_instance_row
from manager_server.core.user_console.services import UserConsoleService
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.logger import get_logger

JIUWENCLAW_ID_COOKIE = "jiuwenclaw_id"

_HDR_GATEWAY_HTTP = "X-Gateway-Http-Upstream"
_HDR_GATEWAY_WS = "X-Gateway-Ws-Upstream"

_CACHE_TTL_S = 5.0
_cache: dict[tuple[str, str], tuple[float, "UserFaceUpstreams"]] = {}

_log = get_logger(__name__)

_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_PUBLIC_TLDS = frozenset(
    {
        "com",
        "org",
        "net",
        "io",
        "cn",
        "edu",
        "gov",
        "co",
        "uk",
        "de",
        "jp",
        "local",
        "localhost",
        "test",
        "example",
        "invalid",
    }
)


@dataclass(frozen=True, slots=True)
class UserFaceUpstreams:
    gateway_http: str
    gateway_ws: str

    def as_headers(self) -> dict[str, str]:
        return {
            _HDR_GATEWAY_HTTP: self.gateway_http,
            _HDR_GATEWAY_WS: self.gateway_ws,
        }


def _k8s_dns_context() -> tuple[str, str] | None:
    """返回 ``(default_namespace, dns_suffix)``；非集群环境返回 None。"""
    sa = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    ns = ""
    if sa.is_file():
        try:
            ns = sa.read_text(encoding="utf-8").strip()
        except OSError:
            ns = ""
    if not ns:
        ns = (
            os.environ.get("POD_NAMESPACE")
            or os.environ.get("NAMESPACE")
            or ""
        ).strip()
    if not ns:
        return None
    cluster_domain = (
        os.environ.get("KUBERNETES_CLUSTER_DOMAIN") or "cluster.local"
    ).strip().strip(".")
    return ns, f"svc.{cluster_domain}"


def expand_k8s_hostname(hostname: str, *, default_ns: str, dns_suffix: str) -> str:
    """把 K8s 短主机名扩成 nginx resolver 可用的 FQDN。"""
    host = str(hostname or "").strip().rstrip(".")
    if not host or _IPV4_RE.match(host) or host.startswith("["):
        return host
    lower = host.lower()
    if lower in {"localhost", "127.0.0.1"} or lower.endswith(".cluster.local"):
        return host
    if lower.endswith(f".{dns_suffix}"):
        return host

    labels = host.split(".")
    if len(labels) == 1:
        return f"{host}.{default_ns}.{dns_suffix}"
    if len(labels) == 2 and labels[1].lower() not in _PUBLIC_TLDS:
        # svc.ns → svc.ns.svc.cluster.local
        return f"{host}.{dns_suffix}"
    return host


def coerce_http_upstream(raw: str) -> str:
    """规范化为 nginx ``proxy_pass`` 可用的 http(s) origin（无尾斜杠）。"""
    url = str(raw or "").strip().rstrip("/")
    if not url:
        return ""
    lower = url.lower()
    if lower.startswith("ws://"):
        url = "http://" + url[5:]
    elif lower.startswith("wss://"):
        url = "https://" + url[6:]
    elif not lower.startswith(("http://", "https://")):
        url = f"http://{url}"

    ctx = _k8s_dns_context()
    if ctx is None:
        return url

    default_ns, dns_suffix = ctx
    parsed = urlparse(url)
    if not parsed.hostname:
        return url
    expanded = expand_k8s_hostname(
        parsed.hostname, default_ns=default_ns, dns_suffix=dns_suffix
    )
    if expanded == parsed.hostname:
        return url
    # 保留端口；userinfo 一般不用
    netloc = expanded
    if parsed.port:
        netloc = f"{expanded}:{parsed.port}"
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    ).rstrip("/")


def _defaults() -> UserFaceUpstreams:
    return UserFaceUpstreams(
        gateway_http=coerce_http_upstream(settings.manager_web_gateway_http_target),
        gateway_ws=coerce_http_upstream(settings.manager_web_gateway_ws_target),
    )


def _pick(configured: Any, fallback: str) -> str:
    return coerce_http_upstream(str(configured or "")) or fallback


def _cache_get(key: tuple[str, str]) -> UserFaceUpstreams | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    expires_at, value = hit
    if expires_at <= time.monotonic():
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: tuple[str, str], value: UserFaceUpstreams) -> None:
    _cache[key] = (time.monotonic() + _CACHE_TTL_S, value)


def clear_user_face_upstream_cache() -> None:
    """测试用：清空解析缓存。"""
    _cache.clear()


async def resolve_user_face_upstreams(
    handler: DBHandler,
    *,
    user_id: str,
    groups: list[str] | None,
    jiuwenclaw_id: str | None,
    bypass_admission: bool = False,
) -> UserFaceUpstreams:
    """解析当前请求应反代的 Gateway 上游；无 Cookie 时直接回退默认。

    ``bypass_admission=True`` 跳过 ``instance_grant`` 准入检查，仅放行给
    ``platform_admin`` 等特权调用方——平台管理员需要跨所有实例读取
    gateway-api 资源（团队配额策略等），不应被逐实例授权表挡住。

    Raises:
        PermissionError: Cookie 指向的实例当前用户无权访问。
        LookupError: Cookie 指向的实例不存在（``bypass_admission=True`` 时回退到
            默认上游而非抛错，给 platform_admin 跨实例管理用）。
    """
    defaults = _defaults()
    jid = str(jiuwenclaw_id or "").strip()
    uid = str(user_id or "").strip()
    # Cache only upstream mappings, not authorization decisions: grants may be
    # revoked/disabled/expired and a refreshed token can have different groups.
    if jid and not bypass_admission:
        admitted = await UserConsoleService(handler).user_can_access_instance(uid, jid, groups)
        if not admitted:
            raise PermissionError(f"instance not admitted: {jid}")
    cache_key = (uid, jid)
    cached = _cache_get(cache_key)
    if cached is not None:
        _log.info(
            "user_face_upstream_cache_hit",
            user_id=uid,
            jiuwenclaw_id=jid or None,
            gateway_http=cached.gateway_http,
            gateway_ws=cached.gateway_ws,
        )
        return cached

    if not jid:
        _cache_set(cache_key, defaults)
        _log.info(
            "user_face_upstream_defaults",
            user_id=uid,
            gateway_http=defaults.gateway_http,
            gateway_ws=defaults.gateway_ws,
        )
        return defaults

    row = await get_instance_row(handler, jid)
    if row is None:
        # platform_admin 跨实例管理：cookie 指向的实例在 manager_dyx 不存在
        # （常见于从 user web 跳进来带别的集群的 jiuwenclaw_id）时，回退到
        # 默认上游继续请求，让 gateway 决定能否服务，而不是直接 403。
        if bypass_admission:
            _cache_set(cache_key, defaults)
            _log.info(
                "user_face_upstream_bypass_missing_instance",
                user_id=uid,
                jiuwenclaw_id=jid,
                user_web=defaults.user_web,
                gateway_http=defaults.gateway_http,
                gateway_ws=defaults.gateway_ws,
            )
            return defaults
        raise LookupError(f"instance not found: {jid}")

    raw_gateway = getattr(row, "gateway_host", None)
    resolved = UserFaceUpstreams(
        gateway_http=_pick(raw_gateway, defaults.gateway_http),
        gateway_ws=_pick(raw_gateway, defaults.gateway_ws),
    )
    if not resolved.gateway_http or not resolved.gateway_ws:
        raise LookupError(f"instance upstream incomplete: {jid}")

    _cache_set(cache_key, resolved)
    _log.info(
        "user_face_upstream_resolved",
        user_id=uid,
        jiuwenclaw_id=jid,
        raw_gateway_host=raw_gateway,
        gateway_http=resolved.gateway_http,
        gateway_ws=resolved.gateway_ws,
    )
    return resolved


__all__ = (
    "JIUWENCLAW_ID_COOKIE",
    "UserFaceUpstreams",
    "clear_user_face_upstream_cache",
    "coerce_http_upstream",
    "expand_k8s_hostname",
    "resolve_user_face_upstreams",
)
