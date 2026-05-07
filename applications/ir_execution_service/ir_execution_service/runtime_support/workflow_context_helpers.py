# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Workflow SessionModelContext 相关的轻量辅助函数。

说明：
- 这些函数被 invoke/stream 两条路径共享，避免两处拷贝漂移。
- 业务语义保持不变：尽量把当前用户输入追加进 SessionModelContext（best-effort）。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.log import get_logger

_LOG = get_logger(__name__)


def stable_workflow_context_id(workflow: Any) -> str:
    """Stable context_id for (workflow, version)."""
    card = getattr(workflow, "card", None)
    wf_id = getattr(card, "id", None) if card is not None else None
    wf_ver = getattr(card, "version", None) if card is not None else None
    if wf_id and wf_ver:
        return f"{wf_id}_{wf_ver}"
    if wf_id:
        return str(wf_id)
    return "workflow"


async def append_user_input_message_if_needed(context: Any, inputs_obj: Any) -> None:
    """Append current user input into context (best-effort)."""
    from openjiuwen.core.foundation.llm import UserMessage
    from openjiuwen.core.session import InteractiveInput

    content: Any = None
    if isinstance(inputs_obj, dict):
        if "query" in inputs_obj:
            content = inputs_obj.get("query")
    elif isinstance(inputs_obj, InteractiveInput):
        if inputs_obj.user_inputs:
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
        _LOG.debug("append user input to context failed", exc_info=True)
