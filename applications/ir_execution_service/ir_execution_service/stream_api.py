# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""execute_stream 的 SSE 逻辑与 chunk 到 ResponseModel 的映射。"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi.exceptions import RequestValidationError
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.runner import Runner
from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_studio.schemas import ResponseModel

from .dsl_workflow_dependency_loader import WorkflowLlmApiKeyMissingError
from .react_agent_builder import build_react_agent_from_ir
from .runtime_support.context_persistence import RedisContextPersistence
from .runtime_support.execution_request import ExecutionPrepareError, prepare_execution_request
from .runtime_support.http_response_contract import (
    LowcodeApiResponseCode,
    ResponseDataType,
    build_error_response_model,
    to_jsonable,
)
from .runtime_support.runtime_bootstrap import ensure_runtime_ready

_log = get_logger(__name__)
_ctx_store = RedisContextPersistence()


def _workflow_context_id(workflow: Any) -> str:
    card = getattr(workflow, "card", None)
    wf_id = getattr(card, "id", None) if card is not None else None
    wf_ver = getattr(card, "version", None) if card is not None else None
    if wf_id and wf_ver:
        return f"{wf_id}_{wf_ver}"
    if wf_id:
        return str(wf_id)
    return "workflow"


async def _append_user_input_message_if_needed(context: Any, inputs_obj: Any) -> None:
    from openjiuwen.core.foundation.llm import UserMessage
    from openjiuwen.core.session import InteractiveInput

    content: Any = None
    if isinstance(inputs_obj, dict) and "query" in inputs_obj:
        content = inputs_obj.get("query")
    elif isinstance(inputs_obj, InteractiveInput) and inputs_obj.user_inputs:
        content = list(inputs_obj.user_inputs.values())[-1]
    if content is None:
        return
    try:
        existing = context.get_messages() if hasattr(context, "get_messages") else []
        if existing:
            last = existing[-1]
            if getattr(last, "role", None) == "user" and getattr(last, "content", None) == content:
                return
        await context.add_messages([UserMessage(role="user", content=content)])
    except Exception:
        _log.debug("append user input to context failed", exc_info=True)


@asynccontextmanager
async def _optional_async_timeout(seconds: float):
    """Python 3.11+ 的 asyncio.timeout 统一包一层，方便测试/兼容。"""
    timeout_cm = getattr(asyncio, "timeout", None)
    if timeout_cm is not None:
        async with timeout_cm(seconds):
            yield
    else:
        yield


def _sse_include_trace() -> bool:
    return (os.environ.get("LOWCODE_SSE_INCLUDE_TRACE") or "").strip().lower() in ("1", "true", "yes", "on")


def _stream_error_event(
    code: LowcodeApiResponseCode,
    *,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    return build_error_response_model(code, message=message, payload=payload).model_dump_json()


def _stream_frame_message(code: LowcodeApiResponseCode, payload: Any) -> str:
    """业务错误帧优先用 payload.message（若存在），否则用枚举默认文案。"""
    if code != LowcodeApiResponseCode.SUCCESS:
        if isinstance(payload, dict):
            m = payload.get("message")
            if m is not None and str(m).strip():
                return str(m)
        return code.default_message
    return code.default_message


def _workflow_chunk_to_type_payload_code(chunk: Any) -> tuple[str | None, Any, LowcodeApiResponseCode]:
    """将 workflow stream chunk 映射为 data.type、payload 与业务 code。"""
    from openjiuwen.core.common.constants.constant import END_NODE_STREAM, INTERACTION
    from openjiuwen.core.session.stream import CustomSchema, OutputSchema, TraceSchema
    from openjiuwen.core.session.tracer.handler import TracerHandlerName

    include_trace = _sse_include_trace()
    ok = LowcodeApiResponseCode.SUCCESS

    if isinstance(chunk, TraceSchema):
        if chunk.type == TracerHandlerName.TRACER_WORKFLOW.value:
            if not include_trace:
                return None, None, ok
            return ResponseDataType.TRACE.value, to_jsonable(chunk.payload), ok
        if include_trace:
            return ResponseDataType.TRACE.value, to_jsonable(chunk.payload), ok
        return None, None, ok

    if isinstance(chunk, OutputSchema):
        output_type = chunk.type
        if output_type == "output":
            return ResponseDataType.NODE_OUTPUT.value, to_jsonable(chunk.payload), ok
        if output_type == END_NODE_STREAM:
            return ResponseDataType.STREAM.value, to_jsonable(chunk.payload), ok
        if output_type == INTERACTION:
            return ResponseDataType.INPUT_REQUIRED.value, to_jsonable(chunk.payload), ok
        if output_type == "workflow_final":
            return ResponseDataType.RESULT.value, to_jsonable(chunk.payload), ok
        return ResponseDataType.STREAM.value, to_jsonable(chunk.payload), ok

    if isinstance(chunk, CustomSchema):
        return ResponseDataType.STREAM.value, to_jsonable(chunk.model_dump()), ok
    if isinstance(chunk, dict):
        return ResponseDataType.STREAM.value, to_jsonable(chunk), ok
    return ResponseDataType.STREAM.value, to_jsonable(chunk), ok


def _agent_output_payload_dict(chunk: Any) -> dict[str, Any]:
    from openjiuwen.core.session.stream import OutputSchema

    if not isinstance(chunk, OutputSchema):
        return {}
    payload = chunk.payload
    if isinstance(payload, dict):
        return payload
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        d = model_dump()
        return d if isinstance(d, dict) else {}
    return {}


def _agent_answer_chunk_to_type_payload_code(
    chunk: Any,
    payload_dict: dict[str, Any],
    ok: LowcodeApiResponseCode,
    fail: LowcodeApiResponseCode,
) -> tuple[str | None, Any, LowcodeApiResponseCode]:
    result_type = str(payload_dict.get("result_type") or "").strip()
    if result_type == "error":
        msg = payload_dict.get("message") or payload_dict.get("output") or ""
        return ResponseDataType.ERROR.value, {"message": str(msg)}, fail
    if result_type == "interrupt":
        return (
            ResponseDataType.INTERACTION.value,
            {
                "workflow_execution_state": to_jsonable(payload_dict.get("workflow_execution_state")),
                "component_ids": to_jsonable(payload_dict.get("component_ids", [])),
            },
            ok,
        )
    if not result_type:
        return ResponseDataType.UNKNOWN.value, to_jsonable(chunk.model_dump()), ok
    if result_type == "answer":
        out = payload_dict.get("output", "")
        return ResponseDataType.RESULT.value, {"output": out if isinstance(out, str) else str(out)}, ok
    return ResponseDataType.FORCE_FINISH.value, to_jsonable(chunk.model_dump()), ok


def _agent_chunk_to_type_payload_code(chunk: Any) -> tuple[str | None, Any, LowcodeApiResponseCode]:
    """将 agent stream chunk 映射为 data.type、payload 与业务 code。"""
    from openjiuwen.core.common.constants.constant import INTERACTION
    from openjiuwen.core.session.stream import CustomSchema, OutputSchema, TraceSchema
    from openjiuwen.core.session.tracer.handler import TracerHandlerName

    include_trace = _sse_include_trace()
    ok = LowcodeApiResponseCode.SUCCESS
    fail = LowcodeApiResponseCode.EXECUTION_FAILED

    if isinstance(chunk, TraceSchema):
        if chunk.type in (TracerHandlerName.TRACE_AGENT.value, TracerHandlerName.TRACER_WORKFLOW.value):
            if not include_trace:
                return None, None, ok
            return ResponseDataType.TRACE.value, to_jsonable(chunk.payload), ok
        if include_trace:
            return ResponseDataType.TRACE.value, to_jsonable(chunk.payload), ok
        return None, None, ok

    if isinstance(chunk, OutputSchema):
        output_type = chunk.type
        payload_dict = _agent_output_payload_dict(chunk)

        if output_type == INTERACTION:
            return ResponseDataType.INTERACTION.value, to_jsonable(chunk.payload), ok

        if output_type == "llm_reasoning":
            content = payload_dict.get("output") or payload_dict.get("content") or ""
            return ResponseDataType.STREAM.value, {"content": content, "stream_type": "llm_reasoning"}, ok

        if output_type == "llm_output":
            content = payload_dict.get("output") or payload_dict.get("content") or ""
            return ResponseDataType.STREAM.value, {"content": content, "stream_type": "llm_output"}, ok

        if output_type == "answer":
            return _agent_answer_chunk_to_type_payload_code(chunk, payload_dict, ok, fail)

        if output_type == "final" and payload_dict.get("error"):
            # 兼容 llm_controller 的错误帧。当前 ReActAgent 主路径不会写这种类型。
            return ResponseDataType.ERROR.value, {"message": str(payload_dict.get("message", ""))}, fail

        return ResponseDataType.STREAM.value, to_jsonable(chunk.model_dump()), ok

    if isinstance(chunk, CustomSchema):
        return ResponseDataType.STREAM.value, to_jsonable(chunk.model_dump()), ok
    if isinstance(chunk, dict):
        return ResponseDataType.STREAM.value, to_jsonable(chunk), ok
    return ResponseDataType.STREAM.value, to_jsonable(chunk), ok


async def _workflow_stream_event_source(chunk_stream: AsyncIterator[Any], timeout_seconds: float) -> AsyncIterator[str]:
    """将 workflow streaming iterator 转为 ResponseModel JSON 流。"""
    from openjiuwen.core.common.exception.errors import BaseError

    try:
        async with _optional_async_timeout(timeout_seconds):
            async for chunk in chunk_stream:
                data_type, payload, frame_code = _workflow_chunk_to_type_payload_code(chunk)
                if data_type is None:
                    continue
                yield ResponseModel(
                    code=int(frame_code),
                    message=_stream_frame_message(frame_code, payload),
                    data={"type": data_type, "payload": payload},
                ).model_dump_json()
    except asyncio.TimeoutError:
        _log.error(
            "workflow stream execution timeout after %.3fs",
            timeout_seconds,
            exc_info=True,
        )
        c = LowcodeApiResponseCode.EXECUTION_TIMEOUT
        yield _stream_error_event(c, message=c.format_message())
    except BaseError as e:
        _log.exception("workflow stream execution failed: %s", e)
        detail_code = int(getattr(e, "code", LowcodeApiResponseCode.INTERNAL_ERROR))
        msg = str(getattr(e, "message", "") or e)
        yield _stream_error_event(
            LowcodeApiResponseCode.EXECUTION_FAILED,
            message=msg,
            payload={"detail_code": detail_code},
        )
    except asyncio.CancelledError:
        c = LowcodeApiResponseCode.EXECUTION_CANCELLED
        yield _stream_error_event(c)
        raise
    except WorkflowLlmApiKeyMissingError as e:
        _log.exception("workflow stream missing LLM API key: %s", e)
        c = LowcodeApiResponseCode.LLM_API_KEY_MISSING
        yield _stream_error_event(c, message=str(e))
    except Exception as e:
        _log.exception("workflow stream internal error: %s", e)
        c = LowcodeApiResponseCode.INTERNAL_ERROR
        yield _stream_error_event(c, message=str(e))


async def _agent_stream_event_source(chunk_stream: AsyncIterator[Any], timeout_seconds: float) -> AsyncIterator[str]:
    """将 agent streaming iterator 转为 ResponseModel JSON 流。"""
    from openjiuwen.core.common.exception.errors import BaseError

    try:
        async with _optional_async_timeout(timeout_seconds):
            async for chunk in chunk_stream:
                data_type, payload, frame_code = _agent_chunk_to_type_payload_code(chunk)
                if data_type is None:
                    continue
                yield ResponseModel(
                    code=int(frame_code),
                    message=_stream_frame_message(frame_code, payload),
                    data={"type": data_type, "payload": payload},
                ).model_dump_json()
    except asyncio.TimeoutError:
        _log.error(
            "agent stream execution timeout after %.3fs",
            timeout_seconds,
            exc_info=True,
        )
        c = LowcodeApiResponseCode.EXECUTION_TIMEOUT
        yield _stream_error_event(c, message=c.format_message())
    except BaseError as e:
        _log.exception("agent stream execution failed: %s", e)
        detail_code = int(getattr(e, "code", LowcodeApiResponseCode.INTERNAL_ERROR))
        msg = str(getattr(e, "message", "") or e)
        yield _stream_error_event(
            LowcodeApiResponseCode.EXECUTION_FAILED,
            message=msg,
            payload={"detail_code": detail_code},
        )
    except asyncio.CancelledError:
        c = LowcodeApiResponseCode.EXECUTION_CANCELLED
        yield _stream_error_event(c)
        raise
    except WorkflowLlmApiKeyMissingError as e:
        _log.exception("agent stream missing LLM API key: %s", e)
        c = LowcodeApiResponseCode.LLM_API_KEY_MISSING
        yield _stream_error_event(c, message=str(e))
    except Exception as e:
        _log.exception("agent stream internal error: %s", e)
        c = LowcodeApiResponseCode.INTERNAL_ERROR
        yield _stream_error_event(c, message=str(e))


async def validation_error_stream_events(exc: RequestValidationError) -> AsyncIterator[str]:
    """SSE 路由的请求体校验失败也返回 ResponseModel 事件体。"""
    yield _stream_error_event(
        LowcodeApiResponseCode.INVALID_REQUEST,
        message=LowcodeApiResponseCode.INVALID_REQUEST.default_message,
        payload={"errors": exc.errors()},
    )


async def execute_stream_event_source(body: Any) -> AsyncIterator[str]:
    """FastAPI 路由层入口。"""
    try:
        await ensure_runtime_ready()
    except Exception as e:
        yield _stream_error_event(LowcodeApiResponseCode.SERVICE_UNAVAILABLE, message=str(e))
        return

    try:
        prepared = await prepare_execution_request(body)
    except ExecutionPrepareError as exc:
        yield _stream_error_event(exc.code, message=exc.message)
        return

    if prepared.executable_kind == "workflow":
        try:
            from .workflow_ir_builder import build_core_workflow_from_ir_file

            workflow = await build_core_workflow_from_ir_file(
                prepared.ir_local_json_path,
                space_id=prepared.space_id,
                current_user=prepared.current_user,
            )
        except WorkflowLlmApiKeyMissingError as e:
            yield _stream_error_event(LowcodeApiResponseCode.LLM_API_KEY_MISSING, message=str(e))
            return
        except Exception as e:
            yield _stream_error_event(LowcodeApiResponseCode.IR_LOAD_FAILED, message=str(e))
            return

        conversation_id = str(prepared.session_id or "")
        context_id = _workflow_context_id(workflow)
        had_interaction = False

        from openjiuwen.core.common.constants.constant import INTERACTION
        from openjiuwen.core.session.stream import OutputSchema

        try:
            async with _ctx_store.conversation_lock(conversation_id=conversation_id):
                context = await _ctx_store.load_context(
                    conversation_id=conversation_id,
                    context_id=context_id,
                    config=ContextEngineConfig(),
                )
                await _append_user_input_message_if_needed(context, prepared.inputs_obj)

                async def _wrapped_chunks() -> AsyncIterator[Any]:
                    nonlocal had_interaction
                    inner = Runner.run_workflow_streaming(
                        workflow=workflow,
                        inputs=prepared.inputs_obj,
                        session=prepared.session_id,
                        context=context,
                    )
                    async for ch in inner:
                        if isinstance(ch, OutputSchema) and ch.type == INTERACTION:
                            had_interaction = True
                            await _ctx_store.save_on_interaction(
                                conversation_id=conversation_id,
                                context_id=context_id,
                                context=context,
                            )
                        yield ch

                async for line in _workflow_stream_event_source(_wrapped_chunks(), prepared.timeout_seconds):
                    yield line

                if not had_interaction:
                    await _ctx_store.delete(conversation_id=conversation_id, context_id=context_id)
        except Exception as e:
            _log.exception("workflow stream failed: %s", e)
            if conversation_id:
                await _ctx_store.delete(conversation_id=conversation_id, context_id=context_id)
            # Keep existing error mapping behavior.
            yield _stream_error_event(LowcodeApiResponseCode.INTERNAL_ERROR, message=str(e))
        return

    try:
        react_agent = await build_react_agent_from_ir(prepared.ir_local_json_path, prepared.current_user)
    except Exception as e:
        yield _stream_error_event(LowcodeApiResponseCode.IR_LOAD_FAILED, message=str(e))
        return

    chunk_iterator = Runner.run_agent_streaming(
        agent=react_agent,
        inputs=prepared.inputs_obj,
        session=prepared.session_id,
    )
    async for line in _agent_stream_event_source(chunk_iterator, prepared.timeout_seconds):
        yield line

