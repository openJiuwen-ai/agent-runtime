# coding: utf-8
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

from types import SimpleNamespace

import pytest
from a2a.types.a2a_pb2 import Message, Part, TASK_STATE_COMPLETED, TaskStatus, TaskStatusUpdateEvent
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.dispatch as dispatch_module
from opentelemetry.trace import SpanKind
from api.dispatch import router
from channels.base import ParsedRequest
from channels.registry import RouteSpec
from tests.framework_parallel._helpers import make_fake_tracer, patch_tracer


class _Registry:
    def __init__(self, spec=None, params=None) -> None:
        self.spec = spec
        self.params = params or {"project_id": "demo", "agent_id": "agent-a", "conversation_id": "conv-1"}

    def match_route(self, _path):
        return self.spec, dict(self.params) if self.spec is not None else {}


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.json_values: dict[str, object] = {}
        self.set_nx_result = True
        self.incr_values: dict[str, int] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set_nx(self, key: str, value: str, ex=None):  # noqa: ARG002
        if self.set_nx_result:
            self.values[key] = value
            return True
        return False

    async def delete(self, key: str):
        self.values.pop(key, None)

    async def get_json(self, key: str):
        return self.json_values.get(key)

    async def set_json(self, key: str, value, ex=None):  # noqa: ARG002
        self.json_values[key] = value

    async def incr(self, key: str):
        val = self.incr_values.get(key, 0) + 1
        self.incr_values[key] = val
        return val

    async def expire(self, key: str, seconds: int):  # noqa: ARG002
        return True


class _TaskStore:
    def __init__(self, task=None) -> None:
        self.task = task

    async def get(self, *_args):
        return self.task


class _Executor:
    def __init__(self, *, fail=False) -> None:
        self.fail = fail
        self.cancelled = []

    async def cancel_task(self, conversation_id: str):
        self.cancelled.append(conversation_id)

    async def execute(self, ctx, event_queue):
        if self.fail:
            raise RuntimeError("boom")
        message = Message(parts=[Part(text="answer")])
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=ctx.task_id or "task",
                context_id=ctx.context_id,
                status=TaskStatus(state=TASK_STATE_COMPLETED, message=message),
            )
        )


class _Channel:
    name = "test"

    def __init__(self, *, stream=False, fail_parse=False) -> None:
        self.stream = stream
        self.fail_parse = fail_parse

    def parse_request(self, body, *, path_params, headers=None, params=None):
        if self.fail_parse:
            raise ValueError("bad request")
        return ParsedRequest(
            conversation_id=path_params.get("conversation_id", "conv-1"),
            agent_id=path_params.get("agent_id", "agent-a"),
            query=body.get("input", {}).get("query", ""),
            body=body,
            headers=headers or {},
            params=params or {},
            stream=body.get("stream", self.stream),
            trace_id="",
        )

    @staticmethod
    def build_message(parsed):
        msg = Message(message_id="msg", context_id=parsed.conversation_id, parts=[Part(text=parsed.query)])
        return SimpleNamespace(message=msg)

    @staticmethod
    def format_event(event, *, agent_id, conversation_id, elapsed):  # noqa: ARG002
        return {"success": True, "custom_rsp_data": {"event": event["type"], "content": "x"}}


def _client(channel=None, *, registry=True, executor=None, redis=None, task_store=None):
    app = FastAPI()
    app.include_router(router)
    channel = channel or _Channel()
    spec = RouteSpec(
        route_key="test",
        prefix="/v1",
        path_template="/{project_id}/agents/{agent_id}/conversations/{conversation_id}",
        channel=channel,
    )
    app.state.adapter_registry = _Registry(spec if registry else None)
    app.state.redis = redis or _Redis()
    app.state.task_store = task_store or _TaskStore()
    app.state.executor = executor or _Executor()
    return TestClient(app), app


@pytest.fixture(autouse=True)
def _patch_logging_and_settings(monkeypatch):
    monkeypatch.setattr(
        dispatch_module,
        "get_settings",
        lambda: SimpleNamespace(redis_host="", rate_limit_max_requests=1),
    )
    monkeypatch.setattr(dispatch_module, "to_logger", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch_module, "build_http_trace", lambda **_kwargs: "trace")
    yield


def test_cancel_route_not_found_and_success():
    client, _app = _client(registry=False)
    response = client.post("/v1/demo/agents/agent-a/conversations/conv-1/cancel")
    assert response.status_code == 404

    client, app = _client()
    response = client.post("/v1/demo/agents/agent-a/conversations/conv-1/cancel")
    assert response.status_code == 200
    assert response.json() == {"status": "cancel_requested"}
    assert app.state.executor.cancelled == ["conv-1"]


def test_dispatch_rejects_route_content_type_json_and_parse_errors(monkeypatch):
    client, _app = _client(registry=False)
    assert client.post("/v1/demo/agents/agent-a/conversations/conv-1", json={}).status_code == 404

    client, _app = _client()
    assert client.post("/v1/demo/agents/agent-a/conversations/conv-1", content="x").status_code == 415
    assert client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1",
        content="{",
        headers={"content-type": "application/json"},
    ).status_code == 400

    async def fake_tag_context(**_kwargs):
        return SimpleNamespace(
            content_type="application/json",
            request_body_snapshot={},
            request_headers={},
            log_context={},
        )

    monkeypatch.setattr(dispatch_module, "build_http_request_tag_context", fake_tag_context)
    assert client.post("/v1/demo/agents/agent-a/conversations/conv-1", json=[]).status_code == 400

    client, _app = _client(channel=_Channel(fail_parse=True))
    response = client.post("/v1/demo/agents/agent-a/conversations/conv-1", json={"input": {"query": "x"}})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_dispatch_rate_limit_non_stream_and_stream(monkeypatch):
    async def limited(**_kwargs):
        return False, "limited", "100001"

    monkeypatch.setattr(dispatch_module, "_check_rate_limit", limited)

    client, _app = _client()
    response = client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1",
        json={"input": {"query": "x"}, "stream": False},
    )
    assert response.status_code == 429
    assert response.json()["error_code"] == "100001"

    response = client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1",
        json={"input": {"query": "x"}, "stream": True},
    )
    assert response.status_code == 429
    assert "100001" in response.text


def test_dispatch_flow_control_fast_path_non_stream_and_stream():
    client, app = _client()
    response = client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1?debug=1",
        json={"input": {"query": "flow-control-test:conv-1"}, "stream": False},
    )
    assert response.status_code == 200
    assert response.json()["answer"] == "flow-control-test-ok"
    assert app.state.redis.json_values

    response = client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1",
        json={"input": {"query": "flow-control-test:conv-1"}, "stream": True},
    )
    assert response.status_code == 200
    assert "flow-control-test-ok" in response.text


def test_dispatch_non_stream_executor_success_and_failure(monkeypatch):
    client, _app = _client()
    response = client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1",
        json={"input": {"query": "normal"}, "stream": False},
    )
    assert response.status_code == 200
    assert response.json()["answer"] == "answer"

    client, _app = _client(executor=_Executor(fail=True))
    response = client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1",
        json={"input": {"query": "normal"}, "stream": False},
    )
    assert response.status_code == 200
    assert response.json()["answer"] == ""


@pytest.mark.asyncio
async def test_helpers_task_mapping_build_request_and_probe():
    redis = _Redis()
    assert dispatch_module._is_flow_control_probe("flow-control-test:x") is True
    assert dispatch_module._build_flow_control_probe_response("c", "a")["answer"] == "flow-control-test-ok"
    task_id = await dispatch_module._ensure_task_mapping(redis, "conv-1")
    assert await dispatch_module._ensure_task_mapping(redis, "conv-1") == task_id

    redis2 = _Redis()
    redis2.set_nx_result = False
    redis2.values[dispatch_module.CONV_TASK_KEY.format("conv-2")] = "existing"
    assert await dispatch_module._ensure_task_mapping(redis2, "conv-2") == "existing"

    request = dispatch_module._build_request(
        "conv",
        "query",
        {"input": {"query": "query"}},
        params={"p": "1"},
        headers={"h": "v"},
    )
    assert request.message.context_id == "conv"
    assert request.message.parts[0].text == "query"
    assert dispatch_module._extract_query({"custom_data": {"inputs": {"query": "q"}}}) == "q"
    assert dispatch_module._extract_query_params(SimpleNamespace(query_params={"a": "b"})) == {"a": "b"}
    assert dispatch_module._extract_query_params(object()) == {}


# ── http.request span（dispatch 是 HTTP 入口，span 从此处起）─────────────────────


def _inject_tracer(monkeypatch):
    """注入假 tracer，返回它（断言 span 创建用）。"""
    tracer = make_fake_tracer()
    patch_tracer(monkeypatch, tracer)
    return tracer


def test_dispatch_http_span_created_with_200_status(monkeypatch):
    tracer = _inject_tracer(monkeypatch)
    client, _app = _client()
    response = client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1",
        json={"input": {"query": "normal"}, "stream": False},
    )
    assert response.status_code == 200
    assert len(tracer.created) == 1, "一次请求恰好 1 个 http 根 span"
    args, kwargs, span = tracer.created[0]
    assert args == ("http.request",)
    assert kwargs == {"kind": SpanKind.SERVER}
    span.set_attribute.assert_any_call("session.id", "conv-1")
    span.set_attribute.assert_any_call("http.request.method", "POST")
    span.set_attribute.assert_any_call("http.route", "/v1/demo/agents/agent-a/conversations/conv-1")
    span.set_attribute.assert_any_call("http.response.status_code", 200)
    # http 根 span 必须携带请求体（用户提问在 trace 上的权威可见位置）
    body_calls = [c for c in span.set_attribute.call_args_list if c.args[0] == "openjiuwen.http.request_body"]
    assert body_calls, "http 根 span 应携带 openjiuwen.http.request_body"
    assert "normal" in body_calls[0].args[1]


def test_dispatch_http_span_records_415_status(monkeypatch):
    tracer = _inject_tracer(monkeypatch)
    client, _app = _client()
    response = client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1", content="x"
    )
    assert response.status_code == 415
    assert len(tracer.created) == 1, "415 也进 span（dispatch 是 http 入口）"
    _, _, span = tracer.created[0]
    span.set_attribute.assert_any_call("http.response.status_code", 415)


def test_dispatch_http_span_records_429_status(monkeypatch):
    async def _limited(**_kwargs):
        return False, "limited", "100001"

    monkeypatch.setattr(dispatch_module, "_check_rate_limit", _limited)
    tracer = _inject_tracer(monkeypatch)
    client, _app = _client()
    response = client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1",
        json={"input": {"query": "x"}, "stream": False},
    )
    assert response.status_code == 429
    _, _, span = tracer.created[0]
    span.set_attribute.assert_any_call("http.response.status_code", 429)


def test_dispatch_404_route_miss_creates_no_span(monkeypatch):
    """404（路由未命中）发生在 span 之前——不应创建 span。"""
    tracer = _inject_tracer(monkeypatch)
    client, _app = _client(registry=False)
    response = client.post("/v1/demo/agents/agent-a/conversations/conv-1", json={})
    assert response.status_code == 404
    assert tracer.created == [], "404 在 span 之前，不进 trace"


def test_dispatch_no_span_and_works_when_tracer_disabled(monkeypatch):
    """tracer=None（OTel 关闭）：dispatch 正常跑、不创建任何 span。"""
    patch_tracer(monkeypatch, None)
    tracer = make_fake_tracer()  # 不接入；仅证明未被调用
    client, _app = _client()
    response = client.post(
        "/v1/demo/agents/agent-a/conversations/conv-1",
        json={"input": {"query": "normal"}, "stream": False},
    )
    assert response.status_code == 200
    assert tracer.created == [], "OTel 关闭时 dispatch 不创建 span"
