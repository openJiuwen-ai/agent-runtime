# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""IR 执行服务 HTTP 入口。"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_APP_DIR = Path(__file__).resolve().parent
_APP_ROOT = _APP_DIR.parent

try:
    from dotenv import load_dotenv

    load_dotenv(_APP_ROOT / ".env", override=False)
except ImportError:
    pass

from openjiuwen.core.runner import Runner
from openjiuwen.core.common.logging import set_session_id
from openjiuwen_runtime.service.app.base_app import BaseApp

from .runtime_support.runtime_env_prepare import prepare_runtime_environment
from .runtime_support.error_logging import setup_error_file_logging
from .runtime_support.alarm_logger import (
    init_alarm_logger_from_env,
    install_core_alarm_sink,
    install_runner_tool_alarm_callbacks,
)
from .runtime_support.core_log_bridge import install_core_log_bridge
from .runtime_support.interface_logger import (
    init_interface_logger_from_env,
    install_core_interface_sink,
    install_runner_tool_interface_callbacks,
    log_server,
    set_request_context,
)

prepare_runtime_environment()
setup_error_file_logging()
init_alarm_logger_from_env()
install_core_log_bridge()
install_core_alarm_sink()
install_runner_tool_alarm_callbacks()
init_interface_logger_from_env()
install_core_interface_sink()
install_runner_tool_interface_callbacks()

# Workflow 默认超时较短，复杂 DSL 续跑时容易误判超时。
os.environ.setdefault("WORKFLOW_EXECUTE_TIMEOUT", "300")

from .runtime_support.http_response_contract import (
    LowcodeApiResponseCode,
    build_error_response_model,
)
from .runtime_support.runtime_bootstrap import ensure_runtime_ready

_JSON_MEDIA_TYPE = "application/json; charset=utf-8"
_PY_LOG = logging.getLogger(__name__)


async def _response_body_bytes(resp: Response) -> bytes | None:
    """取响应正文。注意 BaseHTTPMiddleware.call_next 返回的是流式包装体，通常没有物化的 .body。"""
    body_iter = getattr(resp, "body_iterator", None)
    if body_iter is not None:
        parts: list[bytes] = []
        async for chunk in body_iter:
            if not chunk:
                continue
            if isinstance(chunk, memoryview):
                parts.append(chunk.tobytes())
            elif isinstance(chunk, (bytes, bytearray)):
                parts.append(bytes(chunk))
            else:
                parts.append(str(chunk).encode(resp.charset))
        return b"".join(parts)
    raw = getattr(resp, "body", None)
    if isinstance(raw, memoryview) and raw:
        return raw.tobytes()
    if isinstance(raw, (bytes, bytearray)) and raw:
        return bytes(raw)
    return None


def _invalid_request_json_response(exc: RequestValidationError) -> JSONResponse:
    body = build_error_response_model(
        LowcodeApiResponseCode.INVALID_REQUEST,
        message=LowcodeApiResponseCode.INVALID_REQUEST.default_message,
        payload={"errors": exc.errors()},
    )
    return JSONResponse(body.model_dump(), media_type=_JSON_MEDIA_TYPE)


class IrQueryBody(BaseModel):
    user_id: str
    conversation_id: str
    ir_path: str = Field(
        ...,
        description="OBS 桶内对象键（Object Key）；正文经进程内缓存与可选 Redis 缓存，加速读取，不落盘。",
    )
    inputs: str
    timeout_ms: int = Field(120_000, ge=1)


class IrExecutionServiceApp(BaseApp):
    """BaseApp 上挂载自定义 POST 路由，不继承 AgentApp。"""

    def __init__(self) -> None:
        super().__init__(
            app_name="IrExecutionService",
            app_description="面向低代码的工作流 IR 执行 HTTP 服务",
            version=(os.environ.get("LOWCODE_IR_EXECUTION_SERVICE_VERSION") or "").strip(),
        )

        class _DfxMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                # Only record DFX logs for the two public endpoints.
                path = (request.url.path or "").rstrip("/")
                if not (path.endswith("/execute_invoke") or path.endswith("/execute_stream")):
                    return await call_next(request)

                import uuid

                request_id = uuid.uuid4().hex
                source_ip = getattr(getattr(request, "client", None), "host", "") or ""
                set_request_context(request_id=request_id, source_ip=source_ip)
                set_session_id(request_id)

                interface_name = "execute_invoke" if path.endswith("/execute_invoke") else "execute_stream"
                t0 = time.perf_counter()
                try:
                    resp = await call_next(request)
                except Exception as e:
                    log_server(
                        interface_name=interface_name,
                        cost_ms=(time.perf_counter() - t0) * 1000.0,
                        ok=False,
                        return_code=int(LowcodeApiResponseCode.INTERNAL_ERROR),
                        return_info=str(e),
                        source_ip=source_ip,
                        add_info={"path": path},
                    )
                    raise

                # For invoke: BaseHTTPMiddleware 下 call_next 得到的是流式包装响应，无物化 .body，需先读全再解析。
                if interface_name == "execute_invoke":
                    had_stream_wrapper = getattr(resp, "body_iterator", None) is not None
                    code = int(LowcodeApiResponseCode.SUCCESS)
                    msg = str(LowcodeApiResponseCode.SUCCESS.default_message)
                    ok = True
                    parsed_ok = False
                    raw_bytes: bytes | None = None
                    try:
                        raw_bytes = await _response_body_bytes(resp)
                        if raw_bytes:
                            parsed = json.loads(raw_bytes.decode("utf-8"))
                            if isinstance(parsed, dict):
                                code = int(parsed.get("code", 0))
                                msg = str(parsed.get("message", "") or "")
                                ok = code == 0
                                parsed_ok = True
                    except Exception as exc:
                        _PY_LOG.warning("failed to parse execute_invoke response body: %s", exc)
                    if not parsed_ok:
                        ok = False
                        code = int(LowcodeApiResponseCode.INTERNAL_ERROR)
                        msg = "parse failed"
                    log_server(
                        interface_name=interface_name,
                        cost_ms=(time.perf_counter() - t0) * 1000.0,
                        ok=ok,
                        return_code=code,
                        return_info=msg,
                        source_ip=source_ip,
                        add_info={"path": path},
                    )
                    if had_stream_wrapper:
                        return Response(
                            content=raw_bytes or b"",
                            status_code=resp.status_code,
                            headers=resp.headers,
                            media_type=resp.media_type,
                        )
                # Stream path end-state is logged inside stream generator (see stream_api).
                return resp

        self.app.add_middleware(_DfxMiddleware)

        @self.app.exception_handler(RequestValidationError)
        async def _validation_on_stream_routes(request: Request, exc: RequestValidationError):
            path = (request.url.path or "").rstrip("/")
            if path.endswith("/execute_stream"):
                from .stream_api import validation_error_stream_events

                return EventSourceResponse(validation_error_stream_events(exc))
            if path.endswith("/execute_invoke"):
                return _invalid_request_json_response(exc)
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

        @self.app.post("/execute_stream")
        async def execute_stream(body: IrQueryBody):
            from .stream_api import execute_stream_event_source

            return EventSourceResponse(execute_stream_event_source(body))

        @self.app.post("/execute_invoke")
        async def execute_invoke(body: IrQueryBody):
            from .invoke_api import handle_execute_invoke

            return await handle_execute_invoke(body)


runner = IrExecutionServiceApp()


@runner.init
async def _startup() -> None:
    await ensure_runtime_ready()
    await Runner.start()


@runner.shutdown
async def _shutdown() -> None:
    await Runner.stop()


app = runner.app


if __name__ == "__main__":
    runner.run()
