# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""低码 Runner HTTP 响应体约定。"""

from __future__ import annotations

from enum import Enum, IntEnum
from typing import Any

from openjiuwen_studio.schemas import ResponseModel


class LowcodeApiResponseCode(IntEnum):
    """低码 Runner HTTP 接口使用的数值 code 及默认英文 message。"""

    def __new__(cls, value: int, default_message: str):
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.default_message = default_message
        return obj

    # 成功
    SUCCESS = (0, "success")

    # 1xxx - 参数错误（客户端问题）
    INVALID_REQUEST = (1001, "invalid request body")
    MISSING_PARAM = (1002, "missing required param: {field}")
    INVALID_PARAM = (1003, "invalid param: {field}")
    INVALID_IR_PATH = (1004, "invalid ir_path format")
    INVALID_INPUTS = (1005, "inputs is not valid json string")
    INVALID_TIMEOUT = (1006, "timeout_ms must be positive integer")

    # 2xxx - 资源错误（加载、找不到）
    IR_NOT_FOUND = (2001, "ir not found: {ir_path}")
    IR_DOWNLOAD_FAILED = (2002, "failed to download ir")
    IR_INVALID = (2003, "invalid ir format")
    IR_LOAD_FAILED = (2004, "failed to load ir")
    SESSION_LOAD_FAILED = (2005, "failed to load session")
    LLM_API_KEY_MISSING = (2006, "LLM api_key missing: set env {env_var} or api_key in DSL")

    # 3xxx - 执行错误（运行时）
    EXECUTION_TIMEOUT = (3001, "execution timeout")
    EXECUTION_FAILED = (3002, "agent execution failed")
    EXECUTION_CANCELLED = (3003, "execution cancelled")
    OUTPUT_INVALID = (3004, "agent output invalid")
    INVOKE_NOT_SUPPORTED = (3005, "invoke not supported")

    # 4xxx - 系统限制（预留，当前不可用）
    RATE_LIMITED = (4001, "rate limit exceeded")
    CONCURRENCY_LIMITED = (4002, "too many concurrent executions")
    QUEUE_FULL = (4003, "execution queue full")
    RESOURCE_EXHAUSTED = (4004, "server resource exhausted")

    # 5xxx - 内部错误（平台问题）
    INTERNAL_ERROR = (5001, "internal server error")
    SERVICE_UNAVAILABLE = (5002, "service temporarily unavailable")
    DEPENDENCY_ERROR = (5003, "dependency service error")

    def format_message(self, **kwargs: str) -> str:
        """使用 default_message 做 str.format；无占位符时忽略 kwargs。"""
        if not kwargs:
            return self.default_message
        return self.default_message.format(**kwargs)


class ResponseDataType(str, Enum):
    """ResponseModel.data.type 取值（字符串枚举）。"""

    ERROR = "error"
    STREAM = "stream"
    RESULT = "result"
    TRACE = "trace"
    NODE_OUTPUT = "node_output"
    INPUT_REQUIRED = "input_required"
    INTERACTION = "interaction"
    FORCE_FINISH = "force_finish"
    UNKNOWN = "unknown"


def build_error_response_model(
    code: LowcodeApiResponseCode,
    *,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> ResponseModel:
    msg = message if message is not None else code.default_message
    body: dict[str, Any] = {"message": msg}
    if payload:
        body.update(payload)
    return ResponseModel(
        code=int(code),
        message=msg,
        data={"type": ResponseDataType.ERROR.value, "payload": body},
    )


def to_jsonable(obj: Any) -> Any:
    """将 core 或 studio 对象转为可 JSON 序列化的 dict、list 或标量。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()
    if hasattr(obj, "__dict__"):
        return {k: to_jsonable(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)

