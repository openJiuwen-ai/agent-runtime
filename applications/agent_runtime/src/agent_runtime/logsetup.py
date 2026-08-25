# coding: utf-8
"""agent-runtime 日志装配（生产可观测性入口）。

两件事：

1. ``configure_logging()``：让 ``AGENT_RUNTIME_LOG_LEVEL`` 真正生效。
   框架在导入期经 ``get_logger → setup_logging → dictConfig`` 重置过 root
   （cli.py 旧 ``basicConfig`` 是死配置，被覆盖丢弃），且 yaml 里 root 与各
   handler 都钉死 INFO——本函数在框架配置**之后**统一收口：root 级别、
   handler 级别（只放宽不收紧）、httpx 探测降噪、请求上下文注入。

2. 请求关联：contextvars + root 级 Filter/Formatter。请求处理期间的日志行
   尾部追加 ``| request_id=… session_id=… endpoint=… instance=…``；后台任务
   （无请求上下文）日志行保持与原先逐字节一致。不改框架 logging.yaml 格式串，
   只在各 root handler 外包一层 Formatter。

调用位置：仅进程入口 ``cli.py``（uvicorn.run 之前）。pytest 下**不要**调用
（会改写 root handler，影响 caplog 捕获）。幂等，可重复调用。
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar, Token
from typing import Any

from openjiuwen_runtime.foundation.log import setup_logging

_VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_CTX: ContextVar[dict[str, str]] = ContextVar(
    "agent_runtime_log_ctx", default={}
)


# ---------------------------------------------------------------- 请求上下文


def set_request_log_context(**fields: Any) -> Token:
    """进入请求时绑定上下文（空值字段丢弃）；返回 token 供 reset。"""
    cleaned = {k: str(v) for k, v in fields.items() if v not in (None, "")}
    return _CTX.set(cleaned)


def reset_request_log_context(token: Token) -> None:
    """离开请求时恢复（必须在请求期间最后一条日志之后调用）。"""
    _CTX.reset(token)


def current_log_tail() -> str:
    """当前上下文的 ``k=v`` 串（无上下文返回空串——后台任务行不加尾巴）。"""
    ctx = _CTX.get()
    if not ctx:
        return ""
    return " ".join(f"{k}={v}" for k, v in ctx.items())


class _RequestContextFilter(logging.Filter):
    """给每条 record 附 request_tail（默认空串，Formatter 不会缺属性）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_tail = current_log_tail()
        return True


class _ContextFormatter(logging.Formatter):
    """包装 handler 既有 formatter：格式化结果 + 请求上下文尾巴。"""

    def __init__(self, base: logging.Formatter) -> None:
        super().__init__()
        self._base = base

    def format(self, record: logging.LogRecord) -> str:
        base = self._base.format(record)
        tail = getattr(record, "request_tail", "")
        return f"{base} | {tail}" if tail else base


def install_request_context() -> None:
    """幂等：把 Filter/Formatter 挂到 root 全部 handler 上。"""
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, _RequestContextFilter) for f in handler.filters):
            handler.addFilter(_RequestContextFilter())
        if not isinstance(handler.formatter, _ContextFormatter):
            base = handler.formatter or logging.Formatter()
            handler.setFormatter(_ContextFormatter(base))


# ---------------------------------------------------------------- 入口收口


def configure_logging(level: str | None = None) -> None:
    """进程入口统一收口（幂等；须在框架 setup_logging 之后，即 import 之后）。

    - 重放一次 ``setup_logging()``（读取 ``OPENJIUWEN_RUNTIME_LOG_FILE=disabled``
      等文件 handler 开关）；
    - ``AGENT_RUNTIME_LOG_LEVEL`` → root 级别；handler 级别只放宽不收紧
      （yaml 的刻意分级不被覆盖，DEBUG 请求能穿透 handler 的 INFO 钉死）；
    - httpx/httpcore 降噪到 WARNING（健康探测每 10s 一条 INFO 刷屏）；
    - 挂请求上下文 Filter/Formatter。
    """
    raw_level = (level or os.getenv("AGENT_RUNTIME_LOG_LEVEL", "INFO")).strip().upper()
    if raw_level not in _VALID_LEVELS:
        logging.getLogger("agent_runtime").warning(
            "invalid AGENT_RUNTIME_LOG_LEVEL=%r, using INFO", raw_level,
        )
        raw_level = "INFO"
    target = getattr(logging, raw_level)

    # 重放框架配置（幂等；会 _reset_root_handlers 后 dictConfig）
    setup_logging()

    root = logging.getLogger()
    root.setLevel(target)
    for handler in root.handlers:
        # handler 钉死在比请求级别粗的位置时放宽（如 yaml INFO vs 请求 DEBUG）
        if handler.level != logging.NOTSET and handler.level > target:
            handler.setLevel(target)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    install_request_context()
