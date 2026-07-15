# coding: utf-8
# pylint: disable=no-self-use
# 说明：测试模块内的 mock 类方法不强制 staticmethod，便于继承与重写。
"""VersatileAdapterRunner.run_async 端到端流测试。

通过 mock httpx 注入 SSE 响应，验证 Runner 完整流程：
- 配置加载 → target 路由匹配 → adapter 创建 → dispatch_stream → 标准化 AdapterEvent 输出
- 一级控制器：data_proxy + execution_completed 终态
- 低码工作流：data_proxy + execution_completed 终态（无 End 节点时兜底 FAILED）
- 错误路径：404/422 HTTPStatusError 透传
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dispatcher.runner import VersatileAdapterRunner
from event.events import AdapterEvent


# ════════════════════════════════════════════════════════════════════
# 辅助：mock httpx SSE 流
# ════════════════════════════════════════════════════════════════════


class _MockResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self.status_code = status_code
        self._lines = lines
        self.is_error = status_code >= 400

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b"\n".join(line.encode("utf-8") for line in self._lines)

    def raise_for_status(self):
        if self.is_error:
            request = httpx.Request("POST", "http://mock/")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )


class _MockStream:
    def __init__(self, response: _MockResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _MockClient:
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self._status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @staticmethod
    def build_request(method, url, **kwargs):
        # 兼容 httpx 的 json= / headers= / params= 关键字调用；
        # 用 **kwargs 接收，避免形参遮蔽模块级 json 导入。
        return httpx.Request(
            method,
            url,
            json=kwargs.get("json"),
            headers=kwargs.get("headers"),
            params=kwargs.get("params"),
        )

    def stream(self, method, url, **kwargs):
        # 兼容 httpx 的 json= / headers= / params= 关键字调用；测试只需返回固定 mock 响应。
        del method, url, kwargs
        return _MockStream(_MockResponse(self._lines, self._status_code))


def _patch_httpx(lines: list[str], status_code: int = 200):
    return patch(
        "adapters.versatile_proxy.httpx.AsyncClient",
        return_value=_MockClient(lines, status_code),
    )


# ════════════════════════════════════════════════════════════════════
# 一级控制器：完整正常流（message → End → input_required → completed）
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_controller_complete_flow_with_end_node(write_yaml):
    """一级控制器：3 帧 message + 1 帧 End → 1 data_proxy×4 + completed/no_result。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    sse_lines = [
        'data: {"event":"message","data":{"node_type":"think","text":"开始思考"}}',
        'data: {"event":"message","data":{"node_type":"QA","node_name":"X","text":"分析"}}',
        'data: {"event":"message","data":{"node_type":"End","is_finished":true}}',
    ]

    with _patch_httpx(sse_lines):
        events = []
        async for ev in runner.run_async(
            target={"conversation_id": "c-1"},
            headers={},
            params={},
            body={"custom_data": {"inputs": {"query": "q"}}},
        ):
            events.append(ev)

    # 3 帧 data_proxy（含 End 节点的 data_proxy） + 0 个 execution_completed（无 workflow_result_node 命中）
    data_events = [e for e in events if e.data_proxy is not None]
    assert len(data_events) == 3
    # 无 workflow_result（GXZQAResponseNode 未出现）→ on_stream_end 不产 execution_completed
    completed = [e for e in events if e.execution_completed is not None]
    assert len(completed) == 0


@pytest.mark.asyncio
async def test_controller_flow_with_workflow_result_node(write_yaml):
    """一级控制器：GXZQAResponseNode 帧被吞掉，提取 text 作为 result，最后 completed。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    sse_lines = [
        'data: {"event":"message","data":{"node_type":"think","text":"thinking"}}',
        # GXZQAResponseNode：被吞掉
        'data: {"data":{"node_type":"QA","node_name":"GXZQAResponseNode","text":"最终答案"}}',
        # End 节点
        'data: {"event":"message","data":{"node_type":"End","is_finished":true}}',
    ]

    with _patch_httpx(sse_lines):
        events = []
        async for ev in runner.run_async(
            target={"conversation_id": "c-1"},
            headers={},
            params={},
            body={"custom_data": {}},
        ):
            events.append(ev)

    # think + End 两帧 data_proxy；GXZQAResponseNode 被吞
    data_events = [e for e in events if e.data_proxy is not None]
    assert len(data_events) == 2
    # 终态 completed 带 result
    completed = [e for e in events if e.execution_completed is not None]
    assert len(completed) == 1
    assert completed[0].execution_completed.result == "最终答案"
    assert completed[0].execution_completed.is_failed is False


@pytest.mark.asyncio
async def test_controller_flow_no_end_node_yields_input_required(write_yaml):
    """一级控制器：没收到 End 节点 → 流自然结束 → 产 execution_input_required。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    sse_lines = [
        'data: {"event":"message","data":{"text":"halfway"}}',
    ]

    with _patch_httpx(sse_lines):
        events = []
        async for ev in runner.run_async(
            target={"conversation_id": "c-1"},
            headers={},
            params={},
            body={"custom_data": {}},
        ):
            events.append(ev)

    irq = [e for e in events if e.execution_input_required is not None]
    assert len(irq) == 1


@pytest.mark.asyncio
async def test_controller_flow_exception_marks_failed(write_yaml):
    """一级控制器：event=exception 帧 → ctx.is_failed=True → completed with is_failed。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    sse_lines = [
        'data: {"event":"exception","data":{"message":"运行时错误"}}',
    ]
    # workflow_result_node 未命中，但 exception 会在流结束时产出 failed completed 事件
    with _patch_httpx(sse_lines):
        events = []
        async for ev in runner.run_async(
            target={"conversation_id": "c-1"},
            headers={},
            params={},
            body={"custom_data": {}},
        ):
            events.append(ev)

    # exception 帧本身作为 data_proxy 转发，流结束补 failed completed
    data_events = [e for e in events if e.data_proxy is not None]
    assert len(data_events) == 1
    assert '"event":"exception"' in data_events[0].data_proxy.raw_data
    completed = [e for e in events if e.execution_completed is not None]
    assert len(completed) == 1
    assert completed[0].execution_completed.is_failed is True
    assert completed[0].execution_completed.result == ""
    assert '"event":"exception"' in completed[0].execution_completed.error_message


# ════════════════════════════════════════════════════════════════════
# 低码工作流：按 intent 匹配 + _SKIP_TYPES 过滤
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_workflow_intent_matched_skips_filtered_types(write_yaml):
    """workflow adapter：按 intent 匹配；finish/runCompleted/dialogId 帧被吞。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    sse_lines = [
        'data: {"type":"rawData","data":{"content":"x"}}',
        'data: {"type":"finish","data":{"content":""}}',        # 跳过
        'data: {"type":"runCompleted","data":{"content":""}}',  # 跳过
        'data: {"type":"dialogId","data":{"content":"d-1"}}',   # 跳过
        'data: {"type":"text","data":{"content":"hello"}}',
    ]

    with _patch_httpx(sse_lines):
        events = []
        async for ev in runner.run_async(
            target={"intent": "knowledge_qa", "conversation_id": "c-2"},
            headers={},
            params={},
            body={"custom_data": {}},
        ):
            events.append(ev)

    # 5 帧输入 → 2 帧透传（rawData + text）
    data_events = [e for e in events if e.data_proxy is not None]
    assert len(data_events) == 2


@pytest.mark.asyncio
async def test_workflow_flow_with_workflow_result_node(write_yaml):
    """workflow adapter：命中自身 workflow_result_node 时提取结果并在 finish 后 completed。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    sse_lines = [
        'data: {"type":"text","data":{"content":"processing"}}',
        'data: {"data":{"node_type":"QA","node_name":"WorkflowQAResponseNode","text":"工作流答案"}}',
        'data: {"type":"finish","data":{"content":""}}',
    ]

    with _patch_httpx(sse_lines):
        events = []
        async for ev in runner.run_async(
            target={"intent": "knowledge_qa", "conversation_id": "c-2"},
            headers={},
            params={},
            body={"custom_data": {}},
        ):
            events.append(ev)

    data_events = [e for e in events if e.data_proxy is not None]
    assert len(data_events) == 1
    completed = [e for e in events if e.execution_completed is not None]
    assert len(completed) == 1
    assert completed[0].execution_completed.is_failed is False
    assert completed[0].execution_completed.result == "工作流答案"


@pytest.mark.asyncio
async def test_workflow_id_matched_uses_workflow_url(write_yaml):
    """workflow adapter：按 workflow_id 匹配；URL 应包含 wf_wealth。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    sse_lines = ['data: {"type":"text","data":{"content":"ok"}}']

    captured_urls = []

    class _CapturingClient(_MockClient):
        def stream(self, method, url, **kwargs):
            captured_urls.append(url)
            return super().stream(method, url, **kwargs)

    with patch(
        "adapters.versatile_proxy.httpx.AsyncClient",
        return_value=_CapturingClient(sse_lines),
    ):
        async for _ in runner.run_async(
            target={"workflow_id": "wf_wealth", "conversation_id": "c-3"},
            headers={},
            params={},
            body={"custom_data": {}},
        ):
            pass

    assert any("wf_wealth" in u and "c-3" in u for u in captured_urls)


# ════════════════════════════════════════════════════════════════════
# 路由兜底：target 不匹配 → 走 default_controller
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_unknown_target_falls_back_to_controller(write_yaml):
    """未知 intent → controller 兜底，URL 用 controller 模板。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    sse_lines = ['data: {"event":"message","data":{"node_type":"x"}}']

    captured_urls = []

    class _CapturingClient(_MockClient):
        def stream(self, method, url, **kwargs):
            captured_urls.append(url)
            return super().stream(method, url, **kwargs)

    with patch(
        "adapters.versatile_proxy.httpx.AsyncClient",
        return_value=_CapturingClient(sse_lines),
    ):
        async for _ in runner.run_async(
            target={"intent": "no-such-intent", "conversation_id": "c-fb"},
            headers={},
            params={},
            body={"custom_data": {}},
        ):
            pass

    # 路径用 controller URL 模板：/v1/agents/agent-a/conversations/{conv_id}
    assert any("agents/agent-a" in u for u in captured_urls)


# ════════════════════════════════════════════════════════════════════
# HTTP 错误路径
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_http_422_raises_httpstatus_error(write_yaml):
    runner = VersatileAdapterRunner(config_path=write_yaml())

    with _patch_httpx([], status_code=422):
        with pytest.raises(httpx.HTTPStatusError):
            async for _ in runner.run_async(
                target={"conversation_id": "c-1"},
                headers={},
                params={},
                body={"custom_data": {}},
            ):
                pass


@pytest.mark.asyncio
async def test_http_500_raises_httpstatus_error(write_yaml):
    runner = VersatileAdapterRunner(config_path=write_yaml())

    with _patch_httpx([], status_code=500):
        with pytest.raises(httpx.HTTPStatusError):
            async for _ in runner.run_async(
                target={"conversation_id": "c-1"},
                headers={},
                params={},
                body={"custom_data": {}},
            ):
                pass


# ════════════════════════════════════════════════════════════════════
# Header 白名单端到端：实际发出的 HTTP 头被白名单过滤
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_header_whitelist_enforced_end_to_end(write_yaml):
    """controller 的 forward_header_whitelist YAML 配置：x-user-id / cookie，其他 header 应被过滤。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    sse_lines = ['data: {"event":"message","data":{}}']

    captured_headers = []

    class _CapturingClient(_MockClient):
        def stream(self, method, url, headers=None, **kwargs):
            captured_headers.append(dict(headers or {}))
            return super().stream(method, url, headers=headers, **kwargs)

    with patch(
        "adapters.versatile_proxy.httpx.AsyncClient",
        return_value=_CapturingClient(sse_lines),
    ):
        async for _ in runner.run_async(
            target={"conversation_id": "c-h"},
            headers={
                "X-User-Id": "u-1",
                "Cookie": "AGENT_SID=abc",
                "X-Noise": "should-be-stripped",
            },
            params={},
            body={"custom_data": {}},
        ):
            pass

    assert captured_headers, "未捕获到任何请求头"
    h = captured_headers[0]
    # 白名单内 header 透传
    assert any(k.lower() == "x-user-id" and v == "u-1" for k, v in h.items())
    # 白名单外 header 被过滤
    assert "X-Noise" not in h and "x-noise" not in h
