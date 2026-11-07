# coding: utf-8
"""e2e ③：两个前端进程与后端的对接链路（`jiuwenswarm-lite frontend start` 拉起后）。

- 管理面前端 :19003（python -m manager_server.manager_web）:
  SPA 静态页 200;/idp 反代到 identity;/api 反代到 manager(未带 token 401=链路通)。
- 用户面前端 :19012（python -m jiuwenswarm.channels.web.app_web）:
  SPA 静态页 200 且 index.html 运行时配置注入完成(无占位符);
  /ws 反代到 gateway——一条用户面聊天消息经前端反代全链打通。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from ..helpers.gateway_ws import GatewayWsClient

pytestmark = pytest.mark.e2e

CHAT_FINAL_TIMEOUT_S = 300.0   # 首聊含 agentserver 子进程冷启动


@pytest.fixture(scope="session")
def frontends(stack, cfg):
    """两个前端进程就绪(幂等;dist 未构建则跳过本文件用例)。"""
    import asyncio

    from jiuwenswarm_ee_lite import frontend as fe

    try:
        asyncio.run(fe.start(cfg))
    except fe.FrontendError as exc:
        pytest.skip(f"前端未就绪: {exc}")
    yield cfg


def test_manager_frontend_serves_and_relays(frontends) -> None:
    base = frontends.manager_frontend_url
    # SPA 静态页
    r = httpx.get(f"{base}/", timeout=10)
    assert r.status_code == 200
    assert "<div id=\"root\">" in r.text, "manager web index.html 缺 #root"

    # /idp → identity 反代(public_key 无鉴权,JSON 可解析即链路通)
    r = httpx.get(f"{base}/idp/v1/auth/public_key", timeout=10)
    assert r.status_code == 200, r.text
    assert r.json(), "public_key 空响应"

    # /api → manager 反代(无 token 401/403 = 到达 manager 而非代理错误)
    r = httpx.get(f"{base}/api/v1/authz/me", timeout=10)
    assert r.status_code in (401, 403), (r.status_code, r.text[:200])


def test_user_frontend_serves_spa_with_runtime_config(frontends) -> None:
    base = frontends.user_frontend_url
    r = httpx.get(f"{base}/", timeout=10)
    assert r.status_code == 200
    # 运行时配置逐请求注入:占位符必须已被替换成实际值
    assert "__JIUWENSWARM_EDITION_VALUE__" not in r.text
    assert "enterprise" in r.text, "index.html 未注入 JIUWENSWARM_EDITION"


async def test_chat_through_user_frontend_ws_relay(
        frontends, chat_setup, mock_llm) -> None:
    """聊天全链经用户面前端反代:WS→app_web /ws 隧道→gateway→…→chat.final。"""
    cfg = frontends
    bot_id = chat_setup["bot_id"]
    session_id = f"sess-{uuid.uuid4().hex[:10]}"
    req_id = f"req-{uuid.uuid4().hex[:10]}"
    url = (f"ws://127.0.0.1:{cfg.user_frontend_port}/ws"
           f"?user_id=user1&bot_id={bot_id}&group_id={chat_setup['jid']}")

    async with GatewayWsClient(url) as ws:
        res = await ws.chat_send(
            session_id=session_id, content="经用户面前端反代的固定台词",
            req_id=req_id, timeout=30.0)
        assert res["ok"] is True, res
        assert res["payload"]["accepted"] is True

        final: dict | None = None
        async for frame in ws.until_final(req_id=req_id,
                                          timeout=CHAT_FINAL_TIMEOUT_S):
            if frame["event"] == "chat.error":
                raise AssertionError(f"chat.error: {frame}")
            if frame["event"] == "chat.final":
                final = frame

        assert final is not None
        assert final["payload"]["content"].startswith("mock-llm-reply")
        assert "固定台词" in mock_llm.last_user_text()
