# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
日志 模板函数。
"""
import contextvars
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from fastapi import Request
from loguru import logger
from pydantic import BaseModel

_TRACE_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "applications_logger_trace_id", default="unknown"
)
_AGENT_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "applications_logger_agent_id", default="unknown"
)
_CONVERSATION_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "applications_logger_conversation_id", default="unknown"
)

# 审计日志中需要脱敏的敏感字段名（统一使用小写匹配）
_SENSITIVE_KEYS = {
    "api_key", "apikey", "token", "access_token", "refresh_token",
    "password", "secret", "authorization", "cust-token",
}


def _mask_sensitive_fields(payload: Any) -> Any:
    """对 dict / list 结构做递归脱敏，命中 _SENSITIVE_KEYS 的字段值替换为 '***'。"""
    if isinstance(payload, dict):
        return {
            k: ("***" if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS
                else _mask_sensitive_fields(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [_mask_sensitive_fields(v) for v in payload]
    return payload


class TagTrace(BaseModel):
    id: Optional[str] = None # trace_id
    timestamp: str # 接口开始的时间戳信息
    name: Optional[str] = None # 可以自定义, 可以是接口Path
    user_id: Optional[str] = None # 接口传入的用户ID信息
    session_id: Optional[str] = None # 接口传入的conversation_id
    input: Optional[dict] = None # 接口调用信息，可以包含body分别按照Json的字段写入
    output: Optional[dict] = None # 接口返回信息，已有的信息都可以记录到Json中
    metadata: Optional[dict] = None # 附加信息记录
    tags: Optional[list] = None # 时间TAG可以记录这里,
    release: Optional[str] = None # Agent版本信息


class ObservationType(StrEnum):
    SPAN = "SPAN"  # 通用操作 / 跨度
    EVENT = "EVENT" # 时间点事件
    GENERATION = "GENERATION" # LLM生成操作
    AGENT = "AGENT" # 自主代理操作
    TOOL = "TOOL" # 工具调用
    CHAIN = "CHAIN" # 链式操作
    RETRIEVER = "RETRIEVER" # 文档检索
    EVALUATOR = "EVALUATOR" # 质量评估
    EMBEDDING = "EMBEDDING" # 向量生成
    GUARDRAIL = "GUARDRAIL" # 安全检查


class TagObservation(BaseModel):
    # 一个阶段的唯一id,暂时使用时间戳来代替id，保证start和end的id是一样的
    id: str
    # 暂时可以先不传，工具从日志头解析
    trace_id: Optional[str] = None
    # 父Observation的ID，如果是接口调用下的感知、规划、执行、反思阶段，没有父ID，
    # 如果是模型调用、执行todolist、调用Versatile等属于规划、执行等阶段的更细粒度步骤，
    # 父ID为其上一层阶段的ID,现阶段置空
    parent_observation_id: Optional[str] = None
    # 可根据阶段类型匹配，例如某一跨度的阶段可以使用SPAN，模型调用使用GENERATION，工具调用使用TOOL等
    type: Optional[ObservationType] = None
    name: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    input: Optional[dict] = None
    output: Optional[dict] = None
    metadata: Optional[dict] = None
    status_message: Optional[int] = None
    model: Optional[str] = None
    internal_model_id: Optional[str] = None # 模型调用阶段的模型ID, 现阶段不填
    model_parameters: Optional[dict] = None # 模型调用阶段的模型参数信息, 现阶段不填
    usage_details: Optional[dict[str, int]] = None # 模型调用阶段的模型使用信息，例如token数等, 现阶段不填,
    cost_details: Optional[dict[str, int]] = None # 模型调用阶段的模型耗时信息，例如Cost数等, 暂时只记录首token耗时,
    total_cost: Optional[int] = None # 模型调用阶段的调用总耗时, 或者接口 / 工具调用的耗时,
    completion_start_time: Optional[str] = None # 模型调用阶段的流式响应开始时间, 现阶段不填,
    prompt_id: Optional[str] = None # 模型调用阶段使用的Prompt ID, 现阶段不填


class Tag(StrEnum):
    TAG_HTTP_REQUEST_START = "HTTP_REQUEST_START"
    TAG_HTTP_REQUEST_END = "HTTP_REQUEST_END"
    TAG_AGENT_INIT_TOOLLIST = "AGENT_INIT_TOOLLIST"
    TAG_LLM_CALL_START = "LLM_CALL_START"
    TAG_LLM_CALL_END = "LLM_CALL_END"
    TAG_PLANNING_DECISION = "PLANNING_DECISION"
    TAG_TODOLIST_QUERY = "TODOLIST_QUERY"
    TAG_TODOLIST_SAVE = "TODOLIST_SAVE"
    TAG_SKILL_EXECUTE_START = "SKILL_EXECUTE_START"
    TAG_SKILL_EXECUTE_END = "SKILL_EXECUTE_END"
    TAG_VERSATILE_START = "VERSATILE_START"
    TAG_VERSATILE_END = "VERSATILE_END"


class Extra(BaseModel):
    tag: Optional[Tag] = None
    cost: Optional[int] = None
    source: Optional[str] = None
    user: Optional[str] = None
    result: Optional[str] = None
    terminal: Optional[str] = None


@dataclass(frozen=True)
class LogContext:
    trace_id: str
    agent_id: str
    conversation_id: str


@dataclass(frozen=True)
class HttpRequestTagContext:
    log_context: LogContext
    request_path: str
    content_type: str
    request_headers: dict[str, str]
    request_body_snapshot: Any
    user_id: str


def to_logger(level: int | str = logging.INFO, message: Any = "", extra: Extra | None = None):
    if extra is not None:
        if isinstance(message, BaseModel):
            message = message.model_dump_json(exclude_none=True)
        with logger.contextualize(**extra.model_dump(exclude_none=True)):
            logger.log(level, message)
    else:
        logger.log(level, message)


def current_local_time() -> str:
    # 生成毫秒精度本地时间字符串，显式指定 UTC 时区后转换为本地时区以避免时区歧义
    return (
        datetime.now(tz=timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    )


def extract_header_value(headers: dict[str, str], name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return ""


def current_tag_context() -> tuple[str, str, str]:
    return _TRACE_ID_CTX.get(), _AGENT_ID_CTX.get(), _CONVERSATION_ID_CTX.get()


def build_log_context(trace_id: str, agent_id: str, conversation_id: str) -> LogContext:
    return LogContext(
        trace_id=trace_id or "unknown",
        agent_id=agent_id or "unknown",
        conversation_id=conversation_id or "unknown",
    )


@contextmanager
def bind_context(log_context: LogContext):
    trace_token = _TRACE_ID_CTX.set(log_context.trace_id)
    agent_token = _AGENT_ID_CTX.set(log_context.agent_id)
    conversation_token = _CONVERSATION_ID_CTX.set(log_context.conversation_id)
    try:
        with logger.contextualize(
            trace_id=log_context.trace_id,
            agent_id=log_context.agent_id,
            conversation_id=log_context.conversation_id,
        ):
            yield
    finally:
        _TRACE_ID_CTX.reset(trace_token)
        _AGENT_ID_CTX.reset(agent_token)
        _CONVERSATION_ID_CTX.reset(conversation_token)


async def build_http_request_tag_context(
    *,
    request: Request,
    trace_id: str,
    agent_id: str,
    conversation_id: str,
) -> HttpRequestTagContext:
    request_headers = dict(request.headers)
    content_type = request.headers.get("content-type", "").lower()
    request_body_snapshot: Any = {"raw_body": ""}

    try:
        raw_body = await request.body()
        raw_body_text = raw_body.decode("utf-8", errors="replace")
        if raw_body_text:
            if "application/json" in content_type:
                try:
                    request_body_snapshot = json.loads(raw_body_text)
                    request_body_snapshot = _mask_sensitive_fields(request_body_snapshot)
                except Exception:
                    logger.exception("[logger] 请求 body JSON 解析失败")
                    request_body_snapshot = {"raw_body": raw_body_text}
                    request_body_snapshot = _mask_sensitive_fields(request_body_snapshot)
            else:
                request_body_snapshot = {"raw_body": raw_body_text}
                request_body_snapshot = _mask_sensitive_fields(request_body_snapshot)
    except Exception:
        logger.exception("[logger] 请求 body 读取失败")
        request_body_snapshot = {"raw_body": "<unavailable>"}

    user_id = extract_header_value(
        request_headers, "x-user-id"
    ) or extract_header_value(request_headers, "cust-userid")

    return HttpRequestTagContext(
        log_context=build_log_context(trace_id, agent_id, conversation_id),
        request_path=request.url.path,
        content_type=content_type,
        request_headers=request_headers,
        request_body_snapshot=request_body_snapshot,
        user_id=user_id,
    )


def build_http_trace(
    *,
    http_request_tag_context: HttpRequestTagContext,
    input_payload: Optional[dict[str, Any]] = None,
    output_payload: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
    release: str = "1.0.0",
) -> TagTrace:
    trace_id = http_request_tag_context.log_context.trace_id
    return TagTrace(
        id=trace_id,
        timestamp=current_local_time(),
        name=http_request_tag_context.request_path,
        user_id=http_request_tag_context.user_id,
        session_id=http_request_tag_context.log_context.conversation_id,
        input=input_payload if input_payload is not None else {},
        output=output_payload if output_payload is not None else {},
        metadata=metadata if metadata is not None else {"UNION_NO": trace_id},
        tags=tags if tags is not None else [],
        release=release,
    )


def build_versatile_start_observation(
    *,
    call_id: str,
    name: str,
    request_headers: dict[str, Any],
    request_body: Any,
) -> TagObservation:
    trace_id, _, _ = current_tag_context()
    return TagObservation(
        id=call_id,
        trace_id=trace_id,
        type=ObservationType.TOOL,
        name=name,
        start_time=current_local_time(),
        input={
            "request_header": request_headers,
            "request_body": request_body,
        },
    )


def build_versatile_end_observation(
    *,
    call_id: str,
    name: str,
    output_payload: dict[str, Any],
    status_message: Any,
    duration_ms: int,
) -> TagObservation:
    trace_id, _, _ = current_tag_context()
    return TagObservation(
        id=call_id,
        trace_id=trace_id,
        type=ObservationType.TOOL,
        name=name,
        end_time=current_local_time(),
        output=output_payload,
        status_message=status_message,
        total_cost=max(duration_ms, 0),
    )
