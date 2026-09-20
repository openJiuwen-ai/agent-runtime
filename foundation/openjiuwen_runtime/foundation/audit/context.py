# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""请求级 ContextVar + session 路由注册表。"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Optional


_session_id: ContextVar[str | None] = ContextVar("audit_session_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("audit_request_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("audit_user_id", default=None)
_src_ip: ContextVar[str | None] = ContextVar("audit_src_ip", default=None)
_dst_ip: ContextVar[str | None] = ContextVar("audit_dst_ip", default=None)
_group_id: ContextVar[str | None] = ContextVar("audit_group_id", default=None)
_bot_id: ContextVar[str | None] = ContextVar("audit_bot_id", default=None)
_channel_id: ContextVar[str | None] = ContextVar("audit_channel_id", default=None)

_ROUTING_BY_SESSION: dict[str, dict[str, str]] = {}
_ROUTING_BY_SESSION_CAP = 512


@dataclass(frozen=True)
class AuditContextTokens:
    session_id: Token[str | None] | None = None
    request_id: Token[str | None] | None = None
    user_id: Token[str | None] | None = None
    src_ip: Token[str | None] | None = None
    dst_ip: Token[str | None] | None = None
    group_id: Token[str | None] | None = None
    bot_id: Token[str | None] | None = None
    channel_id: Token[str | None] | None = None


def bind_audit_context(
    *,
    session_id: str | None = None,
    request_id: str | None = None,
    user_id: str | None = None,
    src_ip: str | None = None,
    dst_ip: str | None = None,
    group_id: str | None = None,
    bot_id: str | None = None,
    channel_id: str | None = None,
) -> AuditContextTokens:
    """在请求入口写入 ContextVar；返回 tokens 供 finally 中 reset。"""
    return AuditContextTokens(
        session_id=_session_id.set(session_id) if session_id is not None else None,
        request_id=_request_id.set(request_id) if request_id is not None else None,
        user_id=_user_id.set(user_id) if user_id is not None else None,
        src_ip=_src_ip.set(src_ip) if src_ip is not None else None,
        dst_ip=_dst_ip.set(dst_ip) if dst_ip is not None else None,
        group_id=_group_id.set(group_id) if group_id is not None else None,
        bot_id=_bot_id.set(bot_id) if bot_id is not None else None,
        channel_id=_channel_id.set(channel_id) if channel_id is not None else None,
    )


def reset_audit_context(tokens: AuditContextTokens | None) -> None:
    if tokens is None:
        return
    if tokens.session_id is not None:
        _session_id.reset(tokens.session_id)
    if tokens.request_id is not None:
        _request_id.reset(tokens.request_id)
    if tokens.user_id is not None:
        _user_id.reset(tokens.user_id)
    if tokens.src_ip is not None:
        _src_ip.reset(tokens.src_ip)
    if tokens.dst_ip is not None:
        _dst_ip.reset(tokens.dst_ip)
    if tokens.group_id is not None:
        _group_id.reset(tokens.group_id)
    if tokens.bot_id is not None:
        _bot_id.reset(tokens.bot_id)
    if tokens.channel_id is not None:
        _channel_id.reset(tokens.channel_id)


def clear_audit_context() -> None:
    """测试辅助：将全部 ContextVar 置回 None。"""
    _session_id.set(None)
    _request_id.set(None)
    _user_id.set(None)
    _src_ip.set(None)
    _dst_ip.set(None)
    _group_id.set(None)
    _bot_id.set(None)
    _channel_id.set(None)


def get_audit_context() -> dict[str, Any]:
    """返回当前 ContextVar 快照（未设置的键值为 None）。"""
    return {
        "session_id": _session_id.get(),
        "request_id": _request_id.get(),
        "user_id": _user_id.get(),
        "src_ip": _src_ip.get(),
        "dst_ip": _dst_ip.get(),
        "group_id": _group_id.get(),
        "bot_id": _bot_id.get(),
        "channel_id": _channel_id.get(),
    }


def audit_context_snapshot() -> dict[str, str]:
    """非空 ContextVar 快照（对齐第二版 audit_context_snapshot）。"""
    result: dict[str, str] = {}
    for key, value in get_audit_context().items():
        if value:
            result[key] = str(value)
    return result


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
    src_ip: Optional[str] = None,
    dst_ip: Optional[str] = None,
) -> AuditContextTokens:
    """第二版别名：bind 请求上下文。"""
    return bind_audit_context(
        session_id=session_id,
        request_id=request_id,
        user_id=user_id,
        src_ip=src_ip if src_ip is not None else srcip,
        dst_ip=dst_ip if dst_ip is not None else dstip,
        group_id=group_id,
        bot_id=bot_id,
        channel_id=channel_id,
    )


def reset_request_context(tokens: AuditContextTokens | dict | None) -> None:
    """第二版别名：reset。兼容 dict token map。"""
    if tokens is None:
        return
    if isinstance(tokens, AuditContextTokens):
        reset_audit_context(tokens)
        return
    # dict[field, Token] from older audit_v2 API
    mapping = {
        "session_id": _session_id,
        "request_id": _request_id,
        "user_id": _user_id,
        "src_ip": _src_ip,
        "dst_ip": _dst_ip,
        "group_id": _group_id,
        "bot_id": _bot_id,
        "channel_id": _channel_id,
        "srcip": _src_ip,
        "dstip": _dst_ip,
    }
    for field, token in tokens.items():
        var = mapping.get(field)
        if var is not None:
            var.reset(token)


def bind_routing(
    *,
    session_id: str = "",
    user_id: str = "",
    request_id: str = "",
    group_id: str = "",
    bot_id: str = "",
    channel_id: str = "",
) -> None:
    """绑定 ContextVar，并写入 session 路由注册表（跨任务回退）。"""
    bind_request_context(
        user_id=user_id or None,
        session_id=session_id or None,
        request_id=request_id or None,
        group_id=group_id or None,
        bot_id=bot_id or None,
        channel_id=channel_id or None,
    )
    if not session_id:
        return
    ctx = {
        "session_id": session_id,
        "user_id": user_id,
        "request_id": request_id,
        "group_id": group_id,
        "bot_id": bot_id,
        "channel_id": channel_id,
    }
    if (
        len(_ROUTING_BY_SESSION) >= _ROUTING_BY_SESSION_CAP
        and session_id not in _ROUTING_BY_SESSION
    ):
        for stale in list(_ROUTING_BY_SESSION)[: len(_ROUTING_BY_SESSION) // 2]:
            _ROUTING_BY_SESSION.pop(stale, None)
    _ROUTING_BY_SESSION[session_id] = ctx


def lookup_routing(session_id: str) -> dict[str, str]:
    if not session_id:
        return {}
    return dict(_ROUTING_BY_SESSION.get(session_id, {}))
