# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""ServiceManager._message_loop：意外 RuntimeError 不停摆；真关闭才退出。

回归(生产事故)：双队列双侧同窗口就绪竞态曾让 get() 抛出文案为 "is closed" 的
RuntimeError（队列实际未关闭），_message_loop 捕获后直接 break，消费循环永久
退出，后续所有请求等待满 message_timeout 才由 Access 超时兜底。
"""

# G.CLS.11（建议级）针对生产封装，不适用于此类白盒测试，故统一豁免。
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from openjiuwen_runtime.management.session.interfaces import (
    IResponseParser,
    IServiceHandler,
    IServiceInstanceFactory,
    MessageType,
    RawMessage,
)
from openjiuwen_runtime.management.session.service_manager import (
    QueueItem,
    ServiceManager,
)
from openjiuwen_runtime.management.session.timer import Timer


@dataclass
class _P(IResponseParser):
    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        return data.get("request_id")

    def is_completed(self, data: dict[str, Any]) -> bool:
        return bool(data.get("done") or data.get("completed"))

    def response(self, data: dict[str, Any]) -> Any:
        return data.get("t", data)


class _NeverFactory(IServiceInstanceFactory):
    async def new_service(
        self,
        response_parser: IResponseParser,
        service_template: Optional[Dict[str, Any]] = None,
    ) -> IServiceHandler:
        raise AssertionError("本测试不应触发 deploy")


class _FlakyQueue:
    """模拟旧版竞态误报：首次 get() 抛「队列未关闭」的 RuntimeError，随后放行一条用户消息再挂住。"""

    def __init__(self, item: QueueItem) -> None:
        self.closed = False
        self._raised = False
        self._item: Optional[QueueItem] = item

    async def get(self) -> QueueItem:
        if not self._raised:
            self._raised = True
            raise RuntimeError("PriorityDualAsyncQueues is closed")
        if self._item is not None:
            item, self._item = self._item, None
            return item
        await asyncio.Future()  # 后续挂住，由测试 cancel 收尾


class _ClosedQueue:
    """真关闭语义：closed 恒为 True，get() 恒抛 RuntimeError。"""

    closed = True

    def __init__(self) -> None:
        self.get_calls = 0

    async def get(self) -> QueueItem:
        self.get_calls += 1
        raise RuntimeError("PriorityDualAsyncQueues is closed and empty")


def _install_recorder(sm: ServiceManager) -> tuple[List[RawMessage], asyncio.Event]:
    """替换 _handle_user_request 为记录式桩（本分支消息循环直接调该方法）。"""
    handled: List[RawMessage] = []
    arrived = asyncio.Event()

    async def _record(item: RawMessage) -> None:
        handled.append(item)
        arrived.set()

    sm._handle_user_request = _record  # type: ignore[method-assign]
    return handled, arrived


def _make_sm(q: Any) -> ServiceManager:
    sm = ServiceManager(
        _NeverFactory(),
        q,
        Timer(),
        min_idle_services=0,
        pod_monitor_enabled=False,
    )
    sm._in_use[None] = {}
    sm._idle[None] = {}
    return sm


@pytest.mark.asyncio
async def test_message_loop_survives_unexpected_runtime_error() -> None:
    """回归(生产事故)：get() 抛「未关闭」RuntimeError 时，循环退避后必须继续消费。"""
    item = RawMessage(MessageType.USER_REQUEST, "payload")
    flaky = _FlakyQueue(item)
    sm = _make_sm(flaky)
    await sm.init(_P())
    sm._running = True
    handled, arrived = _install_recorder(sm)

    loop_task = asyncio.create_task(sm._message_loop())
    await asyncio.wait_for(arrived.wait(), timeout=2.0)
    assert handled == [item], "意外异常后消息循环应继续消费而非永久退出"

    sm._running = False
    loop_task.cancel()
    await asyncio.gather(loop_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_message_loop_exits_when_queue_really_closed() -> None:
    """真关闭语义保持：closed 队列抛 RuntimeError 时循环应立即退出（不空转重试）。"""
    cq = _ClosedQueue()
    sm = _make_sm(cq)
    await sm.init(_P())
    sm._running = True
    _install_recorder(sm)

    # closed → break → _message_loop 立即返回
    await asyncio.wait_for(sm._message_loop(), timeout=1.0)
    assert cq.get_calls == 1
