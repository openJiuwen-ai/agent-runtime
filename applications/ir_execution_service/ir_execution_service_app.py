#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""IR 执行服务 HTTP 入口。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse
from starlette.requests import Request

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.append(str(_APP_DIR))

from runtime_support.runtime_env_prepare import prepare_runtime_environment
from runtime_support.error_logging import setup_error_file_logging

prepare_runtime_environment()
setup_error_file_logging()

# Workflow 默认超时较短，复杂 DSL 续跑时容易误判超时。
os.environ.setdefault("WORKFLOW_EXECUTE_TIMEOUT", "300")

from openjiuwen.core.runner import Runner
from openjiuwen_runtime.service.app.base_app import BaseApp

from runtime_support.http_response_contract import LowcodeApiResponseCode, build_error_response_model
from runtime_support.runtime_bootstrap import ensure_runtime_ready

_JSON_MEDIA_TYPE = "application/json; charset=utf-8"


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
        description="OBS 桶内对象键（Object Key），对应文件会缓存到 LOWCODE_IR_DOWNLOAD_DIR。",
    )
    inputs: str
    timeout_ms: int = Field(120_000, ge=1)


class IrExecutionServiceApp(BaseApp):
    """BaseApp 上挂载自定义 POST 路由，不继承 AgentApp。"""

    def __init__(self) -> None:
        super().__init__(
            app_name="IrExecutionService",
            app_description="IR JSON -> ReActAgent or Workflow, Runner + memory/checkpointer",
            version="0.1.0",
        )

        @self.app.exception_handler(RequestValidationError)
        async def _validation_on_stream_routes(request: Request, exc: RequestValidationError):
            path = (request.url.path or "").rstrip("/")
            if path.endswith("/execute_stream"):
                from stream_api import validation_error_stream_events

                return EventSourceResponse(validation_error_stream_events(exc))
            if path.endswith("/execute_invoke"):
                return _invalid_request_json_response(exc)
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

        @self.app.post("/execute_stream")
        async def execute_stream(body: IrQueryBody):
            from stream_api import execute_stream_event_source

            return EventSourceResponse(execute_stream_event_source(body))

        @self.app.post("/execute_invoke")
        async def execute_invoke(body: IrQueryBody):
            from invoke_api import handle_execute_invoke

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
