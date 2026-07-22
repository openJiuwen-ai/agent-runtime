# coding: utf-8
"""VersatileA2AGateway 单元测试。"""
# pylint: disable=protected-access,add-staticmethod-or-classmethod-decorator
import json

import pytest

from adapters.versatile_a2a_gateway import VersatileA2AGateway
from adapters.versatile_proxy import VersatileStreamCtx


@pytest.fixture
def gw():
    return VersatileA2AGateway(
        a2a_gateway_base="https://a2a-gateway.example.com",
        agent_card_name="KnowledgeAgent",
        token="test-token",
        url_template="{a2a_gateway_base}/a2a/{agent_card_name}",
        timeout=600,
        headers_template={"Accept": "text/event-stream"},
    )


def _make_artifact_update(artifact_id, text, append=True, last_chunk=False):
    return {
        "jsonrpc": "2.0",
        "id": "req-001",
        "result": {
            "taskId": "task-001",
            "payload": {
                "taskArtifactUpdate": {
                    "conversationId": "conv-001",
                    "append": append,
                    "lastChunk": last_chunk,
                    "artifact": {
                        "artifactId": artifact_id,
                        "type": "text",
                        "parts": [{"type": "text", "text": text}],
                    },
                }
            },
        },
    }


def _make_status_update(state, message_text=None):
    payload = {
        "taskStatusUpdate": {
            "conversationId": "conv-001",
            "state": state,
        }
    }
    if message_text:
        payload["taskStatusUpdate"]["message"] = {
            "parts": [{"type": "text", "text": message_text}]
        }
    return {
        "jsonrpc": "2.0",
        "id": "req-001",
        "result": {"taskId": "task-001", "payload": payload},
    }


class TestBuildUrl:
    def test_build_url(self, gw):
        gw._conv_id = "conv-001"
        url = gw._build_url("conv-001")
        assert url == "https://a2a-gateway.example.com/a2a/KnowledgeAgent"


class TestBuildHeaders:
    def test_required_headers(self, gw):
        gw._passed_headers = {"userId": "user-123"}
        gw._cached_user_id = gw._extract_user_id()
        gw._trace_id = "trace-001"
        headers = gw._build_headers({"userId": "user-123"})
        assert headers["token"] == "test-token"
        assert headers["userId"] == "user-123"

    def test_optional_b3_headers_present(self, gw):
        gw._passed_headers = {
            "userId": "user-123",
            "x-b3-traceid": "trace-abc",
            "x-b3-parentspanid": "parent-xyz",
            "x-b3-sampled": "1",
            "x-biz-tag": "finance",
        }
        gw._trace_id = ""
        headers = gw._build_headers(gw._passed_headers)
        assert headers["X-B3-TraceId"] == "trace-abc"
        assert headers["X-B3-ParentSpanId"] == "parent-xyz"
        assert "X-B3-SpanId" in headers
        assert headers["X-B3-Sampled"] == "1"
        assert headers["X-Biz-Tag"] == "finance"

    def test_optional_b3_headers_absent(self, gw):
        gw._passed_headers = {"userId": "user-123"}
        gw._trace_id = ""
        headers = gw._build_headers({"userId": "user-123"})
        assert "X-B3-TraceId" not in headers
        assert "X-B3-ParentSpanId" not in headers
        assert "X-Biz-Tag" not in headers


class TestBuildRequestBody:
    def test_request_body_structure(self, gw):
        gw._conv_id = "conv-001"
        gw._trace_id = "trace-001"
        gw._passed_headers = {"userId": "user-123"}
        gw._cached_user_id = gw._extract_user_id()
        body = {
            "input": {"query": "请推荐理财", "intent": "knowledge_qa"},
            "custom_data": {"inputs": {"query": "请推荐理财", "intent": "knowledge_qa"}},
        }
        rb = gw._build_request_body(body)
        assert rb["method"] == "SendStreamingMessage"
        assert rb["params"]["payload"]["message"]["conversationId"] == "conv-001"
        assert rb["params"]["payload"]["message"]["parts"][0]["text"] == "请推荐理财"
        assert rb["params"]["payload"]["configuration"]["blocking"] is True
        assert rb["params"]["payload"]["configuration"]["acceptedOutputModes"] == ["text/plain"]
        assert rb["params"]["metadata"]["userId"] == "user-123"
        assert rb["params"]["metadata"]["traceId"] == "trace-001"
        assert rb["params"]["metadata"]["versatile"]["inputs"]["query"] == "请推荐理财"


class TestArtifactUpdate:
    def test_append_true_accumulate(self, gw):
        ctx = VersatileStreamCtx()
        chunk1 = json.dumps(_make_artifact_update("A1", "低风险理财", append=True, last_chunk=False))
        chunk2 = json.dumps(_make_artifact_update("A1", "第一类", append=True, last_chunk=True))

        events1 = gw._process_chunk(chunk1, ctx)
        assert ctx.artifact_texts == {"A1": "低风险理财"}
        assert len(events1) == 1
        frame1 = json.loads(events1[0].data_proxy.raw_data)
        assert frame1 == {"event": "message", "data": {"text": "低风险理财"}}

        events2 = gw._process_chunk(chunk2, ctx)
        assert ctx.artifact_texts == {"A1": "低风险理财第一类"}
        assert len(events2) == 1
        frame2 = json.loads(events2[0].data_proxy.raw_data)
        assert frame2 == {"event": "message", "data": {"text": "第一类"}}

    def test_append_false_overwrite(self, gw):
        ctx = VersatileStreamCtx()
        chunk = json.dumps(_make_artifact_update("A1", "完整文本", append=False, last_chunk=True))
        events = gw._process_chunk(chunk, ctx)
        assert ctx.artifact_texts == {"A1": "完整文本"}
        assert len(events) == 1
        frame = json.loads(events[0].data_proxy.raw_data)
        assert frame == {"event": "message", "data": {"text": "完整文本"}}

    def test_multiple_artifacts(self, gw):
        ctx = VersatileStreamCtx()
        gw._process_chunk(json.dumps(_make_artifact_update("A1", "中间过程", append=False, last_chunk=True)), ctx)
        gw._process_chunk(json.dumps(_make_artifact_update("A2", "最终回答", append=True, last_chunk=True)), ctx)
        assert ctx.artifact_texts == {"A1": "中间过程", "A2": "最终回答"}
        assert ctx.last_artifact_id == "A2"


class TestStatusUpdate:
    def test_completed_with_result(self, gw):
        ctx = VersatileStreamCtx()
        ctx.artifact_texts = {"A1": "低风险理财推荐"}
        ctx.last_artifact_id = "A1"
        chunk = json.dumps(_make_status_update("TASK_STATE_COMPLETED"))
        events = gw._process_chunk(chunk, ctx)
        assert ctx.completed is True
        assert ctx.execution_result == "低风险理财推荐"
        # 补发 end 帧
        assert len(events) == 1
        assert json.loads(events[0].data_proxy.raw_data) == {"event": "end"}

    def test_completed_without_artifact(self, gw):
        ctx = VersatileStreamCtx()
        chunk = json.dumps(_make_status_update("TASK_STATE_COMPLETED"))
        events = gw._process_chunk(chunk, ctx)
        assert ctx.completed is True
        assert ctx.execution_result == "" or ctx.execution_result is None

    def test_failed_with_message(self, gw):
        ctx = VersatileStreamCtx()
        chunk = json.dumps(_make_status_update("TASK_STATE_FAILED", "Model only support text input"))
        events = gw._process_chunk(chunk, ctx)
        assert ctx.completed is True
        assert ctx.is_failed is True
        assert ctx.error_message == "Model only support text input"
        assert len(events) == 1
        frame = json.loads(events[0].data_proxy.raw_data)
        assert frame["event"] == "error"
        assert frame["data"]["message"] == "Model only support text input"

    def test_input_required(self, gw):
        ctx = VersatileStreamCtx()
        chunk = json.dumps(_make_status_update("TASK_STATE_INPUT_REQUIRED"))
        events = gw._process_chunk(chunk, ctx)
        assert ctx.completed is True
        assert ctx.input_required is True
        assert len(events) == 0

    def test_working_ignored(self, gw):
        ctx = VersatileStreamCtx()
        chunk = json.dumps(_make_status_update("TASK_STATE_WORKING"))
        events = gw._process_chunk(chunk, ctx)
        assert ctx.completed is False
        assert len(events) == 0


class TestOnStreamEnd:
    def test_stream_end_without_status_yields_input_required(self, gw):
        """流关闭未收到 status-update 时兜底 INPUT_REQUIRED。"""
        ctx = VersatileStreamCtx()
        events = gw._on_stream_end(ctx)
        assert len(events) == 1
        assert events[0].execution_input_required is not None

    def test_input_required_terminal(self, gw):
        ctx = VersatileStreamCtx()
        ctx.completed = True
        ctx.input_required = True
        events = gw._on_stream_end(ctx)
        assert len(events) == 1
        assert events[0].execution_input_required is not None

    def test_completed_terminal(self, gw):
        ctx = VersatileStreamCtx()
        ctx.completed = True
        ctx.execution_result = "推荐结果"
        events = gw._on_stream_end(ctx)
        assert len(events) == 1
        assert events[0].execution_completed.is_failed is False
        assert events[0].execution_completed.result == "推荐结果"

    def test_failed_terminal(self, gw):
        ctx = VersatileStreamCtx()
        ctx.completed = True
        ctx.is_failed = True
        ctx.error_message = "执行失败"
        events = gw._on_stream_end(ctx)
        assert len(events) == 1
        assert events[0].execution_completed.is_failed is True
        assert events[0].execution_completed.error_message == "执行失败"


class TestResultExtraction:
    def test_strategy_d_last_artifact(self, gw):
        ctx = VersatileStreamCtx()
        gw._process_chunk(json.dumps(_make_artifact_update("A1", "中间过程", append=False, last_chunk=True)), ctx)
        gw._process_chunk(json.dumps(_make_artifact_update("A2", "最终", append=True, last_chunk=False)), ctx)
        gw._process_chunk(json.dumps(_make_artifact_update("A2", "回答", append=True, last_chunk=True)), ctx)
        gw._process_chunk(json.dumps(_make_status_update("TASK_STATE_COMPLETED")), ctx)
        assert ctx.execution_result == "最终回答"

    def test_jsonrpc_error(self, gw):
        ctx = VersatileStreamCtx()
        chunk = json.dumps({"jsonrpc": "2.0", "id": "req-001", "error": {"code": -32600, "message": "Invalid Request"}})
        events = gw._process_chunk(chunk, ctx)
        assert ctx.completed is True
        assert ctx.is_failed is True
        assert len(events) == 1

    def test_workflow_result_node_ignored(self, gw):
        """workflow_result_node 配置不影响 A2A Gateway。"""
        gw2 = VersatileA2AGateway(
            a2a_gateway_base="https://gw.example.com",
            agent_card_name="Agent",
            token="tok",
            url_template="{a2a_gateway_base}/a2a/{agent_card_name}",
            workflow_result_node="WorkflowQAResponseNode",
        )
        ctx = VersatileStreamCtx()
        gw2._process_chunk(json.dumps(_make_artifact_update("A1", "文本", append=False, last_chunk=True)), ctx)
        gw2._process_chunk(json.dumps(_make_status_update("TASK_STATE_COMPLETED")), ctx)
        assert ctx.execution_result == "文本"
