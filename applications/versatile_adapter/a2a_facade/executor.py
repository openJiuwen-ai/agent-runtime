# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
A2aVersatileExecutor — A2A 薄壳适配层。

纯 A2A 协议适配：不包含业务逻辑。
- 从 A2A RequestContext 解析 input_data（target/body/headers/params）
- 调用 VersatileAdapterRunner.run_async() 获取 AdapterEvent
- 基于 AdapterEvent 类型做 A2A 协议映射
"""
from __future__ import annotations

import uuid

from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf import struct_pb2
from typing_extensions import override

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.helpers import (
    new_message
)
from a2a.types.a2a_pb2 import (
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
    ROLE_AGENT,
)
from loguru import logger

from dispatcher.runner import VersatileAdapterRunner


class A2aVersatileExecutor(AgentExecutor):
    """A2A 协议薄壳：AdapterEvent → A2A Artifact/Status 映射。"""

    def __init__(self, runner: VersatileAdapterRunner) -> None:
        self._runner = runner

    async def _setup_task(
        self, context: RequestContext, event_queue: EventQueue
    ) -> TaskUpdater:
        if context.current_task is None:
            task = Task(id=context.task_id, context_id=context.context_id,
                        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED), history=[context.message])
            await event_queue.enqueue_event(task)
        else:
            task = context.current_task
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        return updater

    @override
    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        if not context.message or not context.context_id or not context.task_id:
            return

        input_data = self._build_first_input(context.message)
        log_kw = self._extract_logging_context(input_data, context.context_id)
        runner_kw = self._extract_runner_kwargs(input_data, context.context_id)

        with logger.contextualize(**log_kw):
            logger.info(f"[A2aVA] execute: conv_id={context.context_id}, task_id={context.task_id}")

            updater = await self._setup_task(context, event_queue)
            message = None
            failed_message = None
            is_failed = False

            try:
                async for event in self._runner.run_async(**runner_kw):
                    if event.data_proxy is not None:
                        part = self._make_text_part(event.data_proxy.raw_data, "data_proxy")
                        await updater.add_artifact(parts=[part])

                    elif event.execution_input_required is not None:
                        await updater.requires_input()
                        return

                    elif event.execution_completed is not None:
                        is_failed = event.execution_completed.is_failed
                        if event.execution_completed.result:
                            part = self._make_text_part(event.execution_completed.result, "workflow_result")
                            message = new_message(parts=[part])
                        if is_failed and event.execution_completed.error_message:
                            err_part = self._make_text_part(
                                event.execution_completed.error_message, "upstream_error"
                            )
                            failed_message = new_message(parts=[err_part])

                if is_failed:
                    await updater.failed(failed_message)
                    logger.info(f"[A2aVA] 流异常结束(failed): conv_id={context.context_id}, task_id={context.task_id}")
                else:
                    await updater.complete(message)
                    logger.info(f"[A2aVA] 流结束: conv_id={context.context_id}, task_id={context.task_id}")

            except Exception as exc:
                logger.exception(f"[A2aVA] 流异常: conv_id={context.context_id}, task_id={context.task_id}")
                err_part = self._make_text_part(str(exc), "upstream_error")
                failed_message = new_message(parts=[err_part])
                await updater.failed(failed_message)
                return

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        conv_id = context.context_id
        task_id = context.task_id
        input_data = self._build_first_input(context.message)
        agent_id = input_data.get("agent_id", "")

        updater = TaskUpdater(event_queue, task_id, conv_id)
        await updater.cancel()
        logger.info(f"[A2aVA] 任务已取消: conv_id={conv_id}, task_id={task_id}, agent_id={agent_id}")

    def _make_text_part(
        self, text: str, vatype: str | None = None, media_type: str | None = None,
    ) -> Part:
        """构造 text Part，可选附带 vatype metadata。"""
        metadata = {"vatype": vatype} if vatype else None
        part = Part(text=text, media_type=media_type or '', metadata=metadata)
        return part

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
            logger.warning("[A2aVA] message 中未提取到 data/text part，使用空查询兜底")
        return {"body": {"input": {"query": text}}, "headers": {}, "params": {}}

    @staticmethod
    def _extract_logging_context(input_data: dict, conv_id: str) -> dict:
        return {
            "trace_id": input_data.get("trace_id", ""),
            "agent_id": input_data.get("agent_id", ""),
            "conv_id": conv_id,
        }

    @staticmethod
    def _extract_runner_kwargs(input_data: dict, conv_id: str) -> dict:
        target = input_data.get("target", {})
        if "conversation_id" not in target and conv_id:
            target = {**target, "conversation_id": conv_id}
        return {
            "target": target,
            "body": input_data.get("body", {}),
            "headers": input_data.get("headers", {}),
            "params": input_data.get("params", {}),
        }
