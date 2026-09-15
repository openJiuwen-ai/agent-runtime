# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway / Runtime Config 下发 HTTP 客户端。

``http_request`` 为公用传输层（endpoint + mTLS + httpx）；
``gateway_request`` / ``runtime_request`` 在其上封装各自响应约定。
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import get_db_handler
from manager_server.infrastructure.logger import get_logger
from manager_server.manager_config_push.endpoint import (
    require_gateway_endpoint,
    require_runtime_endpoint,
)
from manager_server.security.link_mtls import ManagerLinkMTLSConfig

logger = get_logger(__name__)

LinkRole = Literal["gateway", "runtime"]


def _contains_credential_replace(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("operation") == "replace" and "value" in value:
            return True
        return any(_contains_credential_replace(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_credential_replace(item) for item in value)
    return False


async def http_request(
    jiuwenclaw_id: str,
    role: LinkRole,
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    *,
    endpoint: str,
    timeout: float = 30.0,
    handler: DBHandler | None = None,
    network_target: Any | None = None,
) -> httpx.Response:
    """Manager → Gateway/Runtime 的公用 HTTP 请求。

    ``endpoint`` 由调用方解析后传入（``gateway_host`` / ``runtime_host``）；
    本函数挂 mTLS、发 ``method path``。业务响应解析由
    ``gateway_request`` / ``runtime_request`` 各自处理。
    """
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")
    if not path.startswith("/"):
        raise ValueError(f"path must start with /: {path!r}")
    base = str(endpoint or "").strip().rstrip("/")
    if not base:
        raise ValueError(f"{role} endpoint is required")

    link_mtls = ManagerLinkMTLSConfig.from_env()
    db = handler
    if db is None and link_mtls.enforced:
        db = get_db_handler()
    link_target = await link_mtls.target(
        db,
        jid,
        role=role,
        endpoint=base,
    )
    endpoint = link_target.endpoint
    headers = link_target.headers
    url = f"{endpoint}{path}"
    payload = dict(json_body or {})

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            **link_target.client_kwargs,
        ) as client:
            if network_target is None:
                resp = await client.request(
                    method.upper(),
                    url,
                    json=payload,
                    **({"headers": headers} if headers else {}),
                )
            else:
                from manager_server.core.template.a2a_discovery import _pinned_request

                pinned = _pinned_request(client, url, *network_target)
                request = client.build_request(
                    method.upper(),
                    pinned.url,
                    json=payload,
                    headers={**headers, "Host": pinned.headers["Host"]},
                    extensions=pinned.extensions,
                )
                resp = await client.send(request)
    except Exception as exc:
        raise ValueError(
            f"{role} HTTP push failed jiuwenclaw_id={jid!r} url={url}: {exc}"
        ) from exc

    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            detail = resp.json().get("detail") or detail
        except Exception:  # noqa: BLE001, S110
            pass
        raise ValueError(
            f"{role} HTTP push rejected status={resp.status_code} detail={detail!r}"
        )
    return resp


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
    endpoint = await require_gateway_endpoint(jid)
    payload = dict(business or {})
    network_target = None
    if path.startswith("/api/v1/a2a-outbound-templates") and _contains_credential_replace(
        payload
    ):
        from manager_server.core.template.a2a_discovery import _normalize_url, _validate_target

        data = payload.get("data") or {}
        policy = data.get("network_policy") if isinstance(data, dict) else None
        policy = policy if isinstance(policy, dict) else {}
        flags = {
            name: policy.get(name) is True
            for name in ("allow_http", "allow_private_network", "allow_public_http")
        }
        _normalize_url(endpoint, None)
        try:
            network_target = await _validate_target(endpoint, **flags)
        except ValueError as exc:
            raise ValueError(
                f"A2A credential sync blocked by network access settings: {exc}"
            ) from exc

    resp = await http_request(
        jid,
        "gateway",
        method,
        path,
        payload,
        endpoint=endpoint,
        timeout=timeout,
        network_target=network_target,
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
    except Exception:  # noqa: BLE001, S110
        pass

    logger.info("[ManagerConfigPush] ok jiuwenclaw_id=%s %s %s", jid, method.upper(), path)
    return {
        "success_flag": True,
        "result": result,
        "transport": "http",
    }


async def runtime_request(
    jiuwenclaw_id: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
    handler: DBHandler | None = None,
) -> dict[str, Any]:
    """向 Agent Runtime 发一次同步写请求。

    ``path`` 为绝对路径，如 ``/api/session/config_sync``。
    无 ``runtime_host`` 时抛 ``ValueError``（与 gateway_request 对称）。
    """
    jid = str(jiuwenclaw_id or "").strip()
    endpoint = await require_runtime_endpoint(jid)
    resp = await http_request(
        jid,
        "runtime",
        method,
        path,
        payload,
        endpoint=endpoint,
        timeout=(
            settings.agent_runtime_sync_timeout if timeout is None else float(timeout)
        ),
        handler=handler,
    )
    body: Any = None
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001, S110
        body = None
    if isinstance(body, dict) and body.get("ok") is False:
        raise ValueError(body.get("error_message") or "agent runtime request failed")
    logger.info(
        "[ManagerConfigPush] runtime ok jiuwenclaw_id=%s %s %s",
        jid,
        method.upper(),
        path,
    )
    return body if isinstance(body, dict) else {"ok": True}
