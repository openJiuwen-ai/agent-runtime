# coding: utf-8
"""e2e ②：一条用户面聊天消息打通（gateway → runtime → agentserver 子进程 → mock LLM）。

链路（除 LLM 本体全部真实组件）：
1. 管理面建实例 + 容器模板（env 注入 mock LLM 端点,即模型凭证经管理面下发）
   + 服务配置模板 + 服务资源（match user1）→ runtime config_sync 落 scope；
2. 用户面 WS 连 gateway /ws（query 携带 user_id/bot_id/group_id,runtime 模式免 token）；
3. chat.send → gateway 会话路由（POST runtime /api/session/route）→ runtime
   按 scope 部署 agentserver **子进程**（SubprocessPodClient）→ gateway 经
   pod_sse_url 直连 agentserver 发消息；
4. agentserver 以容器模板 env 里的 OpenAI 兼容配置调用 mock LLM,回复经
   chat.delta/chat.final 事件流回到 WS 客户端。
"""

from __future__ import annotations

import uuid

import pytest

from ..helpers.gateway_ws import GatewayWsClient
from ..helpers.mock_llm import MockLLM

pytestmark = pytest.mark.e2e

CHAT_FINAL_TIMEOUT_S = 300.0   # 首聊含 agentserver 子进程冷启动(workspace 初始化)
# mock_llm / chat_setup 夹具在 conftest.py（test_03 前端链路复用）


async def test_user_chat_message_end_to_end(
        stack, cfg, chat_setup, mock_llm: MockLLM) -> None:
    jid = chat_setup["jid"]
    bot_id = chat_setup["bot_id"]
    session_id = f"sess-{uuid.uuid4().hex[:10]}"
    req_id = f"req-{uuid.uuid4().hex[:10]}"
    url = (f"ws://127.0.0.1:{cfg.gateway_web_port}/ws"
           f"?user_id=user1&bot_id={bot_id}&group_id={jid}")

    async with GatewayWsClient(url) as ws:
        res = await ws.chat_send(
            session_id=session_id, content="你好,请原样回复固定台词",
            req_id=req_id, timeout=30.0)
        assert res["ok"] is True, res
        assert res["payload"]["accepted"] is True
        assert res["payload"]["session_id"] == session_id

        final: dict | None = None
        async for frame in ws.until_final(req_id=req_id,
                                          timeout=CHAT_FINAL_TIMEOUT_S):
            if frame["event"] == "chat.error":
                raise AssertionError(f"chat.error: {frame}")
            if frame["event"] == "chat.final":
                final = frame

        assert final is not None
        content = final["payload"]["content"]
        assert content.startswith("mock-llm-reply"), content

        # mock LLM 实收请求 → 消息确实穿过了 agentserver
        assert mock_llm.requests, "mock LLM got no request"
        assert "固定台词" in mock_llm.last_user_text()
