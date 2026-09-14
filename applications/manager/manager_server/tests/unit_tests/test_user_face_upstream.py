# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""用户面动态上游解析单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from manager_server.core.user_console import user_face_upstream as upstream


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    upstream.clear_user_face_upstream_cache()
    yield
    upstream.clear_user_face_upstream_cache()


def test_coerce_http_upstream_ws_to_http() -> None:
    assert upstream.coerce_http_upstream("ws://gw:19000/") == "http://gw:19000"
    assert upstream.coerce_http_upstream("wss://gw:19000") == "https://gw:19000"
    assert upstream.coerce_http_upstream("https://web:5173/") == "https://web:5173"


def test_expand_k8s_hostname_short_and_svc_ns() -> None:
    assert (
        upstream.expand_k8s_hostname(
            "jiuwenclaw-web", default_ns="wx", dns_suffix="svc.cluster.local"
        )
        == "jiuwenclaw-web.wx.svc.cluster.local"
    )
    assert (
        upstream.expand_k8s_hostname(
            "jiuwenclaw-web.wx2", default_ns="wx", dns_suffix="svc.cluster.local"
        )
        == "jiuwenclaw-web.wx2.svc.cluster.local"
    )
    assert (
        upstream.expand_k8s_hostname(
            "jiuwenclaw-web.wx.svc.cluster.local",
            default_ns="wx",
            dns_suffix="svc.cluster.local",
        )
        == "jiuwenclaw-web.wx.svc.cluster.local"
    )
    assert (
        upstream.expand_k8s_hostname(
            "example.com", default_ns="wx", dns_suffix="svc.cluster.local"
        )
        == "example.com"
    )


def test_coerce_expands_when_k8s_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        upstream, "_k8s_dns_context", lambda: ("wx", "svc.cluster.local")
    )
    assert (
        upstream.coerce_http_upstream("http://jiuwenclaw-web:5173")
        == "http://jiuwenclaw-web.wx.svc.cluster.local:5173"
    )
    assert (
        upstream.coerce_http_upstream("http://jiuwenclaw-web.wx2:5173/")
        == "http://jiuwenclaw-web.wx2.svc.cluster.local:5173"
    )


@pytest.mark.asyncio
async def test_resolve_falls_back_to_defaults_without_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        upstream.settings,
        "manager_web_user_web_target",
        "http://default-web:5173",
    )
    monkeypatch.setattr(
        upstream.settings,
        "manager_web_gateway_http_target",
        "http://default-gw:19002",
    )
    monkeypatch.setattr(
        upstream.settings,
        "manager_web_gateway_ws_target",
        "ws://default-gw:19000",
    )
    result = await upstream.resolve_user_face_upstreams(
        handler=object(),  # type: ignore[arg-type]
        user_id="u1",
        groups=[],
        is_admin=False,
        jiuwenclaw_id=None,
    )
    assert result.user_web == "http://default-web:5173"
    assert result.gateway_http == "http://default-gw:19002"
    assert result.gateway_ws == "http://default-gw:19000"


@pytest.mark.asyncio
async def test_resolve_uses_instance_hosts_when_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        upstream.settings,
        "manager_web_user_web_target",
        "http://default-web:5173",
    )
    monkeypatch.setattr(
        upstream.settings,
        "manager_web_gateway_http_target",
        "http://default-gw:19002",
    )
    monkeypatch.setattr(
        upstream.settings,
        "manager_web_gateway_ws_target",
        "http://default-gw:19000",
    )

    async def admit(*_a: Any, **_k: Any) -> bool:
        return True

    monkeypatch.setattr(
        "manager_server.core.user_console.UserConsoleService.user_can_access_instance",
        admit,
    )
    monkeypatch.setattr(
        upstream,
        "get_instance_row",
        AsyncMock(
            return_value=SimpleNamespace(
                data={
                    "user_web_host": "http://inst-web:5173",
                    "gateway_web_http_host": "http://inst-gw:19002",
                    "gateway_web_ws_host": "ws://inst-gw:19000",
                }
            )
        ),
    )
    result = await upstream.resolve_user_face_upstreams(
        handler=object(),  # type: ignore[arg-type]
        user_id="u1",
        groups=["g1"],
        is_admin=False,
        jiuwenclaw_id="jid-1",
    )
    assert result.user_web == "http://inst-web:5173"
    assert result.gateway_http == "http://inst-gw:19002"
    assert result.gateway_ws == "http://inst-gw:19000"


@pytest.mark.asyncio
async def test_resolve_rejects_unadmitted_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def deny(*_a: Any, **_k: Any) -> bool:
        return False

    monkeypatch.setattr(
        "manager_server.core.user_console.UserConsoleService.user_can_access_instance",
        deny,
    )
    with pytest.raises(PermissionError):
        await upstream.resolve_user_face_upstreams(
            handler=object(),  # type: ignore[arg-type]
            user_id="u1",
            groups=[],
            is_admin=False,
            jiuwenclaw_id="jid-denied",
        )
