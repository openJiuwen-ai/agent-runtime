# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileAdapterExecutor — 无 LLM 的 A2A 执行器（a2a-sdk 1.0.0-alpha.1）。

实现 A2A SDK 的 AgentExecutor 接口：
  - execute(context, event_queue): 接收任务 → 调用 VersatileProxy → 推送流式事件
  - cancel(context, event_queue): 取消任务

纯透传层：不做 end 节点判断，不做状态管理，不存任何上下文。
"""
from __future__ import annotations

import uuid

from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value as ProtoValue
from typing_extensions import override

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import Part, Role

from loguru import logger

from adapter.versatile_proxy import VersatileProxy


class VersatileAdapterExecutor(AgentExecutor):  # noqa: F821
    """VersatileAdapter A2A 执行器。"""

    def __init__(
        self,
        versatile_proxy: VersatileProxy,
    ) -> None:
        self._proxy = versatile_proxy

    @override
    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task_id = context.task_id
        conv_id = context.context_id
        trace_id = (context.task_id or uuid.uuid4().hex)

        with logger.contextualize(trace_id=trace_id, task_id=task_id, conv_id=conv_id):

            logger.info(
                f"[VersatileAdapter] execute：conv_id={conv_id}, task_id={task_id}"
            )
    
            # 先发送 Task 事件（a2a-sdk 1.0.0 要求）
            # 注：wyt 在 backup_enhancement 分支删除了这段，原因未说明；
            # 已记录在 docs/issue-wyt-merge-decisions.md V-1 待核实 a2a-sdk 版本与协议要求。
            from a2a.types.a2a_pb2 import Task, TaskStatus, TaskState
            user_message = context.message
            if task_id and conv_id and user_message:
                await event_queue.enqueue_event(
                    Task(
                        id=task_id,
                        context_id=conv_id,
                        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                        history=[user_message],
                    )
                )
    
            updater = TaskUpdater(event_queue, task_id, conv_id)
    
            # ── 直接使用传过来的请求头和请求体 ────────────────────────────────
            input_data = self._build_first_input(context.message)
            versatile_input = input_data.get("body", {})
            headers = input_data.get("headers", {})
            params = input_data.get("params", {})
            logger.info(f"[VersatileAdapter] 接收请求：conv_id={conv_id}, task_id={task_id}")
            logger.debug(f"[VersatileAdapter] 请求头：{headers}")
            logger.debug(f"[VersatileAdapter] 请求体：{versatile_input}")
            logger.debug(f"[VersatileAdapter] 请求参数：{params}")
    
            await updater.start_work()
    
            # ── 纯透传调用 VersatileProxy，流式推送 ─────────────────────────────
            # 用"前一个 chunk"模式：延迟一次，确保最后一个 chunk 以 last_chunk=True 发送且不重复
            prev_part: Part | None = None
    
            try:
                async for chunk in self._proxy.dispatch_stream(
                    versatile_input, conv_id, headers, params
                ):
                    if prev_part is not None:
                        await updater.add_artifact(
                            parts=[prev_part],
                            artifact_id=str(uuid.uuid4()),
                            last_chunk=False,
                        )
    
                    data_part = Part()
                    proto_value = ProtoValue()
                    ParseDict(chunk, proto_value.struct_value)
                    data_part.data.CopyFrom(proto_value)
                    prev_part = data_part
    
                # ── 流结束：将最后一个 chunk 以 last_chunk=True 发出 ─────────────────
                if prev_part is not None:
                    await updater.add_artifact(
                        parts=[prev_part],
                        artifact_id=str(uuid.uuid4()),
                        last_chunk=True,
                    )
                else:
                    text_part = Part()
                    text_part.text = "流结束"
                    await updater.add_artifact(
                        parts=[text_part],
                        artifact_id=str(uuid.uuid4()),
                        last_chunk=True,
                    )
    
                logger.info(
                    f"[VersatileAdapter] 流结束：conv_id={conv_id}, task_id={task_id}"
                )
            except Exception as e:
                logger.exception(
                    f"[VersatileAdapter] proxy 流异常：conv_id={conv_id}, task_id={task_id}"
                )
                await updater.failed(message=str(e))
                return

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        conv_id = context.context_id
        task_id = context.task_id

        updater = TaskUpdater(event_queue, task_id, conv_id)
        await updater.cancel()
        logger.info(
            f"[VersatileAdapter] 任务已取消：conv_id={conv_id}, task_id={task_id}"
        )

    def _build_first_input(self, message) -> dict:
        for part in message.parts:
            if part.WhichOneof("content") == "data":
                data = MessageToDict(part.data)
                if isinstance(data, dict) and data:
                    return data
        text = ""
        for part in message.parts:
            if part.WhichOneof("content") == "text" and part.text:
                text = part.text
                break
        if not text:
            logger.warning(
                "[VersatileAdapter] message 中未提取到 data/text part，使用空查询兜底"
            )
        return {"body": {"input": {"query": text}}, "headers": {}, "params": {}}
