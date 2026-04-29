# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""VersatileProxy 静态 headers 模板 + Cookie 白名单 + 异常正文记录单元测试。

背景：
  - issue 2026-04-28：versatile_adapter 调用 Versatile 时丢失认证头（Cookie/AGENT_SID），
    导致依赖 AGENT_SID Session 的工作流（如 FUND_BETA）必然失败。
  - 修复方向（issue 第五章 5.1/5.2/5.4）：
      5.1 引入静态 headers 模板，构造 VersatileProxy 时注入；调用时作为兜底，无论
          上游是否传 Cookie 都能保证发到 Versatile。
      5.2 把 cookie 加进白名单，让上游动态 Cookie 能覆盖模板。
      5.4 HTTP 错误分支记录响应正文，便于定位 Versatile 业务错误。

本测试锁定上述三条契约。
"""
from __future__ import annotations

import logging

import pytest
from loguru import logger

from adapter.versatile_proxy import VersatileProxy


@pytest.fixture
def caplog_loguru(caplog):
    """让 loguru 的日志走 stdlib logging，从而能被 caplog 捕获。"""
    handler_id = logger.add(
        caplog.handler,
        format="{message}",
        level="DEBUG",
        filter=lambda r: True,
    )
    caplog.set_level(logging.DEBUG)
    yield caplog
    logger.remove(handler_id)


# ════════════════════════════════════════════════════════════════════
# 5.1 静态模板兜底
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dispatch_stream_injects_headers_from_template(httpx_mock):
    """构造 VersatileProxy 时传入 headers_template，每次调用都会带上模板里的头。"""
    httpx_mock.add_response(
        url="https://va.test/v1/conv-1",
        method="POST",
        content=b'data: {"event":"end"}\n\n',
        headers={"Content-Type": "text/event-stream"},
    )

    proxy = VersatileProxy(
        url_template="https://va.test/v1/{conversation_id}",
        headers_template={
            "Cookie": "AGENT_SID=testUser|0",
            "Accept": "application/json, text/event-stream",
        },
    )
    stream = proxy.dispatch_stream(body={"custom_data": {}}, conv_id="conv-1")
    [_ async for _ in stream]

    sent = httpx_mock.get_requests()[0]
    assert sent.headers["Cookie"] == "AGENT_SID=testUser|0"
    # 模板里的非白名单头也应该带上（对齐 AgentEngine 行为）
    assert sent.headers["Accept"] == "application/json, text/event-stream"


@pytest.mark.asyncio
async def test_dispatch_stream_template_applies_when_extra_headers_empty(httpx_mock):
    """没有 extra_headers 时，仍然要带模板里的头；Content-Type 必须保留。"""
    httpx_mock.add_response(
        url="https://va.test/v1/conv-2",
        method="POST",
        content=b'data: {"event":"end"}\n\n',
        headers={"Content-Type": "text/event-stream"},
    )

    proxy = VersatileProxy(
        url_template="https://va.test/v1/{conversation_id}",
        headers_template={"Cookie": "AGENT_SID=testUser|0"},
    )
    stream = proxy.dispatch_stream(body={"custom_data": {}}, conv_id="conv-2")
    [_ async for _ in stream]

    sent = httpx_mock.get_requests()[0]
    assert sent.headers["Cookie"] == "AGENT_SID=testUser|0"
    assert sent.headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_dispatch_stream_no_template_keeps_default_content_type(httpx_mock):
    """不传 headers_template 时维持原行为：仅保留 Content-Type，不主动注入 Cookie。"""
    httpx_mock.add_response(
        url="https://va.test/v1/conv-3",
        method="POST",
        content=b'data: {"event":"end"}\n\n',
        headers={"Content-Type": "text/event-stream"},
    )

    proxy = VersatileProxy(url_template="https://va.test/v1/{conversation_id}")
    stream = proxy.dispatch_stream(body={"custom_data": {}}, conv_id="conv-3")
    [_ async for _ in stream]

    sent = httpx_mock.get_requests()[0]
    assert sent.headers["Content-Type"] == "application/json"
    assert "Cookie" not in sent.headers


# ════════════════════════════════════════════════════════════════════
# 5.2 cookie 加入白名单 + 动态 Cookie 覆盖模板
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dispatch_stream_dynamic_cookie_overrides_template(httpx_mock):
    """上游 extra_headers 里的 Cookie 必须能覆盖模板（白名单允许 cookie）。"""
    httpx_mock.add_response(
        url="https://va.test/v1/conv-4",
        method="POST",
        content=b'data: {"event":"end"}\n\n',
        headers={"Content-Type": "text/event-stream"},
    )

    proxy = VersatileProxy(
        url_template="https://va.test/v1/{conversation_id}",
        headers_template={"Cookie": "AGENT_SID=fallback|0"},
    )
    stream = proxy.dispatch_stream(
        body={"custom_data": {}},
        conv_id="conv-4",
        extra_headers={"Cookie": "AGENT_SID=realUser|7"},
    )
    [_ async for _ in stream]

    sent = httpx_mock.get_requests()[0]
    assert sent.headers["Cookie"] == "AGENT_SID=realUser|7"


@pytest.mark.asyncio
async def test_dispatch_stream_existing_whitelist_headers_still_pass_through(httpx_mock):
    """现有白名单（x-user-id 等）必须维持透传，避免回归。"""
    httpx_mock.add_response(
        url="https://va.test/v1/conv-5",
        method="POST",
        content=b'data: {"event":"end"}\n\n',
        headers={"Content-Type": "text/event-stream"},
    )

    proxy = VersatileProxy(
        url_template="https://va.test/v1/{conversation_id}",
        headers_template={"Cookie": "AGENT_SID=testUser|0"},
    )
    stream = proxy.dispatch_stream(
        body={"custom_data": {}},
        conv_id="conv-5",
        extra_headers={
            "x-user-id": "u-1",
            "x-project-id": "p-1",
            "cust-token": "tok-xyz",
            "X-Should-Be-Filtered": "leak",
        },
    )
    [_ async for _ in stream]

    sent = httpx_mock.get_requests()[0]
    assert sent.headers["x-user-id"] == "u-1"
    assert sent.headers["x-project-id"] == "p-1"
    assert sent.headers["cust-token"] == "tok-xyz"
    # 非白名单头仍应被丢弃
    assert "x-should-be-filtered" not in {k.lower() for k in sent.headers.keys()}


# ════════════════════════════════════════════════════════════════════
# 5.4 异常分支记录响应正文
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dispatch_stream_logs_response_body_on_http_error(
    httpx_mock, caplog_loguru
):
    """上游返回 5xx 时，错误日志要包含响应正文（用于诊断 Versatile 业务错误）。"""
    error_body = (
        '{"event":"error","data":{"code":"103104",'
        '"message":"NoneType has no attribute content"}}'
    )
    httpx_mock.add_response(
        url="https://va.test/v1/conv-err",
        method="POST",
        status_code=500,
        content=error_body.encode("utf-8"),
    )

    proxy = VersatileProxy(url_template="https://va.test/v1/{conversation_id}")
    stream = proxy.dispatch_stream(body={"custom_data": {}}, conv_id="conv-err")
    [_ async for _ in stream]

    combined = " ".join(rec.getMessage() for rec in caplog_loguru.records)
    assert "103104" in combined, (
        f"HTTPStatusError 日志应包含响应正文（含 103104），实际：{combined!r:.300}"
    )
