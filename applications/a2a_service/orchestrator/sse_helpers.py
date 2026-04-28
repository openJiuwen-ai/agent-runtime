# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SSE / EventQueue 辅助函数（无业务依赖，便于独立单测）。"""
from __future__ import annotations

from typing import Any, Optional

from a2a.server.events.event_queue import QueueShutDown
from loguru import logger


async def next_sse_event(event_queue: Any) -> Optional[Any]:
    """从 a2a EventQueue 取下一个事件。

    返回:
        - 事件对象：队列内还有数据
        - None：队列已正常关闭且为空（end-of-stream 信号）

    抛出:
        除 ``QueueShutDown`` 之外的任何异常都会上抛，由调用方记 WARNING。

    注意：a2a-sdk 在 py3.11/3.12 用 ``AsyncQueueShutDown as QueueShutDown`` 别名
    导入；类的真实 ``__name__`` 是 ``AsyncQueueShutDown``，所以这里用
    ``isinstance`` / ``except`` 做类匹配，不能用 ``__name__`` 字符串比较。
    """
    try:
        event = await event_queue.dequeue_event()
    except QueueShutDown:
        return None
    event_queue.task_done()
    return event


def log_outbound_sse(
    *,
    conversation_id: str,
    sequence: int,
    payload: str,
    event_kind: str,
) -> None:
    """北向 SSE 推送埋点（INFO 级，每帧一行）。

    用于现场排障：当只能看 a2a_service 日志、看不到客户端抓包时，可以通过这条
    日志确认 Runtime 实际向北向推送了哪些事件、字节数与顺序。
    """
    logger.info(
        "[Router] → SSE conv={} #{} kind={} bytes={}",
        conversation_id,
        sequence,
        event_kind,
        len(payload.encode("utf-8")),
    )
