# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

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
import socket
import time
import uuid
from typing import Any, Optional, Tuple

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Message,
    SendMessageRequest,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
)
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct, Value
from loguru import logger

from common.constants import session_request_key
from common.logger import (
    Extra,
    Tag,
    bind_context,
    build_http_request_tag_context,
    build_http_trace,
    to_logger,
    get_real_ip, Level, ResultEnum
)
from common.response_wrapper import wrap_agent_event, wrap_workflow_event
from config import get_settings
from orchestrator.executor import Executor
from orchestrator.sse_helpers import log_outbound_sse, next_sse_event

router = APIRouter()

_REQUEST_TTL = 1800
_CONV_TASK_KEY = "session:{}:a2a_task_id"
ERROR_CODE_RATE_LIMIT_EXCEEDED = "100001"
ERROR_MSG_RATE_LIMIT_EXCEEDED = "系统超负载，请在稍后重试"
DEFAULT_SESSION_MAX_REQUESTS = 1
DEFAULT_SESSION_WINDOW_SECONDS = 10
DEFAULT_GLOBAL_MAX_REQUESTS = 10
DEFAULT_GLOBAL_WINDOW_SECONDS = 10
FLOW_CONTROL_TEST_QUERY_PREFIX = "flow-control-test:"

_RATE_LIMIT_ATOMIC_LUA = """
-- KEYS[1]: session_rate_limit_key
-- KEYS[2]: global_rate_limit_key
-- ARGV[1]: current_time
-- ARGV[2]: session_window_start
-- ARGV[3]: session_max_requests
-- ARGV[4]: session_expire_seconds
-- ARGV[5]: global_window_start
-- ARGV[6]: global_max_requests
-- ARGV[7]: global_expire_seconds
-- ARGV[8]: conversation_id
-- ARGV[9]: session_member
-- ARGV[10]: global_member

local session_key = KEYS[1]
local global_key = KEYS[2]

local current_time = tonumber(ARGV[1])
local session_window_start = tonumber(ARGV[2])
local session_max_requests = tonumber(ARGV[3])
local session_expire_seconds = tonumber(ARGV[4])
local global_window_start = tonumber(ARGV[5])
local global_max_requests = tonumber(ARGV[6])
local global_expire_seconds = tonumber(ARGV[7])
local conversation_id = ARGV[8]
local session_member = ARGV[9]
local global_member = ARGV[10]

redis.call('ZREMRANGEBYSCORE', session_key, '-inf', session_window_start)
local session_requests = tonumber(redis.call('ZCARD', session_key))
if session_requests >= session_max_requests then
    return {0, 'session', session_requests, -1}
end

redis.call('ZREMRANGEBYSCORE', global_key, '-inf', global_window_start)
local global_requests = tonumber(redis.call('ZCARD', global_key))

local session_exists_in_global = 0
if global_requests >= global_max_requests then
    local prefix = conversation_id .. ':'
    local cursor = '0'
    repeat
        local scan_result = redis.call('ZSCAN', global_key, cursor, 'MATCH', prefix .. '*', 'COUNT', 100)
        cursor = scan_result[1]
        local members = scan_result[2]
        if members ~= nil and #members > 0 then
            session_exists_in_global = 1
            break
        end
    until cursor == '0'
end

if global_requests >= global_max_requests and session_exists_in_global == 0 then
    return {0, 'global', session_requests, global_requests}
end

redis.call('ZADD', session_key, current_time, session_member)
redis.call('EXPIRE', session_key, session_expire_seconds)
redis.call('ZADD', global_key, current_time, global_member)
redis.call('EXPIRE', global_key, global_expire_seconds)

if session_exists_in_global == 1 then
    return {1, 'ok_existing', session_requests + 1, global_requests + 1}
end
return {1, 'ok', session_requests + 1, global_requests + 1}
"""


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return default


def _get_rate_limit_config(agent_id: str, settings) -> dict:
    session_max = _positive_int(
        getattr(settings, "rate_limit_max_requests", None),
        DEFAULT_SESSION_MAX_REQUESTS,
    )
    session_window = _positive_int(
        getattr(settings, "rate_limit_window_seconds", None),
        DEFAULT_SESSION_WINDOW_SECONDS,
    )
    global_max = _positive_int(
        getattr(settings, "global_rate_limit_max_requests", None),
        DEFAULT_GLOBAL_MAX_REQUESTS,
    )
    global_window = _positive_int(
        getattr(settings, "global_rate_limit_window_seconds", None),
        DEFAULT_GLOBAL_WINDOW_SECONDS,
    )

    default_config = {
        "enabled": True,
        "session_max_requests": session_max,
        "session_window_seconds": session_window,
        "global_max_requests": global_max,
        "global_window_seconds": global_window,
    }
    config_map = {
        "edp_agent": {
            "enabled": True,
            "session_max_requests": session_max,
            "session_window_seconds": session_window,
            "global_max_requests": global_max,
            "global_window_seconds": global_window,
        }
    }
    return config_map.get(agent_id, default_config)


async def _check_rate_limit(
    redis,
    settings,
    agent_id: str,
    conversation_id: str,
) -> Tuple[bool, Optional[str], Optional[str]]:
    try:
        # Redis 未配置时按 fail-open 放行
        if not settings.redis_host:
            logger.warning("[RateLimit] Redis 未配置，跳过限流检查")
            return True, None, None

        try:
            redis_client = redis.client
        except RuntimeError:
            logger.warning("[RateLimit] Redis 客户端未初始化，跳过限流检查")
            return True, None, None

        rate_limit_config = _get_rate_limit_config(agent_id, settings)
        if not rate_limit_config or not rate_limit_config.get("enabled", False):
            logger.debug(f"[RateLimit] Agent {agent_id} 无限流配置或限流未启用，跳过限流检查")
            return True, None, None

        rate_limit_max_requests = _positive_int(
            rate_limit_config.get("session_max_requests"),
            DEFAULT_SESSION_MAX_REQUESTS,
        )
        rate_limit_window_seconds = _positive_int(
            rate_limit_config.get("session_window_seconds"),
            DEFAULT_SESSION_WINDOW_SECONDS,
        )
        global_rate_limit_max_requests = _positive_int(
            rate_limit_config.get("global_max_requests"),
            DEFAULT_GLOBAL_MAX_REQUESTS,
        )
        global_rate_limit_window_seconds = _positive_int(
            rate_limit_config.get("global_window_seconds"),
            DEFAULT_GLOBAL_WINDOW_SECONDS,
        )

        current_time = time.time()

        # Session 级限流 key
        session_window_start = current_time - rate_limit_window_seconds
        session_rate_limit_key = f"a2a_service:rate_limit:{agent_id}:session:{conversation_id}"

        # 全局级限流 key
        global_window_start = current_time - global_rate_limit_window_seconds
        global_rate_limit_key = f"a2a_service:rate_limit:{agent_id}:global"

        result = await redis_client.eval(
            _RATE_LIMIT_ATOMIC_LUA,
            2,
            session_rate_limit_key,
            global_rate_limit_key,
            str(current_time),
            str(session_window_start),
            str(rate_limit_max_requests),
            str(rate_limit_window_seconds * 2),
            str(global_window_start),
            str(global_rate_limit_max_requests),
            str(global_rate_limit_window_seconds * 2),
            conversation_id,
            str(uuid.uuid4()),
            f"{conversation_id}:{uuid.uuid4()}",
        )

        if not isinstance(result, (list, tuple)) or len(result) < 2:
            raise RuntimeError(f"限流 Lua 返回异常: {result!r}")

        allowed = int(result[0]) == 1
        reason = str(result[1])
        session_requests = int(result[2]) if len(result) > 2 and result[2] is not None else -1
        global_requests = int(result[3]) if len(result) > 3 and result[3] is not None else -1

        if not allowed and reason == "session":
            logger.warning(
                f"[RateLimit] 会话 {conversation_id} 触发 Session 级限流，"
                f"当前请求数：{session_requests}, 限制：{rate_limit_max_requests}"
            )
            return False, ERROR_MSG_RATE_LIMIT_EXCEEDED, ERROR_CODE_RATE_LIMIT_EXCEEDED

        if not allowed and reason == "global":
            logger.warning(
                f"[RateLimit] Agent {agent_id} 触发全局限流（新会话拦截），"
                f"conversation_id={conversation_id}, 当前请求数：{global_requests}, "
                f"限制：{global_rate_limit_max_requests}"
            )
            return False, ERROR_MSG_RATE_LIMIT_EXCEEDED, ERROR_CODE_RATE_LIMIT_EXCEEDED

        if not allowed:
            logger.warning(
                f"[RateLimit] Lua 返回拒绝但原因未知，默认拒绝："
                f"agent_id={agent_id}, conversation_id={conversation_id}, reason={reason}"
            )
            return False, ERROR_MSG_RATE_LIMIT_EXCEEDED, ERROR_CODE_RATE_LIMIT_EXCEEDED

        if reason == "ok_existing":
            logger.info(
                f"[RateLimit] Agent {agent_id} 全局已满但会话已存在，允许通过，"
                f"conversation_id={conversation_id}, 当前请求数：{global_requests}, "
                f"限制：{global_rate_limit_max_requests}"
            )

        logger.info(
            f"[RateLimit] 会话 {conversation_id} 通过限流检查，"
            f"Session 请求数：{session_requests}/{rate_limit_max_requests}, "
            f"全局请求数：{global_requests}/{global_rate_limit_max_requests}"
        )
        return True, None, None
    except Exception as e:
        logger.error(f"[RateLimit] 限流检查失败：{e}")
        # fail-open：限流异常不阻断主链路
        return True, None, None


def _extract_event_meta(event) -> Optional[dict]:
    """从 A2A protobuf event 抽出 {kind, type, content, data}。

    返回 None 表示该 event 不需要向前端推送。

    - agent 事件：TaskArtifactUpdateEvent，data part 带 ``type`` 键
      → {"kind": "agent", "type": <event_type>, "content": <text>, "data": <剩余>}
    - workflow 事件：TaskArtifactUpdateEvent，data part 形状为 ``{"event": <kind>, "data": <dict>}``
      —— 由 VersatileAdapter 的 `_unwrap_upstream_frame` 解包后产出；
      event == "end" 是上游流结束信号，返回 None 由 [DONE] 收尾。
      → {"kind": "workflow", "type": "<event>", "content": "", "data": <节点数据>}
    - TaskStatusUpdateEvent(COMPLETED) 当作 final_answer_end 对待（agent 事件）
    - 其他 TaskStatusUpdateEvent（如 FAILED）返回 None，不推送给前端
    """
    if isinstance(event, TaskArtifactUpdateEvent):
        text_content = ""
        data_dict: dict = {}
        for part in event.artifact.parts:
            kind = part.WhichOneof("content")
            if kind == "text":
                text_content = part.text or ""
            elif kind == "data":
                data_dict = MessageToDict(part.data)

        # workflow 事件：VersatileAdapter 解包后的 {event, data} 形状
        if "event" in data_dict and isinstance(data_dict.get("data"), dict):
            event_kind = data_dict["event"]
            if event_kind == "end":
                # 上游流结束信号，不作为北向事件推送
                return None
            return {
                "kind": "workflow",
                "type": event_kind,
                "content": "",
                "data": data_dict["data"],
            }

        # agent 事件：EDPAgent 约定 data 里含 type 字段
        if "type" in data_dict:
            event_type = data_dict.pop("type")
            # 注意：spec 外事件（conversation_end / summary / thought / answer /
            # delegate）不做屏蔽，与参考实现 AgentEngine 对齐（见 docs/
            # issue-versatile-blackboard-cache.md 附 D-1）。
            # data 里 content 字段与 text 重复，提出去避免冗余
            data_dict.pop("content", None)
            # tool_* 事件的 plugin 字段要上浮到 custom_rsp_data.plugin，
            # 避免埋在 data 里再被外层的空串覆盖。
            plugin = data_dict.pop("plugin", "")
            # 若事件自带 data 载荷（如 ToolEndEvent.data），直接作为 payload；
            # 否则剩余字段（id/title/status/args/progress 等）即 payload。
            nested = data_dict.pop("data", None)
            payload = nested if isinstance(nested, dict) else data_dict
            return {
                "kind": "agent",
                "type": event_type,
                "content": text_content,
                "data": payload,
                "plugin": plugin,
            }

        # 未知 artifact 形态，回退为 agent thought 兼容
        logger.debug(
            f"[Router] 未知 artifact event 形态，回退兼容序列化；parts={len(event.artifact.parts)}"
        )
        return {
            "kind": "agent",
            "type": "thought",
            "content": text_content,
            "data": data_dict,
            "plugin": "",
        }

    if isinstance(event, TaskStatusUpdateEvent):
        if event.status and event.status.state == TASK_STATE_COMPLETED:
            content = ""
            if event.status.message:
                for part in event.status.message.parts:
                    if part.WhichOneof("content") == "text":
                        content = part.text or ""
                        break
            return {
                "kind": "agent",
                "type": "final_answer_end",
                "content": content,
                "data": {},
                "plugin": "",
            }
        # FAILED 事件映射为 interrupt_start agent 帧，对齐 AgentEngine：
        # 顶层 success=False + error=<msg>，custom_rsp_data.event=interrupt_start。
        # 错误描述放在 status.message.parts[0].text，由 Executor 在 VA 上游报错或
        # execute() 异常时填充。
        if event.status and event.status.state == TASK_STATE_FAILED:
            content = ""
            if event.status.message:
                for part in event.status.message.parts:
                    if part.WhichOneof("content") == "text":
                        content = part.text or ""
                        break
            return {
                "kind": "agent",
                "type": "interrupt_start",
                "content": content,
                "data": {},
                "plugin": "",
                "success": False,
                "error": content,
            }
        return None

    # 未识别类型：不推送
    return None


def _serialize_event(event, *, agent_id: str, conversation_id: str, start_time: float) -> Optional[str]:
    """将内部 A2A event 序列化为前端可见的 SSE JSON（已包装）。

    返回 None 表示该 event 不需要产出 SSE 帧（调用方应跳过）。
    """
    meta = _extract_event_meta(event)
    if meta is None:
        return None

    elapsed = time.monotonic() - start_time
    if meta["kind"] == "agent":
        wrapped = wrap_agent_event(
            event_type=meta["type"],
            content=meta["content"],
            data=meta["data"],
            agent_id=agent_id,
            conversation_id=conversation_id,
            elapsed=elapsed,
            plugin=meta.get("plugin", ""),
            success=meta.get("success", True),
            error=meta.get("error", ""),
        )
    else:
        wrapped = wrap_workflow_event(
            event_kind=meta["type"],
            data=meta["data"],
            agent_id=agent_id,
            conversation_id=conversation_id,
            elapsed=elapsed,
        )
    return json.dumps(wrapped, ensure_ascii=False)


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


def _extract_query_params(request) -> dict:
    """从 FastAPI Request 取 URL query string 并转 dict；非 Request 对象降级为 {}。

    首跳捕获的 params 会写入 session 缓存，供 VA 委托/续轮场景从 Redis 恢复，最终
    经 A2A DataPart → VersatileAdapter → httpx 透传到 Versatile 后端 URL。
    """
    if hasattr(request, "query_params"):
        return dict(request.query_params)
    return {}


def _build_request(
    conversation_id: str,
    user_query: str,
    body: dict,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> SendMessageRequest:
    """将 Versatile 格式请求体转为 A2A SendMessageRequest。

    headers 须随每轮请求透传给 executor，否则下游 versatile_adapter
    收不到 cust-token / x-user-id 等关键头（见 issue 2026-04-28）。
    """
    body_struct = Struct()
    body_struct.update(
        {
            "body": body,
            "params": params or {},
            "headers": headers or {},
        }
    )

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


def _is_flow_control_probe(user_query: str) -> bool:
    return user_query.startswith(FLOW_CONTROL_TEST_QUERY_PREFIX)


async def _ensure_task_mapping(redis, conversation_id: str) -> str:
    conv_task_key = _CONV_TASK_KEY.format(conversation_id)
    task_id = await redis.get(conv_task_key)
    if task_id:
        return task_id

    new_task_id = str(uuid.uuid4())
    was_set = await redis.set_nx(conv_task_key, new_task_id, ex=_REQUEST_TTL)
    if was_set:
        return new_task_id
    return await redis.get(conv_task_key) or new_task_id


def _build_flow_control_probe_response(conversation_id: str, agent_id: str) -> dict:
    return {
        "success": True,
        "answer": "flow-control-test-ok",
        "conversation_id": conversation_id,
        "agent_id": agent_id,
    }


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
    traceid = str(uuid.uuid4())
    settings = get_settings()
    request_started_ms = int(time.time() * 1000)
    # 在工具类中统一完成请求快照解析与公共字段提取
    http_request_tag_context = await build_http_request_tag_context(
        request=request,
        trace_id=traceid,
        agent_id=agent_id,
        conversation_id=conversation_id,
    )

    to_logger(
        level=Level.INFO,
        message=build_http_trace(
            http_request_tag_context=http_request_tag_context,
            metadata={
                "UNION_NO": http_request_tag_context.request_body_snapshot.get("custom_data", {}).get("inputs",
                    {}).get("UNION_NO", None)
            },
            input_payload={
                "request_header": http_request_tag_context.request_headers,
                "request_body": http_request_tag_context.request_body_snapshot,
            },
        ),
        extra=Extra(tag=Tag.TAG_HTTP_REQUEST_START, cost=0),
        log_context=http_request_tag_context.log_context,
    )

    # 显式校验 Content-Type + JSON 解析（对应需求 §5.10）
    if "application/json" not in http_request_tag_context.content_type:
        response_content = {
            "success": False,
            "error": "unsupported_media_type",
            "message": "请求数据格式需为 application/json",
        }
        duration_ms = int(time.time() * 1000) - request_started_ms
        to_logger(
            level="WARNING",
            message=build_http_trace(
                http_request_tag_context=http_request_tag_context,
                metadata={
                    "UNION_NO": http_request_tag_context.request_body_snapshot.get("custom_data", {}).get("inputs",
                                                                                                        {}).get(
                        "UNION_NO", None)
                },
                output_payload=response_content,
            ),
            extra=Extra(tag=Tag.TAG_HTTP_REQUEST_END, cost=max(duration_ms, 0)),
        )
        return JSONResponse(status_code=415, content=response_content)

    try:
        body = await request.json()
    except Exception as e:
        logger.warning(f"[Router] JSON parse failed: {e}")
        response_content = {
            "success": False,
            "error": "invalid_json",
            "message": f"请求 body 非合法 JSON 格式：{e}",
        }
        duration_ms = int(time.time() * 1000) - request_started_ms
        to_logger(
            level="WARNING",
            message=build_http_trace(
                http_request_tag_context=http_request_tag_context,
                metadata={
                    "UNION_NO": http_request_tag_context.request_body_snapshot.get("custom_data", {}).get("inputs",
                                                                                                        {}).get(
                        "UNION_NO", None)
                },
                output_payload=response_content,
            ),
            extra=Extra(tag=Tag.TAG_HTTP_REQUEST_END, cost=max(duration_ms, 0)),
        )
        return JSONResponse(status_code=400, content=response_content)

    if not isinstance(body, dict):
        response_content = {
            "success": False,
            "error": "invalid_body",
            "message": "请求 body 必须是 JSON 对象（dict）",
        }
        duration_ms = int(time.time() * 1000) - request_started_ms
        to_logger(
            level=Level.WARNING,
            message=build_http_trace(
                http_request_tag_context=http_request_tag_context,
                metadata={
                    "UNION_NO": http_request_tag_context.request_body_snapshot.get("custom_data", {}).get("inputs",
                                                                                                        {}).get(
                        "UNION_NO", None)
                },
                output_payload=response_content,
            ),
            extra=Extra(tag=Tag.TAG_HTTP_REQUEST_END, cost=max(duration_ms, 0)),
            log_context=http_request_tag_context.log_context,
        )
        return JSONResponse(status_code=400, content=response_content)

    user_query = _extract_query(body)
    stream_mode = bool(body.get("stream", True))

    logger.info(
        f"[Router] conv={conversation_id} stream={stream_mode} "
        f"query={user_query!r:.80} trace={traceid}"
    )

    executor: Executor = request.app.state.executor
    redis = request.app.state.redis
    task_store = request.app.state.task_store
    event_queue = EventQueue()
    headers = http_request_tag_context.request_headers

    # 入口限流：在进入编排执行前统一拦截
    allowed, error_msg, error_code = await _check_rate_limit(
        redis=redis,
        settings=settings,
        agent_id=agent_id,
        conversation_id=conversation_id,
    )
    if not allowed:
        logger.warning(
            f"[Router] Rate limited conv={conversation_id}, agent={agent_id}, "
            f"code={error_code}, trace={traceid}"
        )
        rejection = {
            "success": False,
            "error": error_msg,
            "error_code": error_code,
            "conversation_id": conversation_id,
            "agent_id": agent_id,
        }
        if stream_mode:

            async def limited_generate():
                with bind_context(http_request_tag_context.log_context):
                    try:
                        yield f"data: {json.dumps(rejection, ensure_ascii=False)}\\n\\n"
                    finally:
                        duration_ms = int(time.time() * 1000) - request_started_ms
                        to_logger(
                            level=Level.WARNING,
                            message=build_http_trace(
                                http_request_tag_context=http_request_tag_context,
                                metadata={
                                    "UNION_NO": http_request_tag_context.request_body_snapshot.get("custom_data",
                                                                                                {}).get(
                                        "inputs", {}).get("UNION_NO", None)
                                },
                                output_payload={
                                    "mode": "stream",
                                    "status_code": status.HTTP_429_TOO_MANY_REQUESTS,
                                    "rejection": rejection,
                                },
                            ),
                            extra=Extra(tag=Tag.TAG_HTTP_REQUEST_END, cost=max(duration_ms, 0)),
                            log_context=http_request_tag_context.log_context,
                        )
            return StreamingResponse(
                limited_generate(),
                media_type="text/event-stream",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        duration_ms = int(time.time() * 1000) - request_started_ms
        to_logger(
            level=Level.WARNING,
            message=build_http_trace(
                http_request_tag_context=http_request_tag_context,
                metadata={
                    "UNION_NO": http_request_tag_context.request_body_snapshot.get("custom_data", {}).get("inputs",
                                                                                                        {}).get(
                        "UNION_NO", None)
                },
                output_payload=rejection,
            ),
            extra=Extra(tag=Tag.TAG_HTTP_REQUEST_END, cost=max(duration_ms, 0)),
            log_context=http_request_tag_context.log_context,
        )
        return JSONResponse(
            content=rejection,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # 缓存首轮请求头、请求体和 URL query params，供 VA 委托调用/续轮恢复使用
    params = _extract_query_params(request)
    cached = await redis.get_json(session_request_key(conversation_id))
    if cached is None:
        await redis.set_json(
            session_request_key(conversation_id),
            {"headers": headers, "body": body, "params": params, "trace_id": traceid, "agent_id": agent_id},
            ex=_REQUEST_TTL,
        )

    if _is_flow_control_probe(user_query):
        task_id = await _ensure_task_mapping(redis, conversation_id)
        probe_response = _build_flow_control_probe_response(conversation_id, agent_id)
        logger.info(
            f"[Router] flow-control probe fast-path: conv={conversation_id}, task_id={task_id}, trace={traceid}"
        )
        if stream_mode:

            async def probe_generate():
                with bind_context(http_request_tag_context.log_context):
                    try:
                        yield f"data: {json.dumps(probe_response, ensure_ascii=False)}\n\n"
                    finally:
                        duration_ms = int(time.time() * 1000) - request_started_ms
                        to_logger(
                            message=build_http_trace(
                                http_request_tag_context=http_request_tag_context,
                                output_payload={
                                    "mode": "stream",
                                    "status_code": status.HTTP_200_OK,
                                    "probe": probe_response,
                                },
                            ),
                            extra=Extra(tag=Tag.TAG_HTTP_REQUEST_END, cost=max(duration_ms, 0)),
                            log_context=http_request_tag_context.log_context,
                        )

            return StreamingResponse(
                probe_generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        duration_ms = int(time.time() * 1000) - request_started_ms
        to_logger(
            level=Level.INFO,
            message=build_http_trace(
                http_request_tag_context=http_request_tag_context,
                metadata={
                    "UNION_NO": http_request_tag_context.request_body_snapshot.get("custom_data", {}).get("inputs",
                                                                                                        {}).get(
                        "UNION_NO", None)
                },
                output_payload=probe_response,
            ),
            extra=Extra(tag=Tag.TAG_HTTP_REQUEST_END, cost=max(duration_ms, 0)),
            log_context=http_request_tag_context.log_context,
        )
        return JSONResponse(content=probe_response)

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
            task_id = (
                new_task_id
                if was_set
                else (await redis.get(conv_task_key) or new_task_id)
            )
            current_task = None
            logger.debug(
                f"[Router] 上轮已完成，新建 task_id={task_id} for conv={conversation_id}"
            )
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
            current_task = (
                await task_store.get(task_id, call_context) if task_id else None
            )
            logger.debug(
                f"[Router] 并发首轮，复用 task_id={task_id} for conv={conversation_id}"
            )

    send_request = _build_request(
        conversation_id, user_query, body, params, headers=headers
    )
    ctx = RequestContext(
        call_context=call_context,
        request=send_request,
        context_id=conversation_id,
        task_id=task_id,
        task=current_task,
    )

    async def run() -> None:
        with bind_context(http_request_tag_context.log_context):
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
                except Exception as enqueue_exc:
                    logger.warning(
                        "[Router] 入队 FAILED 状态事件失败（忽略）：{!r}",
                        enqueue_exc,
                    )
            finally:
                await event_queue.close()

    if stream_mode:

        async def generate():
            with bind_context(http_request_tag_context.log_context):
                run_task = asyncio.create_task(run())
                pushed_events = 0
                status_message = 0
                finish_reason = "stream_completed"
                turn_start = time.monotonic()
                try:
                    while True:
                        try:
                            event = await next_sse_event(event_queue)
                            if event is None:
                                break
                            if (
                                isinstance(event, TaskStatusUpdateEvent)
                                and event.status
                                and event.status.state == TASK_STATE_FAILED
                            ):
                                status_message = 1
                            payload = _serialize_event(
                                event,
                                agent_id=agent_id,
                                conversation_id=conversation_id,
                                start_time=turn_start,
                            )
                            # None 表示该事件被屏蔽或是流结束信号，不推送
                            if payload is None:
                                continue
                            pushed_events += 1
                            log_outbound_sse(
                                conversation_id=conversation_id,
                                sequence=pushed_events,
                                payload=payload,
                                event_kind=type(event).__name__,
                            )
                            yield f"data: {payload}\n\n"
                        except Exception as e:
                            logger.warning(
                                f"[Router] generate 序列化异常，停止推送：{e!r}"
                            )
                            status_message = 1
                            break
                finally:
                    if not run_task.done():
                        run_task.cancel()
                        status_message = 1
                        finish_reason = "client_disconnected"
                        logger.debug(
                            f"[Router] 客户端断连，取消后台任务：conv={conversation_id}"
                        )
                        to_logger(level=Level.INFO, message=user_query,
                                  extra=Extra(source=get_real_ip(request), user=body.get("userId", ""),
                                              result=ResultEnum.FAILED,
                                              terminal=socket.gethostbyname(socket.gethostname())),
                                  log_context=http_request_tag_context.log_context,
                                  )
                    else:
                        to_logger(level=Level.INFO, message=user_query,
                                  extra=Extra(source=get_real_ip(request), user=body.get("userId", ""),
                                              result=ResultEnum.SUCCESS,
                                              terminal=socket.gethostbyname(socket.gethostname())),
                                  log_context=http_request_tag_context.log_context,
                                  )

                    duration_ms = int(time.time() * 1000) - request_started_ms
                    to_logger(
                        level=Level.WARNING if status_message else Level.INFO,

                        message=build_http_trace(
                            http_request_tag_context=http_request_tag_context,
                            metadata={
                                "UNION_NO": http_request_tag_context.request_body_snapshot.get("custom_data", {}).get(
                                    "inputs", {}).get("UNION_NO", None)
                            },
                            output_payload={
                                "mode": "stream",
                                "status_code": status.HTTP_200_OK,
                                "events": pushed_events,
                                "finish_reason": finish_reason,
                            },
                        ),
                        extra=Extra(tag=Tag.TAG_HTTP_REQUEST_END, cost=max(duration_ms, 0)),
                        log_context=http_request_tag_context.log_context,
                    )
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    collected = []
    run_task = asyncio.create_task(run())
    status_message = 0
    while True:
        try:
            event = await next_sse_event(event_queue)
            if event is None:
                break
            if (
                isinstance(event, TaskStatusUpdateEvent)
                and event.status
                and event.status.state == TASK_STATE_FAILED
            ):
                status_message = 1
            collected.append(event)
        except Exception as e:
            logger.warning(f"[Router] non-stream 队列异常：{e!r}")
            status_message = 1
            break
    await run_task

    def _is_completed_task_event(evt: Any) -> bool:
        return (
            isinstance(evt, TaskStatusUpdateEvent)
            and evt.status
            and evt.status.state == TASK_STATE_COMPLETED
            and evt.status.message
        )

    answer = ""
    for event in collected:
        if _is_completed_task_event(event):
            for part in event.status.message.parts:
                if part.WhichOneof("content") == "text":
                    answer = part.text
                    break

    response_content = {"success": True, "answer": answer}
    duration_ms = int(time.time() * 1000) - request_started_ms
    to_logger(
        level=Level.WARNING if status_message else Level.INFO,
        message=build_http_trace(
            http_request_tag_context=http_request_tag_context,
            metadata={
                "UNION_NO": http_request_tag_context.request_body_snapshot.get("custom_data", {}).get("inputs", {}).get(
                    "UNION_NO", None)
            },
            output_payload=response_content,
        ),
        extra=Extra(tag=Tag.TAG_HTTP_REQUEST_END, cost=max(duration_ms, 0)),
        log_context=http_request_tag_context.log_context,
    )
    return JSONResponse(content=response_content)