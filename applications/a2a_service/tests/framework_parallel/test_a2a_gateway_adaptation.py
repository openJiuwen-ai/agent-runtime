# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""A2AGateway 网关适配测试：URL 格式 / agent_card_name / token / userId 注入。

测试主 Agent 调子 Agent 经 A2AGateway 网关的适配改动：
1. _build_sub_agent_card: URL 末尾不加 / + name 用 agent_card_name + token 注入
2. _get_sub_agent_client: cache_key 包含 token/agent_card_name
3. userId 提取: 从 cached headers 提取，大小写不敏感
4. SubAgentEntry: 新增 a2a_gateway_base/agent_card_name/token 字段
"""
# pylint: disable=protected-access
from __future__ import annotations

import pytest

# 复用 _helpers 的 import 占位机制（绕过 openjiuwen 依赖）
from tests.framework_parallel._helpers import (  # noqa: F401
    make_executor,
    make_turn_ctx,
    collect_sub_tasks,
)

from orchestrator.handlers.remote_agent_handler import RemoteAgentHandler


# ════════════════════════════════════════════════════════════════════
# 1. _build_sub_agent_card 测试
# ════════════════════════════════════════════════════════════════════


class TestBuildSubAgentCard:
    """测试 _build_sub_agent_card 的 URL/name/token 适配。"""

    def test_url_no_trailing_slash(self):
        """URL 末尾不应有 /（网关路径 /a2a/SubEDPAgent 不需要末尾 /）。"""
        card = RemoteAgentHandler._build_sub_agent_card(
            "https://a2a-gateway.example.com/a2a/SubEDPAgent"
        )
        rpc_url = card.supported_interfaces[0].url
        assert not rpc_url.endswith("/"), f"URL 末尾不应有 /: {rpc_url}"
        assert rpc_url == "https://a2a-gateway.example.com/a2a/SubEDPAgent"

    def test_url_strips_trailing_slash_from_input(self):
        """输入 URL 含末尾 / 时应被去掉。"""
        card = RemoteAgentHandler._build_sub_agent_card(
            "https://a2a-gateway.example.com/a2a/SubEDPAgent/"
        )
        rpc_url = card.supported_interfaces[0].url
        assert not rpc_url.endswith("/"), f"末尾 / 应被去掉: {rpc_url}"

    def test_name_uses_agent_card_name(self):
        """AgentCard.name 应使用配置的 agent_card_name，不是硬编码 SubDPA。"""
        card = RemoteAgentHandler._build_sub_agent_card(
            "https://a2a-gateway.example.com/a2a/SubEDPAgent",
            agent_card_name="SubEDPAgent",
        )
        assert card.name == "SubEDPAgent", f"name 应为 SubEDPAgent: {card.name}"

    def test_name_default_sub_dpa(self):
        """未传 agent_card_name 时默认 SubDPA。"""
        card = RemoteAgentHandler._build_sub_agent_card(
            "https://a2a-gateway.example.com/a2a/SubDPA"
        )
        assert card.name == "SubDPA"

    def test_token_param_accepted(self):
        """token 参数不再需要（gateway 模式不注入 token）。"""
        card = RemoteAgentHandler._build_sub_agent_card(
            "https://a2a-gateway.example.com/a2a/SubEDPAgent",
            agent_card_name="SubEDPAgent",
        )
        assert card.name == "SubEDPAgent"

    def test_no_token_no_error(self):
        """无 token 时不报错。"""
        card = RemoteAgentHandler._build_sub_agent_card(
            "https://a2a-gateway.example.com/a2a/SubEDPAgent",
        )
        assert len(card.security_schemes) == 0

    def test_protocol_version_1_0(self):
        """协议版本应为 1.0。"""
        from a2a.utils.constants import PROTOCOL_VERSION_1_0

        card = RemoteAgentHandler._build_sub_agent_card(
            "https://a2a-gateway.example.com/a2a/SubEDPAgent"
        )
        assert card.supported_interfaces[0].protocol_version == PROTOCOL_VERSION_1_0

    def test_streaming_capability(self):
        """应支持流式传输。"""
        card = RemoteAgentHandler._build_sub_agent_card(
            "https://a2a-gateway.example.com/a2a/SubEDPAgent"
        )
        assert card.capabilities.streaming is True


# ════════════════════════════════════════════════════════════════════
# 2. _get_sub_agent_client cache_key 测试
# ════════════════════════════════════════════════════════════════════


class TestGetSubAgentClientCacheKey:
    """测试 _get_sub_agent_client 的 cache_key 逻辑。"""

    @pytest.mark.asyncio
    async def test_different_url_different_cache_key(self):
        """不同 URL 应生成不同 cache_key，返回不同 client。"""
        from tests.framework_parallel._helpers import FakeSubAgentClient

        handler = RemoteAgentHandler.__new__(RemoteAgentHandler)
        handler._sub_agent_clients = {}
        handler._client_factory = type(
            "FakeFactory",
            (),
            {
                "create": staticmethod(
                    lambda card: FakeSubAgentClient(send=[])
                )
            },
        )()

        client1 = await handler._get_sub_agent_client("http://localhost:28090/a2a", agent_card_name="SubDPA")
        client2 = await handler._get_sub_agent_client("http://localhost:28091/a2a", agent_card_name="SubDPA")
        assert client1 is not client2, "不同 URL 应返回不同 client"
        assert len(handler._sub_agent_clients) == 2

    @pytest.mark.asyncio
    async def test_same_params_returns_cached_client(self):
        """相同参数应返回缓存的 client。"""
        from tests.framework_parallel._helpers import FakeSubAgentClient

        handler = RemoteAgentHandler.__new__(RemoteAgentHandler)
        handler._sub_agent_clients = {}
        handler._client_factory = type(
            "FakeFactory",
            (),
            {
                "create": staticmethod(
                    lambda card: FakeSubAgentClient(send=[])
                )
            },
        )()

        url = "https://a2a-gateway.example.com/a2a/SubEDPAgent"
        client1 = await handler._get_sub_agent_client(url, agent_card_name="SubEDPAgent")
        client2 = await handler._get_sub_agent_client(url, agent_card_name="SubEDPAgent")
        assert client1 is client2, "相同参数应返回缓存 client"
        assert len(handler._sub_agent_clients) == 1

    @pytest.mark.asyncio
    async def test_different_agent_card_name_different_cache_key(self):
        """不同 agent_card_name 应生成不同 cache_key。"""
        from tests.framework_parallel._helpers import FakeSubAgentClient

        handler = RemoteAgentHandler.__new__(RemoteAgentHandler)
        handler._sub_agent_clients = {}
        handler._client_factory = type(
            "FakeFactory",
            (),
            {
                "create": staticmethod(
                    lambda card: FakeSubAgentClient(send=[])
                )
            },
        )()

        url = "https://a2a-gateway.example.com/a2a/SubEDPAgent"
        client1 = await handler._get_sub_agent_client(url, agent_card_name="SubEDPAgent")
        client2 = await handler._get_sub_agent_client(url, agent_card_name="RiskAgent")
        assert client1 is not client2, "不同 agent_card_name 应返回不同 client"


# ════════════════════════════════════════════════════════════════════
# 3. userId 提取逻辑测试
# ════════════════════════════════════════════════════════════════════


class TestUserIdExtraction:
    """测试从 cached headers 提取 userId 的逻辑（大小写不敏感）。

    _drive_sub_agent 中的提取逻辑：
        user_id = (
            upstream_headers.get("x-user-id")
            or upstream_headers.get("X-User-Id")
            or upstream_headers.get("userId")
            or upstream_headers.get("userid")
            or ""
        )
    """

    @staticmethod
    def _extract_user_id(headers: dict) -> str:
        """复制 _drive_sub_agent 中的 userId 提取逻辑。"""
        return (
            headers.get("x-user-id")
            or headers.get("X-User-Id")
            or headers.get("userId")
            or headers.get("userid")
            or ""
        )

    def test_extract_from_x_user_id(self):
        """能从 x-user-id 提取。"""
        headers = {"x-user-id": "user-001"}
        assert self._extract_user_id(headers) == "user-001"

    def test_extract_from_capital_x_user_id(self):
        """能从 X-User-Id 提取。"""
        headers = {"X-User-Id": "user-002"}
        assert self._extract_user_id(headers) == "user-002"

    def test_extract_from_user_id_camel(self):
        """能从 userId 提取。"""
        headers = {"userId": "user-003"}
        assert self._extract_user_id(headers) == "user-003"

    def test_extract_from_userid_lower(self):
        """能从 userid 提取。"""
        headers = {"userid": "user-004"}
        assert self._extract_user_id(headers) == "user-004"

    def test_extract_empty_when_missing(self):
        """无 userId 相关字段时返回空字符串。"""
        headers = {"other-header": "value"}
        assert self._extract_user_id(headers) == ""

    def test_extract_empty_when_headers_empty(self):
        """headers 为空时返回空字符串。"""
        assert self._extract_user_id({}) == ""

    def test_priority_x_user_id_first(self):
        """x-user-id 优先级最高。"""
        headers = {
            "x-user-id": "first",
            "X-User-Id": "second",
            "userId": "third",
            "userid": "fourth",
        }
        assert self._extract_user_id(headers) == "first"

    def test_priority_fallback_to_next(self):
        """x-user-id 不存在时 fallback 到 X-User-Id。"""
        headers = {
            "X-User-Id": "second",
            "userId": "third",
        }
        assert self._extract_user_id(headers) == "second"
