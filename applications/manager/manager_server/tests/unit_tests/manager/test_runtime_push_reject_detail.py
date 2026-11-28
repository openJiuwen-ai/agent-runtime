# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""P0：Runtime 4xx 推送失败应透传 error_message，而不是整段 JSON 包装。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from manager_server.manager_config_push.client import _reject_detail, http_request


def test_reject_detail_prefers_runtime_error_message() -> None:
    resp = httpx.Response(
        400,
        json={
            "ok": False,
            "error_code": "VALIDATION",
            "error_message": (
                "template 't1' volumes not mounted by any container: ['data1']"
            ),
        },
    )
    assert (
        _reject_detail(resp)
        == "template 't1' volumes not mounted by any container: ['data1']"
    )


def test_reject_detail_falls_back_to_fastapi_detail() -> None:
    resp = httpx.Response(400, json={"detail": "gateway said no"})
    assert _reject_detail(resp) == "gateway said no"


@pytest.mark.asyncio
async def test_http_request_runtime_4xx_raises_clean_message(monkeypatch) -> None:
    envelope = {
        "ok": False,
        "error_code": "VALIDATION",
        "error_message": (
            "containers[0].env[1].name 'QA_V_RUN' duplicates an earlier env entry"
        ),
    }

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, json=None, headers=None):
            return httpx.Response(400, json=envelope)

    class _FakeLink:
        enforced = False

        @classmethod
        def from_env(cls):
            return cls()

        async def target(self, db, jid, *, role, endpoint):
            return SimpleNamespace(
                endpoint=endpoint.rstrip("/"),
                headers={},
                client_kwargs={},
            )

    monkeypatch.setattr(
        "manager_server.manager_config_push.client.httpx.AsyncClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        "manager_server.manager_config_push.client.ManagerLinkMTLSConfig",
        _FakeLink,
    )

    with pytest.raises(ValueError) as exc:
        await http_request(
            "jid-1",
            "runtime",
            "POST",
            "/api/session/config_sync",
            {},
            endpoint="http://runtime.example",
        )
    assert str(exc.value) == (
        "containers[0].env[1].name 'QA_V_RUN' duplicates an earlier env entry"
    )
    assert "HTTP push rejected" not in str(exc.value)
    assert "error_code" not in str(exc.value)


@pytest.mark.asyncio
async def test_http_request_gateway_4xx_passes_through_detail(monkeypatch) -> None:
    # issue #200：Gateway 4xx（含 Pydantic 422 / 业务 400）应直接透传 detail，
    # 不再包成 "gateway HTTP push rejected status=400 detail='...'"，让可读文案直达用户。
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, json=None, headers=None):
            return httpx.Response(400, json={"detail": "bad gateway payload"})

    class _FakeLink:
        enforced = False

        @classmethod
        def from_env(cls):
            return cls()

        async def target(self, db, jid, *, role, endpoint):
            return SimpleNamespace(
                endpoint=endpoint.rstrip("/"),
                headers={},
                client_kwargs={},
            )

    monkeypatch.setattr(
        "manager_server.manager_config_push.client.httpx.AsyncClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        "manager_server.manager_config_push.client.ManagerLinkMTLSConfig",
        _FakeLink,
    )

    with pytest.raises(ValueError) as exc:
        await http_request(
            "jid-1",
            "gateway",
            "PUT",
            "/api/v1/logging",
            {},
            endpoint="http://gateway.example",
        )
    assert str(exc.value) == "bad gateway payload"
    assert "HTTP push rejected" not in str(exc.value)
