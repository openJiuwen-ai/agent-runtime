"""
user_router — 用户入口路由（Versatile 平台定制格式）。

暴露端点（URL 和参数格式与 Versatile 平台约定保持不变）：
  POST /v1/{project_id}/agents/{agent_id}/conversations/{conversation_id}

请求体（JSON）：
  {
    "agent_id": "...",
    "input": {"query": "用户自然语言输入"},
    "conversation_id": "会话唯一ID",
    "stream": true,
    "custom_data": {"inputs": {"query": "..."}, ...}
  }

响应：
  - stream=true  → SSE（text/event-stream），data: <A2A 事件 JSON>，data: [DONE]
  - stream=false → JSON，返回最终回答文本

Task 管理：
  首轮：从 Redis 查询 session:{conv_id}:a2a_task_id；不存在则新建 task_id 并写入 Redis。
  续轮：从 Redis 取 task_id → TaskStore 读取 Task → 注入 RequestContext。
  Executor 通过 context.current_task.status.state 判断是否为 INPUT_REQUIRED 续轮。
"""
from __future__ import annotations

import asyncio
import json
import uuid

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Message,
    SendMessageRequest,
    TaskStatus,
    TaskStatusUpdateEvent,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
)
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct, Value
from loguru import logger

from common.constants import session_request_key
from orchestrator.executor import Executor

router = APIRouter()

_REQUEST_TTL = 1800
_CONV_TASK_KEY = "session:{}:a2a_task_id"


def _serialize_event(event) -> str:
    return json.dumps(MessageToDict(event), ensure_ascii=False)


def _extract_query(body: dict) -> str:
    if isinstance(body.get("input"), dict):
        q = body["input"].get("query", "")
        if q:
            return q
    if isinstance(body.get("custom_data"), dict):
        inputs = body["custom_data"].get("inputs", {})
        if isinstance(inputs, dict):
            return inputs.get("query", "")
    return ""


def _build_request(conversation_id: str, user_query: str, body: dict) -> SendMessageRequest:
    """将 Versatile 格式请求体转为 A2A SendMessageRequest。"""
    body_struct = Struct()
    body_struct.update({"body": body})

    body_value = Value()
    body_value.struct_value.CopyFrom(body_struct)

    msg = Message()
    msg.message_id = str(uuid.uuid4())
    msg.context_id = conversation_id
    msg.task_id = ""
    msg.role = 1  # user

    text_part = msg.parts.add()
    text_part.text = user_query

    data_part = msg.parts.add()
    data_part.data.CopyFrom(body_value)

    return SendMessageRequest(message=msg)


@router.post(
    "/v1/{project_id}/agents/{agent_id}/conversations/{conversation_id}",
    response_model=None,
)
async def dispatch(
    project_id: str,
    agent_id: str,
    conversation_id: str,
    request: Request,
) -> StreamingResponse | JSONResponse:
    body = await request.json()
    user_query = _extract_query(body)
    stream_mode = body.get("stream", True)
    traceid = str(uuid.uuid4())

    logger.info(
        f"[Router] conv={conversation_id} stream={stream_mode} "
        f"query={user_query!r:.80} trace={traceid}"
    )

    executor: Executor = request.app.state.executor
    redis = request.app.state.redis
    task_store = request.app.state.task_store
    event_queue = EventQueue()
    headers = dict(request.headers)

    # 缓存首轮请求头和请求体，供 VA 委托调用使用
    cached = await redis.get_json(session_request_key(conversation_id))
    if cached is None:
        await redis.set_json(
            session_request_key(conversation_id),
            {"headers": headers, "body": body},
            ex=_REQUEST_TTL,
        )

    # ── Task 管理：conv_id → task_id 映射 ─────────────────────────────────
    call_context = ServerCallContext()
    conv_task_key = _CONV_TASK_KEY.format(conversation_id)
    task_id = await redis.get(conv_task_key)
    current_task = None

    if task_id:
        current_task = await task_store.get(task_id, call_context)
        # 上轮已完成 → 原子重置：先删旧 key，再 SET NX；若并发请求抢先，则沿用对方写入的 task_id
        if current_task and current_task.status.state == TASK_STATE_COMPLETED:
            new_task_id = str(uuid.uuid4())
            await redis.delete(conv_task_key)
            was_set = await redis.set_nx(conv_task_key, new_task_id, ex=_REQUEST_TTL)
            task_id = new_task_id if was_set else (await redis.get(conv_task_key) or new_task_id)
            current_task = None
            logger.debug(f"[Router] 上轮已完成，新建 task_id={task_id} for conv={conversation_id}")
    else:
        # 首轮：SET NX 原子创建，防止并发重入生成两个 task_id
        new_task_id = str(uuid.uuid4())
        was_set = await redis.set_nx(conv_task_key, new_task_id, ex=_REQUEST_TTL)
        if was_set:
            task_id = new_task_id
            logger.debug(f"[Router] 新建 task_id={task_id} for conv={conversation_id}")
        else:
            # 并发请求已抢先创建，读取其写入的 task_id，复用同一 Task
            task_id = await redis.get(conv_task_key)
            current_task = await task_store.get(task_id, call_context) if task_id else None
            logger.debug(f"[Router] 并发首轮，复用 task_id={task_id} for conv={conversation_id}")

    send_request = _build_request(conversation_id, user_query, body)
    ctx = RequestContext(
        call_context=call_context,
        request=send_request,
        context_id=conversation_id,
        task_id=task_id,
        task=current_task,
    )

    async def run() -> None:
        try:
            await executor.execute(ctx, event_queue)
        except Exception as e:
            logger.exception(f"[Router] execute 异常：{e}")
            try:
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=conversation_id,
                        status=TaskStatus(state=TASK_STATE_FAILED),
                    )
                )
            except Exception:
                pass
        finally:
            await event_queue.close()

    if stream_mode:
        async def generate():
            run_task = asyncio.create_task(run())
            try:
                while True:
                    try:
                        event = await event_queue.dequeue_event()
                        event_queue.task_done()
                        yield f"data: {_serialize_event(event)}\n\n"
                    except Exception as e:
                        if type(e).__name__ != "QueueShutDown":
                            logger.warning(f"[Router] generate 序列化异常，停止推送：{e!r}")
                        break
                yield "data: [DONE]\n\n"
            finally:
                if not run_task.done():
                    run_task.cancel()
                    logger.debug(f"[Router] 客户端断连，取消后台任务：conv={conversation_id}")

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        await run()
        collected = []
        while True:
            try:
                event = await asyncio.wait_for(
                    event_queue.dequeue_event(), timeout=0.01
                )
                event_queue.task_done()
                collected.append(event)
            except Exception:
                break

        answer = ""
        for event in collected:
            if (
                isinstance(event, TaskStatusUpdateEvent)
                and event.status
                and event.status.state == TASK_STATE_COMPLETED
                and event.status.message
            ):
                for part in event.status.message.parts:
                    if part.WhichOneof("content") == "text":
                        answer = part.text
                        break

        return JSONResponse(content={"success": True, "answer": answer})
