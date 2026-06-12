# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""_drive_sub_agent：StreamResponse 类型契约 + 断线重连 + tasks/get 回退。

关联用例：CTX-07 / TECH §3.2 实现注 1（WhichOneof 而非 isinstance）、
RECONN-01（抖动重连续接 live 尾流）、RECONN-03（断连期间已完成→tasks/get，不误判 failed）、
RECONN-05（首帧前断线→failed）。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from a2a.types.a2a_pb2 import StreamResponse, Task
from a2a.utils.errors import UnsupportedOperationError

import orchestrator.executor as EXE
from common.events import SubAgentSpec

from tests.framework_parallel._helpers import (
    FakeAsyncStream,
    FakeSubAgentClient,
    make_executor,
    make_turn_ctx,
    sr_artifact,
    sr_completed_text,
    sr_task,
    task_completed_text,
)

_SPEC = SubAgentSpec(entity_id="A", entity_name="企业A", query="分析A")


async def _drive(executor, *, child_path=("A",)):
    ctx = make_turn_ctx()
    frames = []
    async for f in executor._drive_sub_agent(
        _SPEC, "c:sub:A", child_path, ctx, asyncio.Event()
    ):
        frames.append(f)
    return frames


# ── CTX-07：类型契约（WhichOneof 判别，不依赖 isinstance）────────────────────


async def test_stream_dispatch_by_whichoneof_happy_path():
    client = FakeSubAgentClient(send=[FakeAsyncStream([
        sr_task("child-1"),
        sr_artifact({"type": "think_chunk", "content": "正在分析A"}),
        sr_completed_text("实体A最终回答"),
    ])])
    executor = make_executor(sub_agent_client=client)

    frames = await _drive(executor)

    assert frames[0] == {"type": "__task_created__", "task_id": "child-1"}
    assert frames[1] == {"type": "think_chunk", "content": "正在分析A"}
    assert frames[2] == {"type": "__completed__", "content": "实体A最终回答"}


def test_streamresponse_is_not_task_instance():
    """文档化 CTX-07：StreamResponse 对 Task 恒 isinstance False，必须靠 WhichOneof。"""
    sr = StreamResponse(task=Task(id="x"))
    assert sr.WhichOneof("payload") == "task"
    assert not isinstance(sr, Task)


# ── RECONN-03：断连期间已完成 → resubscribe 抛错 → tasks/get 回退 ────────────


async def test_resubscribe_unsupported_falls_back_to_tasks_get():
    # 首帧拿到 task_id 后流抛 UnsupportedOperationError（终态/队列已关）
    client = FakeSubAgentClient(
        send=[FakeAsyncStream([sr_task("child-1")], exc=UnsupportedOperationError())],
        get_task=task_completed_text("child-1", "断连期间已跑完"),
    )
    executor = make_executor(sub_agent_client=client)

    frames = await _drive(executor)

    assert {"type": "__task_created__", "task_id": "child-1"} in frames
    assert frames[-1] == {"type": "__completed__", "content": "断连期间已跑完"}
    assert client.get_task_calls == 1  # 走了 tasks/get 回退


async def test_resubscribe_unsupported_and_tasks_get_empty_reraises():
    client = FakeSubAgentClient(
        send=[FakeAsyncStream([sr_task("child-1")], exc=UnsupportedOperationError())],
        get_task=None,  # tasks/get 也拿不到 → 上层转 failed
    )
    executor = make_executor(sub_agent_client=client)
    with pytest.raises(UnsupportedOperationError):
        await _drive(executor)


# ── RECONN-01：网络抖动 → subscribe 重连，续接 live 尾流 ─────────────────────


async def test_network_blip_reconnects_via_subscribe(monkeypatch):
    monkeypatch.setattr(EXE.asyncio, "sleep", _no_sleep)  # 跳过指数退避真实等待
    client = FakeSubAgentClient(
        send=[FakeAsyncStream([sr_task("child-1")], exc=httpx.ReadError("blip"))],
        subscribe=[FakeAsyncStream([
            sr_task("child-1"),  # resubscribe 首帧快照：child_task_id 已知 → 不重复 yield
            sr_artifact({"type": "think_chunk", "content": "续接尾流"}),
            sr_completed_text("最终回答"),
        ])],
    )
    executor = make_executor(sub_agent_client=client)

    frames = await _drive(executor)

    assert client.subscribe_calls == 1
    assert frames[0] == {"type": "__task_created__", "task_id": "child-1"}
    assert frames[1] == {"type": "think_chunk", "content": "续接尾流"}
    assert frames[-1] == {"type": "__completed__", "content": "最终回答"}
    # 无重复 __task_created__（resubscribe 快照帧不重复捕获）
    assert sum(1 for f in frames if f.get("type") == "__task_created__") == 1


# ── RECONN-05：首帧（task）前断线 → 无 task_id 可重连 → 抛错（failed 兜底）───


async def test_blip_before_first_frame_reraises():
    client = FakeSubAgentClient(
        send=[FakeAsyncStream([], exc=httpx.RemoteProtocolError("early"))],
    )
    executor = make_executor(sub_agent_client=client)
    with pytest.raises(httpx.RemoteProtocolError):
        await _drive(executor)


async def _no_sleep(*_a, **_k):
    return None
