# coding: utf-8
# pylint: disable=protected-access
# 说明：本文件为单元测试，需要访问被测类的 protected 成员（如 _process_chunk、
# _on_stream_end、_build_request 等）。
"""VersatileProxy + VersatileController + VersatileWorkflow 测试。

按 TECH_VersatileAdapter.md §2.1/§2.2/§2.3 规范验证：
- URL 模板构建
- Header 白名单过滤（一级控制器默认 + YAML 覆盖）
- 请求体提取（controller 取 custom_data；workflow 直传）
- SSE 行解析（id/event/空行跳过，data: 前缀剥离）
- 一级控制器 _process_chunk：End/exception/workflow_result_node 三类特殊处理
- 一级控制器 _on_stream_end：completed + result / completed + no result / not completed
- 低码工作流 _SKIP_TYPES 过滤（finish/runCompleted/dialogId）
- ctx 状态贯穿性
"""
from __future__ import annotations

import json

import pytest

from adapters.versatile_controller import VersatileController, _FORWARD_HEADER_WHITELIST
from adapters.versatile_proxy import VersatileProxy, VersatileStreamCtx
from adapters.versatile_workflow import VersatileWorkflow
from event.events import AdapterEvent


# ════════════════════════════════════════════════════════════════════
# §2.1/§2.2 _build_url：URL 占位符
# ════════════════════════════════════════════════════════════════════


class TestBuildUrl:
    @staticmethod
    def test_controller_replaces_conversation_id():
        ctrl = VersatileController("http://h/agents/a/conversations/{conversation_id}")
        assert ctrl._build_url("c-1") == "http://h/agents/a/conversations/c-1"

    @staticmethod
    def test_controller_no_placeholder_unchanged():
        ctrl = VersatileController("http://h/ping")
        assert ctrl._build_url("any") == "http://h/ping"

    @staticmethod
    def test_workflow_replaces_both_workflow_id_and_conversation_id():
        wf = VersatileWorkflow(
            url_template="http://h/workflows/{workflow_id}/conversations/{conversation_id}",
            workflow_id="wf-xyz",
        )
        assert wf._build_url("conv-1") == "http://h/workflows/wf-xyz/conversations/conv-1"

    @staticmethod
    def test_workflow_empty_workflow_id_is_acceptable():
        wf = VersatileWorkflow(
            url_template="http://h/workflows/{workflow_id}/conversations/{conversation_id}",
            workflow_id="",
        )
        assert wf._build_url("c-1") == "http://h/workflows//conversations/c-1"


# ════════════════════════════════════════════════════════════════════
# §2.3 _build_headers：模板 + 白名单合并
# ════════════════════════════════════════════════════════════════════


class TestBuildHeaders:
    @staticmethod
    def test_controller_default_whitelist_when_unspecified():
        """VersatileController 不传 whitelist 时使用 _FORWARD_HEADER_WHITELIST 默认 5 项。"""
        ctrl = VersatileController("http://h/", forward_header_whitelist=None)
        # 默认白名单包含这些
        assert set(_FORWARD_HEADER_WHITELIST) >= {
            "x-user-id", "x-project-id", "cust-token", "cust-userid", "cookie",
        }
        h = ctrl._build_headers({"X-User-Id": "u1", "X-Other": "skip"})
        assert h.get("X-User-Id") == "u1"
        assert "X-Other" not in h  # 非默认白名单被过滤

    @staticmethod
    def test_explicit_whitelist_overrides_default():
        ctrl = VersatileController(
            "http://h/",
            forward_header_whitelist={"x-trace-id"},
        )
        h = ctrl._build_headers({"X-Trace-Id": "t1", "Cookie": "skip"})
        assert h.get("X-Trace-Id") == "t1"
        assert "Cookie" not in h  # 即便默认白名单含 cookie，被显式覆盖后过滤

    @staticmethod
    def test_template_headers_preserved():
        ctrl = VersatileController(
            "http://h/",
            headers_template={"Accept": "application/json", "stream": "true"},
        )
        h = ctrl._build_headers()
        assert h["Accept"] == "application/json"
        assert h["stream"] == "true"

    @staticmethod
    def test_content_type_defaulted_when_not_in_template():
        ctrl = VersatileController("http://h/", headers_template={"Accept": "x"})
        h = ctrl._build_headers()
        assert h["Content-Type"] == "application/json"

    @staticmethod
    def test_content_type_not_overridden_when_in_template():
        ctrl = VersatileController(
            "http://h/",
            headers_template={"Content-Type": "application/x-protobuf"},
        )
        h = ctrl._build_headers()
        assert h["Content-Type"] == "application/x-protobuf"

    @staticmethod
    def test_incoming_headers_overwrite_template():
        """白名单 header 出现在 incoming 中应覆盖模板同名 key。"""
        ctrl = VersatileController(
            "http://h/",
            headers_template={"Cookie": "from-template"},
        )
        h = ctrl._build_headers({"Cookie": "from-incoming"})
        assert h["Cookie"] == "from-incoming"

    @staticmethod
    def test_whitelist_none_or_empty_falls_back_to_default_for_controller():
        """VersatileController.__init__ 中：None 或空集 都回退到默认白名单。"""
        ctrl_none = VersatileController("http://h/", forward_header_whitelist=None)
        ctrl_empty = VersatileController("http://h/", forward_header_whitelist=set())
        # 两个实例内部白名单都指向 _FORWARD_HEADER_WHITELIST 默认值
        assert ctrl_none._forward_header_whitelist == _FORWARD_HEADER_WHITELIST
        assert ctrl_empty._forward_header_whitelist == _FORWARD_HEADER_WHITELIST

    @staticmethod
    def test_workflow_no_whitelist_passes_all_headers():
        """VersatileWorkflow 没有强制默认白名单，None 时透传全部。"""
        wf = VersatileWorkflow(
            url_template="http://h/{workflow_id}/{conversation_id}",
            workflow_id="wf",
            forward_header_whitelist=None,
        )
        h = wf._build_headers({"X-Custom-Anything": "v"})
        assert h["X-Custom-Anything"] == "v"


# ════════════════════════════════════════════════════════════════════
# §2.1 请求体提取：controller 取 custom_data；workflow 透传
# ════════════════════════════════════════════════════════════════════


class TestBuildRequestBody:
    @staticmethod
    def test_controller_extracts_custom_data():
        ctrl = VersatileController("http://h/")
        body = {"custom_data": {"inputs": {"query": "q"}}, "noise": 1}
        assert ctrl._build_request_body(body) == {"inputs": {"query": "q"}}

    @staticmethod
    def test_controller_missing_custom_data_returns_empty():
        ctrl = VersatileController("http://h/")
        assert ctrl._build_request_body({}) == {}
        assert ctrl._build_request_body({"input": "x"}) == {}

    @staticmethod
    def test_workflow_default_extracts_custom_data():
        """VersatileWorkflow 未覆写 _build_request_body，继承基类 → 也取 custom_data。"""
        wf = VersatileWorkflow("http://h/{workflow_id}/{conversation_id}", "wf")
        body = {"custom_data": {"question": "q"}}
        assert wf._build_request_body(body) == {"question": "q"}


# ════════════════════════════════════════════════════════════════════
# §2.1/§2.2 _process_line：SSE 行级过滤
# ════════════════════════════════════════════════════════════════════


class TestProcessLine:
    @pytest.fixture
    def ctrl(self):
        return VersatileController("http://h/")

    @staticmethod
    def test_data_prefix_stripped(ctrl):
        ctx = VersatileStreamCtx()
        events = ctrl._process_line('data: {"event":"message"}', ctx)
        assert len(events) == 1
        assert '"event":"message"' in events[0].data_proxy.raw_data
        assert not events[0].data_proxy.raw_data.startswith("data:")

    @staticmethod
    def test_data_prefix_without_space(ctrl):
        ctx = VersatileStreamCtx()
        events = ctrl._process_line('data:{"x":1}', ctx)
        assert len(events) == 1

    @staticmethod
    def test_blank_line_skipped(ctrl):
        ctx = VersatileStreamCtx()
        assert ctrl._process_line("", ctx) == []
        assert ctrl._process_line("   ", ctx) == []

    @staticmethod
    def test_id_line_skipped(ctrl):
        """_process_line 正确跳过 SSE id: 控制行。"""
        ctx = VersatileStreamCtx()
        assert ctrl._process_line("id: 123", ctx) == []

    @staticmethod
    def test_event_line_skipped(ctrl):
        """_process_line 正确跳过 SSE event: 控制行。"""
        ctx = VersatileStreamCtx()
        assert ctrl._process_line("event: message", ctx) == []

    @staticmethod
    def test_retry_line_skipped(ctrl):
        """_process_line 正确跳过 SSE retry: 控制行。"""
        ctx = VersatileStreamCtx()
        assert ctrl._process_line("retry: 3000", ctx) == []

    @staticmethod
    def test_comment_line_skipped(ctrl):
        """_process_line 正确跳过 SSE 注释行（冒号开头）。"""
        ctx = VersatileStreamCtx()
        assert ctrl._process_line(": this is a comment", ctx) == []

    @staticmethod
    def test_data_with_only_whitespace_after_prefix_skipped(ctrl):
        ctx = VersatileStreamCtx()
        assert ctrl._process_line("data:    ", ctx) == []


# ════════════════════════════════════════════════════════════════════
# §2.1 VersatileController._process_chunk：三类特殊处理
# ════════════════════════════════════════════════════════════════════


class TestControllerProcessChunk:
    @staticmethod
    def test_end_node_sets_completed_and_passes_through():
        ctrl = VersatileController("http://h/")
        ctx = VersatileStreamCtx()
        chunk = '{"event":"message","data":{"node_type":"End","is_finished":true}}'
        events = ctrl._process_chunk(chunk, ctx)
        assert ctx.completed is True
        assert ctx.is_failed is False
        # §2.3.1 文档：End 节点帧需要转发前端
        assert len(events) == 1
        assert events[0].data_proxy is not None

    @staticmethod
    def test_end_node_with_spaces_in_json_also_detected():
        """json.dumps 默认输出（冒号后有空格）也能命中 End 节点检测。"""
        ctrl = VersatileController("http://h/")
        ctx = VersatileStreamCtx()
        chunk = json.dumps({"event": "message", "data": {"node_type": "End", "is_finished": True}})
        events = ctrl._process_chunk(chunk, ctx)
        assert ctx.completed is True

    @staticmethod
    def test_exception_event_sets_completed_and_failed():
        ctrl = VersatileController("http://h/")
        ctx = VersatileStreamCtx()
        chunk = '{"event":"exception","data":{"message":"err"}}'
        events = ctrl._process_chunk(chunk, ctx)
        assert ctx.completed is True
        assert ctx.is_failed is True
        # exception 帧也需要转发前端
        assert len(events) == 1

    @staticmethod
    def test_exception_event_with_spaces_in_json_also_detected():
        """json.dumps 默认输出（冒号后有空格）也能命中 exception 检测。"""
        ctrl = VersatileController("http://h/")
        ctx = VersatileStreamCtx()
        chunk = json.dumps({"event": "exception", "data": {"message": "err"}})
        events = ctrl._process_chunk(chunk, ctx)
        assert ctx.completed is True
        assert ctx.is_failed is True

    @staticmethod
    def test_workflow_result_node_extracted_and_dropped():
        ctrl = VersatileController("http://h/", workflow_result_node="GXZQAResponseNode")
        ctx = VersatileStreamCtx()
        # 修复后：紧凑格式与带空格的 JSON 均可命中（兼容 json.dumps 默认输出）
        chunk = json.dumps(
            {
                "custom_rsp_data": {
                    "event": "message",
                    "data": {
                        "node_type": "QA",
                        "node_name": "GXZQAResponseNode",
                        "text": "the-answer",
                    },
                },
            }
        )
        events = ctrl._process_chunk(chunk, ctx)
        assert events == []  # 不转发前端
        assert ctx.execution_result == "the-answer"

    @staticmethod
    def test_workflow_result_node_compact_json_also_matched():
        """紧凑格式（separators=(",", ":")）同样被命中。"""
        ctrl = VersatileController("http://h/", workflow_result_node="GXZQAResponseNode")
        ctx = VersatileStreamCtx()
        chunk = json.dumps(
            {"data": {"node_type": "QA", "node_name": "GXZQAResponseNode", "text": "compact"}},
            separators=(",", ":"),
        )
        events = ctrl._process_chunk(chunk, ctx)
        assert events == []
        assert ctx.execution_result == "compact"

    @staticmethod
    def test_workflow_result_node_without_custom_rsp_envelope():
        """支持 data 直接在顶层的格式（不是 custom_rsp_data 嵌套）。"""
        ctrl = VersatileController("http://h/", workflow_result_node="GXZQAResponseNode")
        ctx = VersatileStreamCtx()
        chunk = json.dumps(
            {
                "data": {
                    "node_type": "QA",
                    "node_name": "GXZQAResponseNode",
                    "text": "flat-format",
                },
            }
        )
        events = ctrl._process_chunk(chunk, ctx)
        assert events == []
        assert ctx.execution_result == "flat-format"

    @staticmethod
    def test_workflow_result_node_empty_text_drops_without_setting_result():
        """text 为空 → 不入 ctx.execution_result，也不产 event。"""
        ctrl = VersatileController("http://h/", workflow_result_node="GXZQAResponseNode")
        ctx = VersatileStreamCtx()
        chunk = json.dumps(
            {"data": {"node_type": "QA", "node_name": "GXZQAResponseNode", "text": ""}}
        )
        events = ctrl._process_chunk(chunk, ctx)
        assert events == []
        assert ctx.execution_result is None

    @staticmethod
    def test_workflow_result_node_unparseable_json_falls_through():
        """识别到 node_name 但 JSON 无法解析 → fall back 到 data_proxy 透传。"""
        ctrl = VersatileController("http://h/", workflow_result_node="GXZQAResponseNode")
        ctx = VersatileStreamCtx()
        chunk = '{not-valid-json "node_name":"GXZQAResponseNode"'
        events = ctrl._process_chunk(chunk, ctx)
        assert len(events) == 1
        assert events[0].data_proxy is not None

    @staticmethod
    def test_other_qa_node_passes_through():
        """非 GXZQAResponseNode 的 QA 帧正常透传。"""
        ctrl = VersatileController("http://h/", workflow_result_node="GXZQAResponseNode")
        ctx = VersatileStreamCtx()
        chunk = '{"data":{"node_type":"QA","node_name":"OtherNode","text":"x"}}'
        events = ctrl._process_chunk(chunk, ctx)
        assert len(events) == 1
        assert ctx.execution_result is None

    @staticmethod
    def test_message_event_normal_passthrough():
        ctrl = VersatileController("http://h/")
        ctx = VersatileStreamCtx()
        chunk = '{"event":"message","data":{"node_type":"think","text":"thinking..."}}'
        events = ctrl._process_chunk(chunk, ctx)
        assert len(events) == 1
        assert ctx.completed is False
        assert events[0].data_proxy.raw_data == chunk

    @staticmethod
    def test_no_workflow_result_node_configured_does_not_extract():
        """workflow_result_node 未配置时，包含 GXZQAResponseNode 的帧仍正常透传。"""
        ctrl = VersatileController("http://h/", workflow_result_node=None)
        ctx = VersatileStreamCtx()
        chunk = '{"data":{"node_type":"QA","node_name":"GXZQAResponseNode","text":"x"}}'
        events = ctrl._process_chunk(chunk, ctx)
        assert len(events) == 1
        assert ctx.execution_result is None


# ════════════════════════════════════════════════════════════════════
# §2.3.2 VersatileController._on_stream_end：终态映射
# ════════════════════════════════════════════════════════════════════


class TestControllerOnStreamEnd:
    @pytest.fixture
    def ctrl(self):
        return VersatileController("http://h/")

    @staticmethod
    def test_not_completed_yields_input_required(ctrl):
        ctx = VersatileStreamCtx()
        events = ctrl._on_stream_end(ctx)
        assert len(events) == 1
        assert events[0].execution_input_required is not None

    @staticmethod
    def test_completed_with_result_yields_completed(ctrl):
        ctx = VersatileStreamCtx()
        ctx.completed = True
        ctx.execution_result = "answer"
        events = ctrl._on_stream_end(ctx)
        assert len(events) == 1
        c = events[0].execution_completed
        assert c is not None
        assert c.result == "answer"
        assert c.is_failed is False

    @staticmethod
    def test_completed_failed_with_result(ctrl):
        ctx = VersatileStreamCtx()
        ctx.completed = True
        ctx.is_failed = True
        ctx.execution_result = "exception-detail"
        events = ctrl._on_stream_end(ctx)
        assert events[0].execution_completed.is_failed is True
        assert events[0].execution_completed.result == "exception-detail"

    @staticmethod
    def test_completed_no_result_yields_nothing(ctrl):
        """End 节点完成但未提取到 workflow_result：不产 execution_completed。"""
        ctx = VersatileStreamCtx()
        ctx.completed = True
        events = ctrl._on_stream_end(ctx)
        assert events == []


# ════════════════════════════════════════════════════════════════════
# §2.2 VersatileWorkflow._process_chunk：_SKIP_TYPES 过滤
# ════════════════════════════════════════════════════════════════════


class TestWorkflowProcessChunk:
    @pytest.fixture
    def wf(self):
        return VersatileWorkflow("http://h/{workflow_id}/{conversation_id}", "wf-1")

    @staticmethod
    @pytest.mark.parametrize("skip_type", ["finish", "runCompleted", "dialogId"])
    def test_skip_types_dropped_compact(wf, skip_type):
        ctx = VersatileStreamCtx()
        chunk = f'{{"type":"{skip_type}","data":{{"content":"x"}}}}'
        assert wf._process_chunk(chunk, ctx) == []

    @staticmethod
    @pytest.mark.parametrize("skip_type", ["finish", "runCompleted", "dialogId"])
    def test_skip_types_dropped_with_spaces(wf, skip_type):
        """json.dumps 默认格式（冒号后有空格）也被正确过滤。"""
        ctx = VersatileStreamCtx()
        chunk = json.dumps({"type": skip_type, "data": {"content": "x"}})
        assert wf._process_chunk(chunk, ctx) == []

    @staticmethod
    @pytest.mark.parametrize("kept_type", ["rawData", "nodeType", "text", "answer", "message"])
    def test_non_skip_types_passthrough(wf, kept_type):
        ctx = VersatileStreamCtx()
        chunk = f'{{"type":"{kept_type}","data":{{"content":"x"}}}}'
        events = wf._process_chunk(chunk, ctx)
        assert len(events) == 1
        assert events[0].data_proxy.raw_data == chunk

    @staticmethod
    def test_skip_type_substring_partial_match_still_dropped(wf):
        """_SKIP_TYPES 用 in 子串判定：'type:"finish"' 在 chunk 中即跳过。"""
        ctx = VersatileStreamCtx()
        # 即便 JSON 嵌套场景中 finish 作为子串出现也会被跳过（实现的折中行为）
        chunk = '{"data":{"x":"y"},"type":"finish","extra":"keep"}'
        assert wf._process_chunk(chunk, ctx) == []

    @staticmethod
    def test_workflow_on_stream_end_yields_nothing(wf):
        """VersatileWorkflow._on_stream_end 始终返回 []（不产 execution_input_required）。"""
        assert wf._on_stream_end(VersatileStreamCtx()) == []


# ════════════════════════════════════════════════════════════════════
# VersatileStreamCtx：状态初始化与贯穿性
# ════════════════════════════════════════════════════════════════════


class TestStreamCtx:
    @staticmethod
    def test_initial_state():
        ctx = VersatileStreamCtx()
        assert ctx.completed is False
        assert ctx.is_failed is False
        assert ctx.execution_result is None

    @staticmethod
    def test_multiple_chunks_accumulate():
        """多个 chunk 共享同一个 ctx：第一帧 message + 第二帧 End → ctx.completed 累积为 True。"""
        ctrl = VersatileController("http://h/")
        ctx = VersatileStreamCtx()
        ctrl._process_chunk('{"event":"message"}', ctx)
        assert ctx.completed is False
        ctrl._process_chunk('{"data":{"node_type":"End"}}', ctx)
        assert ctx.completed is True

    @staticmethod
    def test_workflow_result_then_end():
        """先收到 workflow_result_node（ctx.execution_result），再收到 End → 终态 completed+result。"""
        ctrl = VersatileController("http://h/", workflow_result_node="GXZQAResponseNode")
        ctx = VersatileStreamCtx()
        ctrl._process_chunk(
            '{"data":{"node_type":"QA","node_name":"GXZQAResponseNode","text":"final"}}',
            ctx,
        )
        ctrl._process_chunk('{"data":{"node_type":"End"}}', ctx)
        events = ctrl._on_stream_end(ctx)
        assert events[0].execution_completed.result == "final"
