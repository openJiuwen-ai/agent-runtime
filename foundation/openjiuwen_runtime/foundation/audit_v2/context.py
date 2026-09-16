# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Request-scoped audit context — ContextVars + bind API.

SDK 侧仅提供 ContextVar 缺省回退；调用方若有跨任务场景，
应在打点时显式传参覆盖。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# 请求上下文字段 → ContextVar（变量名加 _cv 前缀，避免与函数参数同名遮蔽）
_CONTEXT_VARS: dict[str, ContextVar[Optional[str]]] = {
    "user_id": ContextVar("audit_user_id", default=None),
    "session_id": ContextVar("audit_session_id", default=None),
    "request_id": ContextVar("audit_request_id", default=None),
    "group_id": ContextVar("audit_group_id", default=None),
    "bot_id": ContextVar("audit_bot_id", default=None),
    "channel_id": ContextVar("audit_channel_id", default=None),
    "srcip": ContextVar("audit_srcip", default=None),
    "dstip": ContextVar("audit_dstip", default=None),
}


def bind_request_context(
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    group_id: Optional[str] = None,
    bot_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    srcip: Optional[str] = None,
    dstip: Optional[str] = None,
) -> dict:
    """Bind request-scoped ids for the current task; returns tokens for reset."""
    values = {
        "user_id": user_id,
        "session_id": session_id,
        "request_id": request_id,
        "group_id": group_id,
        "bot_id": bot_id,
        "channel_id": channel_id,
        "srcip": srcip,
        "dstip": dstip,
    }
    tokens: dict[str, object] = {}
    for field, value in values.items():
        if value is not None:
            tokens[field] = _CONTEXT_VARS[field].set(value)
    return tokens


def reset_request_context(tokens: dict) -> None:
    """Reset all tokens returned by :func:`bind_request_context`."""
    for field, token in tokens.items():
        _CONTEXT_VARS[field].reset(token)


def snapshot() -> dict[str, str]:
    """Non-empty context values, keyed by field name."""
    result: dict[str, str] = {}
    for field, var in _CONTEXT_VARS.items():
        value = var.get()
        if value:
            result[field] = str(value)
    return result


# ---------------------------------------------------------------------------
# Session-keyed routing registry — task-independent fallback.
#
# 长驻交互任务的 ContextVar 快照早于当前请求的 bind，打点时按 session_id
# 回退查最新一次绑定（最新请求胜出，容量封顶防膨胀）。
# ---------------------------------------------------------------------------

_ROUTING_BY_SESSION: dict[str, dict[str, str]] = {}
_ROUTING_BY_SESSION_CAP = 512


def bind_routing(
    *,
    session_id: str = "",
    user_id: str = "",
    request_id: str = "",
    group_id: str = "",
    bot_id: str = "",
    channel_id: str = "",
) -> None:
    """Bind request routing ids into ContextVars AND the session registry."""
    bind_request_context(
        user_id=user_id or None,
        session_id=session_id or None,
        request_id=request_id or None,
        group_id=group_id or None,
        bot_id=bot_id or None,
        channel_id=channel_id or None,
    )
    if session_id:
        ctx = {
            "session_id": session_id,
            "user_id": user_id,
            "request_id": request_id,
            "group_id": group_id,
            "bot_id": bot_id,
            "channel_id": channel_id,
        }
        if len(_ROUTING_BY_SESSION) >= _ROUTING_BY_SESSION_CAP and session_id not in _ROUTING_BY_SESSION:
            for stale in list(_ROUTING_BY_SESSION)[: len(_ROUTING_BY_SESSION) // 2]:
                _ROUTING_BY_SESSION.pop(stale, None)
        _ROUTING_BY_SESSION[session_id] = ctx


def lookup_routing(session_id: str) -> dict[str, str]:
    """Latest routing snapshot bound for ``session_id``. """
    if not session_id:
        return {}
    return dict(_ROUTING_BY_SESSION.get(session_id, {}))
