# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""A separately maintained handler module included by multi_handler_app.py."""

from pydantic import BaseModel, Field

from openjiuwen_runtime.service import HandlerRegistry


class UppercaseInput(BaseModel):
    """Input owned by this extension module."""

    text: str = Field(min_length=1, examples=["hello"])


class UppercaseOutput(BaseModel):
    """Output owned by this extension module."""

    text: str
    authenticated_user: str


custom_handlers = HandlerRegistry()


@custom_handlers.handle(
    "custom.uppercase",
    request_model=UppercaseInput,
    response_model=UppercaseOutput,
    summary="Uppercase text",
    description="A handler loaded from a separate, reusable handler module.",
    tags=["extension"],
)
async def uppercase(ctx, env):
    return {
        "text": env.rawdata.text.upper(),
        "authenticated_user": ctx.principal["username"],
    }
