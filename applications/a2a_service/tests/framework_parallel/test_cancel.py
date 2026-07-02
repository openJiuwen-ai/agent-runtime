# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""取消：cancel_task（主侧 API 级深度取消）+ cancel（子侧 A2A on_cancel_task）。

关联用例：CANCEL-01（主设令牌 + 对各子发 A2A CancelTask）、
CANCEL-07（best-effort：单个失败不阻断其余）、TECH §4.2（子侧 cancel 设令牌 + 写 CANCELED）、
_fetch_final_via_get（仅 COMPLETED 视为成功，否则 None）。
"""
# 单元测试以白盒方式直接验证 Executor 的内部实现（受保护成员），
# G.CLS.11（建议级）针对生产封装，不适用于此类白盒测试，故统一豁免。
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import TASK_STATE_CANCELED, TaskStatusUpdateEvent

from tests.framework_parallel._helpers import (
    FakeSubAgentClient,
    drain_queue,
    make_executor,
    task_completed_text,
)


# ── cancel_task：无 sub_agent_client → 仅设令牌 ─────────────────────────────


async def test_cancel_task_without_client_sets_token_only():
    executor = make_executor(sub_agent_client=None)
    await executor.cancel_task("conv-1")
    token = executor._test_remote_handler._cancel_tokens.get("conv-1")
    assert token is not None and token.is_set()


# ── cancel_task：有 client + 已登记子任务 → 对每个 child 发 A2A CancelTask ───


async def test_cancel_task_propagates_to_each_child():
    client = FakeSubAgentClient()
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler
    handler._cancel_tokens["conv-1"] = asyncio.Event()
    handler._child_task_ids["conv-1"] = {
        "A": {"task_id": "ta", "url": ""}, "B": {"task_id": "tb", "url": ""},
    }

    await executor.cancel_task("conv-1")

    assert handler._cancel_tokens["conv-1"].is_set()
    assert client.cancel_task.await_count == 2
    sent_ids = {call.args[0].id for call in client.cancel_task.await_args_list}
    assert sent_ids == {"ta", "tb"}


async def test_cancel_task_best_effort_continues_on_error():
    client = FakeSubAgentClient()
    client.cancel_task = AsyncMock(side_effect=[RuntimeError("child A 已终态"), None])
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler
    handler._cancel_tokens["conv-1"] = asyncio.Event()
    handler._child_task_ids["conv-1"] = {
        "A": {"task_id": "ta", "url": ""}, "B": {"task_id": "tb", "url": ""},
    }

    await executor.cancel_task("conv-1")  # 不应抛出

    assert client.cancel_task.await_count == 2  # 第一个失败仍尝试第二个


# ── cancel（子侧）：设令牌 + 写 CANCELED 终态事件 ───────────────────────────


async def test_cancel_sets_token_and_enqueues_canceled():
    executor = make_executor()
    token = asyncio.Event()
    executor._test_remote_handler._cancel_tokens["sub-conv"] = token
    context = SimpleNamespace(context_id="sub-conv", task_id="task-1", call_context=None)
    eq = EventQueue()

    await executor.cancel(context, eq)

    assert token.is_set()
    status_events = [e for e in drain_queue(eq) if isinstance(e, TaskStatusUpdateEvent)]
    assert len(status_events) == 1
    assert status_events[0].status.state == TASK_STATE_CANCELED


async def test_cancel_without_registered_token_still_enqueues_canceled():
    executor = make_executor()
    context = SimpleNamespace(context_id="unknown", task_id="t", call_context=None)
    eq = EventQueue()
    await executor.cancel(context, eq)  # 无令牌也不报错
    status_events = [e for e in drain_queue(eq) if isinstance(e, TaskStatusUpdateEvent)]
    assert status_events[0].status.state == TASK_STATE_CANCELED


# ── _fetch_final_via_get：仅 COMPLETED 视为成功 ─────────────────────────────


async def test_fetch_final_via_get_completed_returns_content():
    client = FakeSubAgentClient(get_task=task_completed_text("child-1", "最终回答"))
    executor = make_executor(sub_agent_client=client)
    final = await executor._fetch_final_via_get("child-1", client)
    assert final == {"content": "最终回答"}


async def test_fetch_final_via_get_none_when_no_client():
    executor = make_executor(sub_agent_client=None)
    assert await executor._fetch_final_via_get("child-1", None) is None


async def test_fetch_final_via_get_none_when_empty_id():
    client = FakeSubAgentClient(get_task=task_completed_text("child-1", "x"))
    executor = make_executor(sub_agent_client=client)
    assert await executor._fetch_final_via_get("", client) is None
