# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""自闭环测试工具：协议对象构造 / 假客户端 / Executor 工厂 / EventQueue 解码。

本模块刻意**不依赖** ``agents/`` 下的真实 Agent，也不依赖任何已上库的测试桩
（MainAgent / SubAgent / StubVersatileProxy / tests 级 conftest）。为此在导入
``orchestrator.executor`` 之前，自行向 ``sys.modules`` 注入一个最小的
``agents.EDPAgent.agent_stream`` 占位，仅用于打通模块 import 链；所有被测路径
要么完全绕过 agent_stream，要么由各用例自行 monkeypatch 受控替身。
"""
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import sys
import types
from typing import Any, AsyncIterator, Optional
from unittest.mock import AsyncMock, MagicMock

# ── 自注入 agents.EDPAgent 占位（须早于 executor 导入）──────────────────────
if "agents.EDPAgent" not in sys.modules:
    _pkg = sys.modules.get("agents") or types.ModuleType("agents")
    sys.modules.setdefault("agents", _pkg)
    _edp = types.ModuleType("agents.EDPAgent")

    async def _agent_stream_placeholder(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        if False:  # pragma: no cover - 仅为使其成为 async generator
            yield
        return

    _edp.agent_stream = _agent_stream_placeholder  # type: ignore[attr-defined]
    sys.modules["agents.EDPAgent"] = _edp
    setattr(_pkg, "EDPAgent", _edp)

# ── 协议 / 框架导入（占位注入之后）──────────────────────────────────────────
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    ROLE_AGENT,
    TASK_STATE_CANCELED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_WORKING,
)
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct, Value

from orchestrator.executor import Executor, _TurnContext
from orchestrator.handlers.remote_agent_handler import RemoteAgentHandler
from orchestrator.handlers.requester_handler import RequesterHandler
from orchestrator.route import RouteDispatcher
from orchestrator.state import TaskStateManager


# ════════════════════════════════════════════════════════════════════
# protobuf 构造
# ════════════════════════════════════════════════════════════════════


def data_part(payload: dict) -> Part:
    """构造一个 DataPart（与 channels.dict_to_a2a 同构）。"""
    struct = Struct()
    struct.update(payload)
    value = Value()
    value.struct_value.CopyFrom(struct)
    part = Part()
    part.data.CopyFrom(value)
    return part


def artifact_event(frame: dict, *, task_id: str = "t", conv_id: str = "c") -> TaskArtifactUpdateEvent:
    """把一个原始帧 dict 包成 TaskArtifactUpdateEvent（单 DataPart）。"""
    art = Artifact(artifact_id="art-1", parts=[data_part(frame)])
    return TaskArtifactUpdateEvent(
        task_id=task_id, context_id=conv_id, artifact=art, last_chunk=False
    )


def status_completed_text(text: str, *, task_id: str = "t", conv_id: str = "c") -> TaskStatusUpdateEvent:
    msg = Message(role=ROLE_AGENT, message_id="m", parts=[Part(text=text)])
    return TaskStatusUpdateEvent(
        task_id=task_id, context_id=conv_id,
        status=TaskStatus(state=TASK_STATE_COMPLETED, message=msg),
    )


def status_completed_data(payload: dict, *, task_id: str = "t", conv_id: str = "c") -> TaskStatusUpdateEvent:
    msg = Message(role=ROLE_AGENT, message_id="m", parts=[data_part(payload)])
    return TaskStatusUpdateEvent(
        task_id=task_id, context_id=conv_id,
        status=TaskStatus(state=TASK_STATE_COMPLETED, message=msg),
    )


def status_completed_empty(*, task_id: str = "t", conv_id: str = "c") -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id, context_id=conv_id,
        status=TaskStatus(state=TASK_STATE_COMPLETED),
    )


def task_completed_text(task_id: str, text: str, *, conv_id: str = "c") -> Task:
    """COMPLETED 终态 Task（供 tasks/get 回退路径使用）。"""
    msg = Message(role=ROLE_AGENT, message_id="m", parts=[Part(text=text)])
    return Task(
        id=task_id, context_id=conv_id,
        status=TaskStatus(state=TASK_STATE_COMPLETED, message=msg),
    )


# ── StreamResponse（A2A client 产出的帧，payload oneof）──────────────────────


def sr_task(task_id: str, *, conv_id: str = "c") -> StreamResponse:
    return StreamResponse(
        task=Task(id=task_id, context_id=conv_id, status=TaskStatus(state=TASK_STATE_WORKING))
    )


def sr_artifact(frame: dict) -> StreamResponse:
    return StreamResponse(artifact_update=artifact_event(frame))


def sr_completed_text(text: str) -> StreamResponse:
    return StreamResponse(status_update=status_completed_text(text))


def sr_failed(text: str = "") -> StreamResponse:
    """子 Agent FAILED 终态帧（error 文本走 status.message）。"""
    parts = [Part(text=text)] if text else []
    msg = Message(role=ROLE_AGENT, message_id="m", parts=parts) if parts else None
    su = TaskStatusUpdateEvent(
        task_id="t", context_id="c",
        status=TaskStatus(state=TASK_STATE_FAILED, message=msg) if msg
        else TaskStatus(state=TASK_STATE_FAILED),
    )
    return StreamResponse(status_update=su)


# ════════════════════════════════════════════════════════════════════
# 假异步流 / 假子 Agent 客户端
# ════════════════════════════════════════════════════════════════════


class FakeAsyncStream:
    """模拟 A2A client.send_message/subscribe 返回的异步流。

    先按序产出 ``items``，全部产出后若设置了 ``exc`` 则抛出（模拟中途断连/终态）。
    支持 ``aclose()``（VA 取消路径会调用）。
    """

    def __init__(self, items: list, *, exc: Optional[BaseException] = None) -> None:
        self._items = list(items)
        self._exc = exc
        self.closed = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for it in self._items:
            yield it
        if self._exc is not None:
            raise self._exc

    async def aclose(self) -> None:
        self.closed = True


class FakeSubAgentClient:
    """最小子 Agent A2A 客户端替身。

    - ``send``/``subscribe``：按调用次序返回预置流（耗尽后复用最后一个）。
    - ``get_task``：tasks/get 回退返回值（Task 或 None）。
    - ``cancel_task``：AsyncMock，便于断言深度取消调用。
    """

    def __init__(
        self,
        *,
        send: Optional[list] = None,
        subscribe: Optional[list] = None,
        get_task: Any = None,
    ) -> None:
        self._send = list(send or [])
        self._subscribe = list(subscribe or [])
        self._get_task = get_task
        self.send_calls = 0
        self.subscribe_calls = 0
        self.get_task_calls = 0
        self.send_requests = []
        self.subscribe_requests = []
        self.cancel_task = AsyncMock()

    def send_message(self, request, context=None):
        self.send_requests.append(request)
        s = self._send[min(self.send_calls, len(self._send) - 1)]
        self.send_calls += 1
        return s() if callable(s) else s

    def subscribe(self, request, context=None):
        self.subscribe_requests.append(request)
        s = self._subscribe[min(self.subscribe_calls, len(self._subscribe) - 1)]
        self.subscribe_calls += 1
        return s() if callable(s) else s

    async def get_task(self, _request):
        self.get_task_calls += 1
        return self._get_task() if callable(self._get_task) else self._get_task


# ════════════════════════════════════════════════════════════════════
# Executor 工厂 / TurnContext
# ════════════════════════════════════════════════════════════════════


def make_executor(*, sub_agent_client: Any = None, redis_json: Any = None, **limits) -> Executor:
    """构造一个 Executor，外部依赖全部用 Mock 填充。

    ``redis_json``：``redis.get_json`` 的统一返回（默认 ``{}``）。
    ``limits``：覆盖 max_concurrent_sub_agents / sub_agent_timeout_seconds /
    max_parallel_workflows_per_agent / workflow_timeout_seconds / max_call_depth。
    """
    va_client = MagicMock()
    redis = MagicMock()
    redis.get = AsyncMock(return_value="")
    redis.get_json = AsyncMock(return_value=({} if redis_json is None else redis_json))
    redis.set_json = AsyncMock()
    task_store = MagicMock()
    task_store.get = AsyncMock(return_value=None)
    task_store.save = AsyncMock()

    cfg = dict(
        max_concurrent_sub_agents=3,
        sub_agent_timeout_seconds=1800,
        max_parallel_workflows_per_agent=3,
        workflow_timeout_seconds=900,
        max_call_depth=3,
    )
    cfg.update(limits)
    state_manager = TaskStateManager(task_store=task_store)
    dispatcher = RouteDispatcher(state_manager)
    remote_handler = RemoteAgentHandler(
        va_client=va_client,
        redis=redis,
        state_manager=state_manager,
        client_factory=None,
        **cfg,
    )
    if sub_agent_client is not None:
        # mock _get_sub_agent_client 直接返回传入的 client（绕过 cache_key 格式）
        _mock_client = sub_agent_client
        async def _mock_get_client(url, agent_card_name="SubDPA"):
            return _mock_client
        remote_handler._get_sub_agent_client = _mock_get_client
        # 同时存 "" key 保持兼容（部分测试直接读 _sub_agent_clients[""]）
        remote_handler._sub_agent_clients[""] = sub_agent_client
    executor = Executor(
        redis=redis,
        route_dispatcher=dispatcher,
        state_manager=state_manager,
    )
    dispatcher.register_handler("requester", RequesterHandler(state_manager).handle)
    dispatcher.register_handler("remote_agent", remote_handler.handle)
    executor._remote_handler = remote_handler
    executor._test_remote_handler = remote_handler
    executor._test_state_manager = state_manager
    executor._test_task_store = task_store
    executor._test_va_client = va_client
    executor._fetch_final_via_get = remote_handler._fetch_final_via_get
    return executor


def make_turn_ctx(*, conv_id: str = "c", task_id: str = "t", sub_task_path: tuple = ()) -> _TurnContext:
    return _TurnContext(
        conv_id=conv_id,
        task_id=task_id,
        call_context=MagicMock(),
        event_queue=EventQueue(),
        sub_task_path=tuple(str(p) for p in sub_task_path),
    )


# ════════════════════════════════════════════════════════════════════
# EventQueue 解码
# ════════════════════════════════════════════════════════════════════


def drain_queue(event_queue: EventQueue) -> list:
    """非破坏地把 enqueue 过的事件全部取出（不消费顺序信号）。"""
    inner = getattr(event_queue, "_queue", None) or getattr(event_queue, "queue", None)
    out: list = []
    if inner is None:
        return out
    try:
        while True:
            out.append(inner.get_nowait())
    except asyncio.QueueEmpty:
        return out
    return out


def artifact_data(event) -> Optional[dict]:
    """从 TaskArtifactUpdateEvent 取出 DataPart 还原的 dict。"""
    artifact = getattr(event, "artifact", None)
    if artifact is None:
        return None
    for part in artifact.parts:
        if part.WhichOneof("content") == "data":
            d = MessageToDict(part.data)
            return d if isinstance(d, dict) else None
    return None


def collect_sub_tasks(event_queue: EventQueue) -> list[dict]:
    """抽出队列里全部 SubTaskEvent 信封（data.type == "sub_task"）。"""
    res: list[dict] = []
    for ev in drain_queue(event_queue):
        if isinstance(ev, TaskArtifactUpdateEvent):
            d = artifact_data(ev)
            if d and d.get("type") == "sub_task":
                res.append(d)
    return res


def find_status_events(event_queue: EventQueue):
    return [ev for ev in drain_queue(event_queue) if isinstance(ev, TaskStatusUpdateEvent)]
