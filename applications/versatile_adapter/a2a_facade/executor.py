# coding: utf-8
from __future__ import annotations

import json
import uuid

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict
from typing_extensions import override

from a2a.helpers import new_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
)
from loguru import logger

from dispatcher.runner import VersatileAdapterRunner


def _data_part(data: dict) -> Part:
    part = Part()
    part.data.CopyFrom(ParseDict(data, struct_pb2.Value()))
    return part


def _struct(data: dict) -> struct_pb2.Struct:
    return ParseDict(data, struct_pb2.Struct())


class A2aVersatileExecutor(AgentExecutor):
    def __init__(self, runner: VersatileAdapterRunner) -> None:
        self._runner = runner

    async def _setup_task(
        self, context: RequestContext, event_queue: EventQueue
    ) -> TaskUpdater:
        if context.current_task is None:
            task = Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message],
            )
            await event_queue.enqueue_event(task)
        else:
            task = context.current_task
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        return updater

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if not context.message or not context.context_id or not context.task_id:
            return

        input_data = self._build_first_input(context.message)
        log_kw = self._extract_logging_context(input_data, context.context_id)
        runner_kw = self._extract_runner_kwargs(input_data, context.context_id)

        with logger.contextualize(**log_kw):
            logger.info(
                f"[A2aVA] execute: conv_id={context.context_id}, task_id={context.task_id}"
            )
            updater = await self._setup_task(context, event_queue)
            terminal_sent = False

            try:
                async for event in self._runner.run_async(**runner_kw):
                    if event.data_proxy is not None:
                        if terminal_sent:
                            logger.warning(
                                "[A2aVA] drop display frame after terminal: task_id={}",
                                context.task_id,
                            )
                            continue
                        await self._emit_proxy_artifact(
                            event_queue,
                            task_id=context.task_id,
                            context_id=context.context_id,
                            chunk=self._parse_proxy_payload(event.data_proxy.raw_data),
                        )
                        continue

                    if event.execution_input_required is not None:
                        if not terminal_sent:
                            await self._emit_input_required(
                                event_queue,
                                task_id=context.task_id,
                                context_id=context.context_id,
                                text="等待用户输入",
                            )
                            terminal_sent = True
                        return

                    if event.execution_completed is not None:
                        if terminal_sent:
                            continue
                        terminal_sent = True
                        completed = event.execution_completed
                        if completed.is_failed:
                            await self._emit_failed(
                                event_queue,
                                task_id=context.task_id,
                                context_id=context.context_id,
                                text=str(completed.error_message or completed.result or "执行报错（无详细信息）"),
                            )
                        else:
                            await self._emit_completed(
                                event_queue,
                                task_id=context.task_id,
                                context_id=context.context_id,
                                result=completed.result,
                            )

                if not terminal_sent:
                    await self._emit_completed(
                        event_queue,
                        task_id=context.task_id,
                        context_id=context.context_id,
                        result=None,
                    )

            except Exception:
                logger.exception(
                    f"[A2aVA] stream exception: conv_id={context.context_id}, task_id={context.task_id}"
                )
                if not terminal_sent:
                    await self._emit_failed(
                        event_queue,
                        task_id=context.task_id,
                        context_id=context.context_id,
                        text="执行报错（无详细信息）",
                    )

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        conv_id = context.context_id
        task_id = context.task_id
        input_data = self._build_first_input(context.message)
        agent_id = input_data.get("agent_id", "")

        updater = TaskUpdater(event_queue, task_id, conv_id)
        await updater.cancel()
        logger.info(
            f"[A2aVA] cancel: conv_id={conv_id}, task_id={task_id}, agent_id={agent_id}"
        )

    async def _emit_proxy_artifact(
        self,
        event_queue: EventQueue,
        *,
        task_id: str,
        context_id: str,
        chunk: dict,
    ) -> None:
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=Artifact(
                    artifact_id=str(uuid.uuid4()),
                    parts=[_data_part(chunk)],
                    metadata=_struct({"type": "versatile_proxy"}),
                ),
                last_chunk=False,
            )
        )

    async def _emit_completed(
        self,
        event_queue: EventQueue,
        *,
        task_id: str,
        context_id: str,
        result,
    ) -> None:
        message = None
        if result:
            message = new_message(parts=[self._make_text_part(str(result), "workflow_result")])
        status_event = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TASK_STATE_COMPLETED, message=message),
        )
        status_event.metadata.CopyFrom(
            _struct({"cascade_result": self._cascade_result(result)})
        )
        await event_queue.enqueue_event(status_event)

    async def _emit_failed(
        self,
        event_queue: EventQueue,
        *,
        task_id: str,
        context_id: str,
        text: str,
    ) -> None:
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TASK_STATE_FAILED,
                    message=new_message(parts=[self._make_text_part(text or "执行报错（无详细信息）", "upstream_error")]),
                ),
            )
        )
        logger.warning(
            "A2A_WARNING:VA_TERMINAL_FALLBACK state=FAILED task_id={} context_id={} text={}",
            task_id,
            context_id,
            text,
        )

    async def _emit_input_required(
        self,
        event_queue: EventQueue,
        *,
        task_id: str,
        context_id: str,
        text: str,
    ) -> None:
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TASK_STATE_INPUT_REQUIRED,
                    message=new_message(parts=[self._make_text_part(text or "等待用户输入")]),
                ),
            )
        )
        logger.info(
            "A2A_INFO:VA_INPUT_REQUIRED state=INPUT_REQUIRED task_id={} context_id={} text={}",
            task_id,
            context_id,
            text,
        )

    @staticmethod
    def _cascade_result(result) -> dict:
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                return parsed if isinstance(parsed, dict) else {"workflow_result": result}
            except Exception:
                return {"workflow_result": result}
        return {"workflow_result": result}

    @staticmethod
    def _parse_proxy_payload(raw_data) -> dict:
        if isinstance(raw_data, dict):
            return raw_data
        if isinstance(raw_data, str):
            try:
                parsed = json.loads(raw_data)
                return parsed if isinstance(parsed, dict) else {"event": "message", "data": {"text": raw_data}}
            except Exception:
                return {"event": "message", "data": {"text": raw_data}}
        return {"event": "message", "data": {"value": raw_data}}

    def _make_text_part(
        self,
        text: str,
        vatype: str | None = None,
        media_type: str | None = None,
    ) -> Part:
        metadata = {"vatype": vatype} if vatype else None
        return Part(text=text, media_type=media_type or "", metadata=metadata)

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
            logger.warning("[A2aVA] message has no data/text part, using empty query")
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
