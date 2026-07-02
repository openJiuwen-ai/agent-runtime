# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
user_router flow-control unit tests.

This file focuses on:
1) _check_rate_limit branch behavior with mocked Redis eval results.
2) Probe related interfaces (_is_flow_control_probe / _build_flow_control_probe_response).
3) Task mapping helper behavior used by flow-control fast-path.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.dispatch import (
    DEFAULT_GLOBAL_MAX_REQUESTS,
    DEFAULT_GLOBAL_WINDOW_SECONDS,
    DEFAULT_SESSION_MAX_REQUESTS,
    DEFAULT_SESSION_WINDOW_SECONDS,
    ERROR_CODE_RATE_LIMIT_EXCEEDED,
    ERROR_MSG_RATE_LIMIT_EXCEEDED,
    _build_flow_control_probe_response,
    _check_rate_limit,
    _ensure_task_mapping,
    _get_rate_limit_config,
    _is_flow_control_probe,
)


class _FakeEvalClient:
    def __init__(self, *, result=None, exc: Exception | None = None):
        self.result = result
        self.exc = exc
        self.last_call = None

    async def eval(self, script, key_count, *args):
        self.last_call = {
            "script": script,
            "key_count": key_count,
            "keys": list(args[:key_count]),
            "argv": list(args[key_count:]),
        }
        if self.exc is not None:
            raise self.exc
        return self.result


class _FakeRedis:
    def __init__(self, *, client=None, raise_client_runtime: bool = False):
        self._client = client
        self._raise_client_runtime = raise_client_runtime

    @property
    def client(self):
        if self._raise_client_runtime:
            raise RuntimeError("redis client not initialized")
        return self._client


class _FakeMappingRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set_nx(self, key: str, value: str, ex=None):
        if key in self.data:
            return False
        self.data[key] = value
        return True


def _settings(**kwargs):
    defaults = {
        "redis_host": "configured",
        "rate_limit_max_requests": 1,
        "rate_limit_window_seconds": 10,
        "global_rate_limit_max_requests": 3,
        "global_rate_limit_window_seconds": 30,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ============================================================================
# _get_rate_limit_config
# ============================================================================


def test_get_rate_limit_config_falls_back_to_defaults_for_invalid_values():
    cfg = _get_rate_limit_config(
        "edp_agent",
        SimpleNamespace(
            rate_limit_max_requests="invalid",
            rate_limit_window_seconds=0,
            global_rate_limit_max_requests=-1,
            global_rate_limit_window_seconds=None,
        ),
    )
    assert cfg["enabled"] is True
    assert cfg["session_max_requests"] == DEFAULT_SESSION_MAX_REQUESTS
    assert cfg["session_window_seconds"] == DEFAULT_SESSION_WINDOW_SECONDS
    assert cfg["global_max_requests"] == DEFAULT_GLOBAL_MAX_REQUESTS
    assert cfg["global_window_seconds"] == DEFAULT_GLOBAL_WINDOW_SECONDS


# ============================================================================
# _check_rate_limit
# ============================================================================


@pytest.mark.asyncio
async def test_check_rate_limit_fail_open_when_redis_host_missing():
    allowed, error_msg, error_code = await _check_rate_limit(
        redis=_FakeRedis(client=_FakeEvalClient(result=[0, "session", 1, 0])),
        settings=_settings(redis_host=""),
        agent_id="edp_agent",
        conversation_id="conv-x",
    )
    assert allowed is True
    assert error_msg is None
    assert error_code is None


@pytest.mark.asyncio
async def test_check_rate_limit_fail_open_when_redis_client_uninitialized():
    allowed, error_msg, error_code = await _check_rate_limit(
        redis=_FakeRedis(raise_client_runtime=True),
        settings=_settings(),
        agent_id="edp_agent",
        conversation_id="conv-x",
    )
    assert allowed is True
    assert error_msg is None
    assert error_code is None


@pytest.mark.asyncio
async def test_check_rate_limit_skips_when_rate_limit_disabled(monkeypatch):
    monkeypatch.setattr(
        "api.dispatch._get_rate_limit_config",
        lambda _agent_id, _settings_obj: {"enabled": False},
    )

    allowed, error_msg, error_code = await _check_rate_limit(
        redis=_FakeRedis(client=_FakeEvalClient(result=[0, "session", 1, 0])),
        settings=_settings(),
        agent_id="edp_agent",
        conversation_id="conv-x",
    )
    assert allowed is True
    assert error_msg is None
    assert error_code is None


@pytest.mark.asyncio
async def test_check_rate_limit_rejects_session_exceeded():
    allowed, error_msg, error_code = await _check_rate_limit(
        redis=_FakeRedis(client=_FakeEvalClient(result=[0, "session", 1, 2])),
        settings=_settings(),
        agent_id="edp_agent",
        conversation_id="conv-s",
    )
    assert allowed is False
    assert error_msg == ERROR_MSG_RATE_LIMIT_EXCEEDED
    assert error_code == ERROR_CODE_RATE_LIMIT_EXCEEDED


@pytest.mark.asyncio
async def test_check_rate_limit_rejects_global_exceeded():
    allowed, error_msg, error_code = await _check_rate_limit(
        redis=_FakeRedis(client=_FakeEvalClient(result=[0, "global", 1, 3])),
        settings=_settings(),
        agent_id="edp_agent",
        conversation_id="conv-g",
    )
    assert allowed is False
    assert error_msg == ERROR_MSG_RATE_LIMIT_EXCEEDED
    assert error_code == ERROR_CODE_RATE_LIMIT_EXCEEDED


@pytest.mark.asyncio
async def test_check_rate_limit_rejects_unknown_reason_as_safe_default():
    allowed, error_msg, error_code = await _check_rate_limit(
        redis=_FakeRedis(client=_FakeEvalClient(result=[0, "unknown", 1, 3])),
        settings=_settings(),
        agent_id="edp_agent",
        conversation_id="conv-u",
    )
    assert allowed is False
    assert error_msg == ERROR_MSG_RATE_LIMIT_EXCEEDED
    assert error_code == ERROR_CODE_RATE_LIMIT_EXCEEDED


@pytest.mark.asyncio
async def test_check_rate_limit_allows_and_builds_expected_redis_keys():
    client = _FakeEvalClient(result=[1, "ok_existing", 1, 3])

    allowed, error_msg, error_code = await _check_rate_limit(
        redis=_FakeRedis(client=client),
        settings=_settings(),
        agent_id="edp_agent",
        conversation_id="conv-ok",
    )

    assert allowed is True
    assert error_msg is None
    assert error_code is None
    assert client.last_call is not None
    assert client.last_call["key_count"] == 2
    assert client.last_call["keys"][0] == "a2a_service:rate_limit:edp_agent:session:conv-ok"
    assert client.last_call["keys"][1] == "a2a_service:rate_limit:edp_agent:global"


@pytest.mark.asyncio
async def test_check_rate_limit_fail_open_when_lua_result_invalid_shape():
    allowed, error_msg, error_code = await _check_rate_limit(
        redis=_FakeRedis(client=_FakeEvalClient(result="bad-result")),
        settings=_settings(),
        agent_id="edp_agent",
        conversation_id="conv-bad",
    )
    assert allowed is True
    assert error_msg is None
    assert error_code is None


@pytest.mark.asyncio
async def test_check_rate_limit_fail_open_when_lua_eval_raises():
    allowed, error_msg, error_code = await _check_rate_limit(
        redis=_FakeRedis(client=_FakeEvalClient(exc=RuntimeError("boom"))),
        settings=_settings(),
        agent_id="edp_agent",
        conversation_id="conv-ex",
    )
    assert allowed is True
    assert error_msg is None
    assert error_code is None


# ============================================================================
# Probe and mapping helper interfaces
# ============================================================================


def test_is_flow_control_probe_detects_reserved_prefix():
    assert _is_flow_control_probe("flow-control-test:abc") is True
    assert _is_flow_control_probe("hello flow-control-test:abc") is False


def test_build_flow_control_probe_response_fields():
    payload = _build_flow_control_probe_response("conv-1", "agent-1")
    assert payload == {
        "success": True,
        "answer": "flow-control-test-ok",
        "conversation_id": "conv-1",
        "agent_id": "agent-1",
    }


@pytest.mark.asyncio
async def test_ensure_task_mapping_creates_once_and_reuses_existing():
    redis = _FakeMappingRedis()

    first = await _ensure_task_mapping(redis, "conv-1")
    second = await _ensure_task_mapping(redis, "conv-1")

    assert isinstance(first, str) and first
    assert second == first
