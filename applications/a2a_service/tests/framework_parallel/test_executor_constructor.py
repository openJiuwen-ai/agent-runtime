# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Executor fallback constructor wiring tests."""
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol

from orchestrator.handlers.remote_agent_handler import RemoteAgentHandler
from orchestrator.executor import Executor


def test_fallback_constructor_passes_client_factory_to_remote_handler():
    redis = MagicMock()
    redis.get = AsyncMock(return_value="")
    redis.get_json = AsyncMock(return_value={})
    redis.set_json = AsyncMock()
    task_store = MagicMock()
    factory = MagicMock()

    executor = Executor(
        redis=redis,
        va_client=MagicMock(),
        task_store=task_store,
        client_factory=factory,
    )

    assert executor._remote_handler is not None
    assert executor._remote_handler._client_factory is factory


async def test_remote_handler_builds_sub_agent_client_from_yaml_url_card():
    redis = MagicMock()
    state_manager = MagicMock()
    client = MagicMock()
    factory = MagicMock()
    factory.create.return_value = client

    handler = RemoteAgentHandler(
        va_client=MagicMock(),
        redis=redis,
        state_manager=state_manager,
        client_factory=factory,
    )

    first = await handler._get_sub_agent_client("http://child/a2a")
    second = await handler._get_sub_agent_client("http://child/a2a")

    assert first is client
    assert second is client
    factory.create.assert_called_once()
    assert not factory.create_from_url.called
    card = factory.create.call_args.args[0]
    assert card.supported_interfaces[0].url == "http://child/a2a/"
    assert card.supported_interfaces[0].protocol_binding == TransportProtocol.JSONRPC
    assert card.supported_interfaces[0].protocol_version == PROTOCOL_VERSION_1_0
    assert card.capabilities.streaming is True
