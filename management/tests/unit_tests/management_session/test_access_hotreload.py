# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Access 热更新：旧 SM 销毁串行化（禁止二次热更新）、单次清理任务、超次数强制 stop。"""

# G.CLS.11（建议级）针对生产封装，不适用于此类白盒测试：被测的切换/清理路径无公开
# 访问接口（_service_manager / _old_sm_cleanup_task / _periodic_cleanup_deprecated_sm），
# 故统一豁免。
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from typing import Optional

import pytest

from openjiuwen_runtime.management.session.access import Access


class _FakeSM:
    """最小 IServiceManager 替身：可控的清理行为（busy / 慢 / 成功）。"""

    def __init__(
        self,
        name: str = "sm",
        *,
        cleanup_gate: Optional[asyncio.Event] = None,
        cleanup_result: bool = False,
    ) -> None:
        self.name = name
        # _running 命名须与生产代码 _periodic_cleanup_deprecated_sm 的 getattr 探测一致
        self._running = True
        self._gate = cleanup_gate
        self._result = cleanup_result
        self.deprecated_count = 0
        self.stop_count = 0
        self.cleanup_calls = 0

    @property
    def running(self) -> bool:
        """是否仍在运行（供断言的公开只读视图）。"""
        return self._running

    async def init(self, response_parser: object) -> None:
        return None

    async def start(self) -> None:
        return None

    def mark_deprecated(self) -> None:
        self.deprecated_count += 1

    async def try_cleanup_if_idle(self) -> bool:
        self.cleanup_calls += 1
        if self._gate is not None:
            await self._gate.wait()
        if self._result:
            self._running = False
            self.stop_count += 1
            return True
        return False

    async def stop(self) -> None:
        self._running = False
        self.stop_count += 1


def _make_access(initial: _FakeSM, *rest: _FakeSM) -> Access:
    fakes = list(rest)

    async def factory() -> _FakeSM:
        return fakes.pop(0)

    acc = Access(factory)
    acc._service_manager = initial  # type: ignore[assignment]
    return acc


@pytest.mark.asyncio
async def test_update_config_serializes_when_old_sm_still_destroying() -> None:
    """旧 SM 销毁未完成时，第二次热更新必须等待（禁止新旧 SM 并存）；且每次只起一个清理任务。"""
    gate = asyncio.Event()
    old = _FakeSM("A", cleanup_gate=gate, cleanup_result=True)  # gate 放行后能清理成功
    b = _FakeSM("B", cleanup_result=True)  # 被弃用时即时清理，避免后台任务外泄
    c = _FakeSM("C")
    acc = _make_access(old, b, c)

    # 第一次热更新：A→B；A 的清理任务阻塞在 gate 上（销毁未完成）
    t1 = asyncio.create_task(acc.update_config())
    await asyncio.sleep(0.05)
    assert acc._service_manager is b
    assert old.deprecated_count == 1
    assert acc._old_sm_cleanup_task is not None and not acc._old_sm_cleanup_task.done()

    # 第二次热更新：因 A 尚未销毁完成，必须等待，不得切换到 C
    t2 = asyncio.create_task(acc.update_config())
    await asyncio.sleep(0.05)
    assert acc._service_manager is b, "旧 SM 销毁完成前不应执行第二次切换"
    assert not t2.done()

    # 放行 A 的清理 → 第二次热更新才能继续
    gate.set()
    await asyncio.wait_for(t1, timeout=2.0)
    await asyncio.wait_for(t2, timeout=2.0)
    assert acc._service_manager is c
    # 每次热更新只起一个清理任务（回归 access.py 重复 create_task 缺陷）
    assert old.cleanup_calls == 1, (
        f"每次热更新只应起一个清理任务，实际 try_cleanup 调用 {old.cleanup_calls} 次"
    )
    # 收尾 b 的后台清理任务，避免外泄
    if acc._old_sm_cleanup_task is not None:
        await asyncio.wait_for(acc._old_sm_cleanup_task, timeout=2.0)


@pytest.mark.asyncio
async def test_periodic_cleanup_force_stops_after_max_retries() -> None:
    """旧 SM 始终 busy（try_cleanup 返回 False）时，达到最大重试次数后必须强制 stop，不得静默泄漏。"""
    old = _FakeSM("A", cleanup_result=False)  # 永远清理不掉
    acc = _make_access(old)

    await acc._periodic_cleanup_deprecated_sm(old, max_retries=2, interval=0.001)

    assert old.cleanup_calls == 2, "应重试 max_retries 次"
    assert old.stop_count == 1, "超次数后必须强制 stop（回归：此前仅打日志 return，SM+Pod 泄漏）"
    assert old.running is False
