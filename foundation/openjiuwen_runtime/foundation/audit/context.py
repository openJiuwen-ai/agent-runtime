# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""请求级 ContextVar：session_id / request_id / user_id / src_ip / dst_ip。"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any


_session_id: ContextVar[str | None] = ContextVar("audit_session_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("audit_request_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("audit_user_id", default=None)
_src_ip: ContextVar[str | None] = ContextVar("audit_src_ip", default=None)
_dst_ip: ContextVar[str | None] = ContextVar("audit_dst_ip", default=None)


@dataclass(frozen=True)
class AuditContextTokens:
    session_id: Token[str | None] | None = None
    request_id: Token[str | None] | None = None
    user_id: Token[str | None] | None = None
    src_ip: Token[str | None] | None = None
    dst_ip: Token[str | None] | None = None


def bind_audit_context(
    *,
    session_id: str | None = None,
    request_id: str | None = None,
    user_id: str | None = None,
    src_ip: str | None = None,
    dst_ip: str | None = None,
) -> AuditContextTokens:
    """在请求入口写入 ContextVar；返回 tokens 供 finally 中 reset。"""
    tokens = AuditContextTokens(
        session_id=_session_id.set(session_id) if session_id is not None else None,
        request_id=_request_id.set(request_id) if request_id is not None else None,
        user_id=_user_id.set(user_id) if user_id is not None else None,
        src_ip=_src_ip.set(src_ip) if src_ip is not None else None,
        dst_ip=_dst_ip.set(dst_ip) if dst_ip is not None else None,
    )
    return tokens


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


def clear_audit_context() -> None:
    """测试辅助：将全部 ContextVar 置回 None。"""
    _session_id.set(None)
    _request_id.set(None)
    _user_id.set(None)
    _src_ip.set(None)
    _dst_ip.set(None)


def get_audit_context() -> dict[str, Any]:
    """返回当前 ContextVar 快照（未设置的键值为 None）。"""
    return {
        "session_id": _session_id.get(),
        "request_id": _request_id.get(),
        "user_id": _user_id.get(),
        "src_ip": _src_ip.get(),
        "dst_ip": _dst_ip.get(),
    }
