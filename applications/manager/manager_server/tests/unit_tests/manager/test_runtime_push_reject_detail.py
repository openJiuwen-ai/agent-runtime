# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""4xx 推送失败应透传可读文案：Runtime error_message / FastAPI detail(str|dict|list) / 422 list。

issue #200：gateway 4xx 不再包成 ``HTTP push rejected status=… detail=…``；
FastAPI/Pydantic 422 的 list 型 detail 由 ``_reject_detail`` 逐条抽 loc+msg 拼成可读文案，
而不是把整段 JSON 原文甩给上层 toast。
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from manager_server.manager_config_push.client import _reject_detail, http_request


def _fake_client_factory(response: httpx.Response) -> type:
    """构造一个返回固定 ``response`` 的 ``httpx.AsyncClient`` 替身。"""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, json=None, headers=None):
            return response

    return _FakeClient


def _patch_fake_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """挂一个不强制 mTLS 的 ``ManagerLinkMTLSConfig`` 替身。"""

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
        "manager_server.manager_config_push.client.ManagerLinkMTLSConfig",
        _FakeLink,
    )


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


def test_reject_detail_extracts_pydantic_422_list_detail() -> None:
    # 标准 FastAPI/Pydantic 422 响应体：detail 为 list of validation error items。
    # _reject_detail 应逐条抽 loc+msg 拼成可读文案，不能返回被截断的原始 JSON。
    resp = httpx.Response(
        422,
        json={
            "detail": [
                {
                    "type": "int_parsing",
                    "loc": ["body", "priority"],
                    "msg": "Input should be a valid integer, unable to parse string as an integer",
                    "input": "10000000000000000000",
                },
            ]
        },
    )
    assert _reject_detail(resp) == (
        "body.priority: Input should be a valid integer, "
        "unable to parse string as an integer"
    )


def test_reject_detail_joins_multiple_422_items() -> None:
    # 多条校验错误用 "; " 拼接；缺 loc 的条目只取 msg；缺 msg 的条目跳过。
    resp = httpx.Response(
        422,
        json={
            "detail": [
                {
                    "type": "int_parsing",
                    "loc": ["body", "priority"],
                    "msg": "Input should be a valid integer",
                },
                {"type": "missing", "loc": ["body", "policy_name"], "msg": "Field required"},
                {"type": "value_error", "msg": "Value error, bad percent"},
                {"type": "no_msg", "loc": ["body", "x"]},
                "plain string detail item",
            ]
        },
    )
    assert _reject_detail(resp) == (
        "body.priority: Input should be a valid integer; "
        "body.policy_name: Field required; "
        "Value error, bad percent; "
        "plain string detail item"
    )


def test_reject_detail_list_detail_empty_falls_back_to_text() -> None:
    # list 全是空条目时回退到原始 text（被 [:500] 截断）。
    resp = httpx.Response(
        422,
        json={"detail": [{"type": "x", "loc": ["body"]}, {"type": "y"}]},
    )
    # 抽不出任何可读 part，回退到 text（包含原始 JSON）。
    assert _reject_detail(resp).startswith("{")


@pytest.mark.asyncio
async def test_http_request_runtime_4xx_raises_clean_message(monkeypatch) -> None:
    envelope = {
        "ok": False,
        "error_code": "VALIDATION",
        "error_message": (
            "containers[0].env[1].name 'QA_V_RUN' duplicates an earlier env entry"
        ),
    }

    monkeypatch.setattr(
        "manager_server.manager_config_push.client.httpx.AsyncClient",
        _fake_client_factory(httpx.Response(400, json=envelope)),
    )
    _patch_fake_link(monkeypatch)

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
    monkeypatch.setattr(
        "manager_server.manager_config_push.client.httpx.AsyncClient",
        _fake_client_factory(httpx.Response(400, json={"detail": "bad gateway payload"})),
    )
    _patch_fake_link(monkeypatch)

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


@pytest.mark.asyncio
async def test_http_request_gateway_422_list_detail_passes_through_readable(
    monkeypatch,
) -> None:
    # issue #200：标准 FastAPI/Pydantic 422 的 detail 是 list，_reject_detail 应逐条
    # 抽 loc+msg 拼成可读文案透传，不能把整段 JSON 原文甩给上层 toast。
    envelope = {
        "detail": [
            {
                "type": "int_parsing",
                "loc": ["body", "priority"],
                "msg": "Input should be a valid integer",
                "input": "10000000000000000000",
            }
        ]
    }
    monkeypatch.setattr(
        "manager_server.manager_config_push.client.httpx.AsyncClient",
        _fake_client_factory(httpx.Response(422, json=envelope)),
    )
    _patch_fake_link(monkeypatch)

    with pytest.raises(ValueError) as exc:
        await http_request(
            "jid-1",
            "gateway",
            "PUT",
            "/api/v1/workspace-quota/policies",
            {},
            endpoint="http://gateway.example",
        )
    assert str(exc.value) == "body.priority: Input should be a valid integer"
    assert "HTTP push rejected" not in str(exc.value)
    # 不能是整段 JSON 原文
    assert not str(exc.value).lstrip().startswith("{")
