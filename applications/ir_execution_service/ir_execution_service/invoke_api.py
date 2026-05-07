# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""execute_invoke 的业务逻辑与返回体转换。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.responses import JSONResponse

from openjiuwen.core.runner import Runner
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_studio.schemas import ResponseModel

from .dsl_workflow_dependency_loader import WorkflowLlmApiKeyMissingError
from .react_agent_builder import build_react_agent_from_ir_dict
from .runtime_support.context_persistence import RedisContextPersistence
from .runtime_support.http_response_contract import (
    LowcodeApiResponseCode,
    ResponseDataType,
    build_error_response_model,
    to_jsonable,
)
from .runtime_support.execution_request import ExecutionPrepareError, prepare_execution_request
from .runtime_support.runtime_bootstrap import ensure_runtime_ready
from .runtime_support.workflow_context_helpers import append_user_input_message_if_needed, stable_workflow_context_id

JSON_MEDIA_TYPE = "application/json; charset=utf-8"

_log = get_logger(__name__)

_ctx_store = RedisContextPersistence()


def _json_response(model: ResponseModel) -> JSONResponse:
    # HTTP 始终返回 200，由 body.code 表达业务状态。
    return JSONResponse(model.model_dump(), media_type=JSON_MEDIA_TYPE)


def _invoke_exception_to_model(exc: Exception) -> ResponseModel:
    """将 invoke 路径异常转换为 error 响应。"""
    from openjiuwen.core.common.exception.errors import BaseError

    if isinstance(exc, WorkflowLlmApiKeyMissingError):
        c = LowcodeApiResponseCode.LLM_API_KEY_MISSING
        return build_error_response_model(c, message=str(exc))
    if isinstance(exc, asyncio.TimeoutError):
        code = LowcodeApiResponseCode.EXECUTION_TIMEOUT
        return build_error_response_model(code, message=code.format_message())
    if isinstance(exc, BaseError):
        detail_code = int(getattr(exc, "code", LowcodeApiResponseCode.INTERNAL_ERROR))
        msg = str(getattr(exc, "message", "") or exc)
        return build_error_response_model(
            LowcodeApiResponseCode.EXECUTION_FAILED,
            message=msg,
            payload={"detail_code": detail_code},
        )
    if isinstance(exc, ValueError):
        msg = str(exc)
        return build_error_response_model(LowcodeApiResponseCode.INVALID_PARAM, message=msg)
    msg = str(exc)
    return build_error_response_model(LowcodeApiResponseCode.INTERNAL_ERROR, message=msg)


def _agent_invoke_result_to_model(result: Any) -> ResponseModel:
    """将 agent invoke 返回值映射为 ResponseModel。"""
    ok = LowcodeApiResponseCode.SUCCESS

    if isinstance(result, dict):
        result_type = str(result.get("result_type") or "").strip()

        if result_type == "answer":
            payload = {"output": str(result.get("output", "") or "")}
            return ResponseModel(code=int(ok), message=ok.default_message, data={"type": "result", "payload": payload})

        if result_type == "error":
            raw_msg = result.get("message")
            if raw_msg is None:
                raw_msg = result.get("output")
            msg = str(raw_msg or "")
            code = LowcodeApiResponseCode.EXECUTION_FAILED
            return ResponseModel(
                code=int(code),
                message=msg.strip() or code.default_message,
                data={"type": "error", "payload": {"message": msg}},
            )

        if result_type == "interrupt":
            payload = {
                "workflow_execution_state": to_jsonable(result.get("workflow_execution_state")),
                "component_ids": to_jsonable(result.get("component_ids", [])),
            }
            return ResponseModel(
                code=int(ok),
                message=ok.default_message,
                data={"type": "interaction", "payload": payload},
            )

        if result_type:
            return ResponseModel(
                code=int(ok),
                message=ok.default_message,
                data={
                    "type": ResponseDataType.FORCE_FINISH.value,
                    "payload": to_jsonable(result),
                },
            )

        return ResponseModel(
            code=int(ok),
            message=ok.default_message,
            data={"type": ResponseDataType.UNKNOWN.value, "payload": to_jsonable(result)},
        )

    return ResponseModel(
        code=int(ok),
        message=ok.default_message,
        data={"type": "result", "payload": to_jsonable(result)},
    )


def _workflow_invoke_result_to_model(result: Any) -> ResponseModel:
    """将 workflow invoke 返回值映射为 ResponseModel。"""
    from openjiuwen.core.common.constants.constant import INTERACTION
    from openjiuwen.core.session.stream import OutputSchema
    from openjiuwen.core.workflow import WorkflowExecutionState, WorkflowOutput

    ok = LowcodeApiResponseCode.SUCCESS

    if isinstance(result, WorkflowOutput):
        state = getattr(result, "state", None)
        inner = getattr(result, "result", None)
        if state == WorkflowExecutionState.INPUT_REQUIRED:
            result = inner if inner is not None else []
        elif state == WorkflowExecutionState.COMPLETED:
            result = inner
        else:
            result = inner

    if isinstance(result, dict):
        return ResponseModel(
            code=int(ok),
            message=ok.default_message,
            data={"type": "result", "payload": {"data": to_jsonable(result)}},
        )

    if isinstance(result, list):
        for item in result:
            output_type = None
            payload_obj: Any = None
            if isinstance(item, OutputSchema):
                output_type = item.type
                payload_obj = item.payload
            elif isinstance(item, dict):
                output_type = item.get("type")
                payload_obj = item.get("payload")

            if output_type == INTERACTION:
                payload_json = to_jsonable(payload_obj)
                interaction_id = payload_json.get("id") if isinstance(payload_json, dict) else None
                interaction_value = payload_json.get("value") if isinstance(payload_json, dict) else None
                return ResponseModel(
                    code=int(ok),
                    message=ok.default_message,
                    data={"type": "interaction", "payload": {"id": interaction_id, "value": interaction_value}},
                )

        c = LowcodeApiResponseCode.INVOKE_NOT_SUPPORTED
        err_payload = {"error_code": int(c), "error_message": c.default_message}
        return ResponseModel(
            code=int(c),
            message=c.default_message,
            data={"type": "error", "payload": err_payload},
        )

    return ResponseModel(
        code=int(ok),
        message=ok.default_message,
        data={"type": "result", "payload": {"data": to_jsonable(result)}},
    )


async def handle_execute_invoke(body: Any) -> JSONResponse:
    """FastAPI 路由层入口。"""
    try:
        await ensure_runtime_ready()
    except Exception as e:
        _log.exception("service unavailable during startup: %s", e)
        return _json_response(build_error_response_model(LowcodeApiResponseCode.SERVICE_UNAVAILABLE, message=str(e)))

    try:
        prepared = await prepare_execution_request(body)
    except ExecutionPrepareError as exc:
        return _json_response(build_error_response_model(exc.code, message=exc.message))

    if prepared.executable_kind == "workflow":
        session_id = str(prepared.session_id or "").strip()
        if not session_id:
            c = LowcodeApiResponseCode.MISSING_PARAM
            return _json_response(
                build_error_response_model(c, message=c.format_message(field="conversation_id"))
            )

        from .workflow_ir_builder import build_core_workflow_from_ir_dict
        from openjiuwen.core.workflow import WorkflowExecutionState, WorkflowOutput

        try:
            workflow = await build_core_workflow_from_ir_dict(
                prepared.ir_root,
                space_id=prepared.space_id,
                current_user=prepared.current_user,
            )
        except WorkflowLlmApiKeyMissingError as e:
            c = LowcodeApiResponseCode.LLM_API_KEY_MISSING
            return _json_response(build_error_response_model(c, message=str(e)))
        except Exception as e:
            return _json_response(build_error_response_model(LowcodeApiResponseCode.IR_LOAD_FAILED, message=str(e)))

        conversation_id = session_id
        context_id = stable_workflow_context_id(workflow)

        try:
            async with _ctx_store.conversation_lock(conversation_id=conversation_id):
                context = await _ctx_store.load_context(
                    conversation_id=conversation_id,
                    context_id=context_id,
                    config=ContextEngineConfig(),
                )
                await append_user_input_message_if_needed(context, prepared.inputs_obj)

                wf_output = await asyncio.wait_for(
                    Runner.run_workflow(
                        workflow=workflow,
                        inputs=prepared.inputs_obj,
                        session=prepared.session_id,
                        context=context,
                    ),
                    timeout=prepared.timeout_seconds,
                )

                if isinstance(wf_output, WorkflowOutput) and wf_output.state == WorkflowExecutionState.INPUT_REQUIRED:
                    await _ctx_store.save_on_interaction(
                        conversation_id=conversation_id,
                        context_id=context_id,
                        context=context,
                    )
                else:
                    await _ctx_store.delete(conversation_id=conversation_id, context_id=context_id)
        except Exception as e:
            _log.exception("workflow invoke failed: %s", e)
            if conversation_id:
                await _ctx_store.delete(conversation_id=conversation_id, context_id=context_id)
            return _json_response(_invoke_exception_to_model(e))

        return _json_response(_workflow_invoke_result_to_model(wf_output))

    try:
        react_agent = await build_react_agent_from_ir_dict(prepared.ir_root, prepared.current_user)
    except Exception as e:
        return _json_response(build_error_response_model(LowcodeApiResponseCode.IR_LOAD_FAILED, message=str(e)))

    try:
        agent_output = await asyncio.wait_for(
            Runner.run_agent(agent=react_agent, inputs=prepared.inputs_obj, session=prepared.session_id),
            timeout=prepared.timeout_seconds,
        )
    except Exception as e:
        _log.exception("agent invoke failed: %s", e)
        return _json_response(_invoke_exception_to_model(e))

    return _json_response(_agent_invoke_result_to_model(agent_output))

