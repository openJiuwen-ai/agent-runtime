# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Timer 单元测试"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from openjiuwen_runtime.management.orchestrator.timer import Timer


class TestTimer:
    """测试 Timer 类"""

    @pytest.fixture
    def timer(self):
        """创建 Timer 实例"""
        return Timer()

    @pytest.mark.asyncio
    async def test_start_timer(self, timer):
        """测试启动定时器"""
        callback = MagicMock()

        await timer.start_timer("test_timer", 1, callback)

        assert await timer.has_timer("test_timer") is True

        await timer.cancel_timer("test_timer")

    @pytest.mark.asyncio
    async def test_start_timer_replaces_existing(self, timer):
        """测试启动定时器时替换已存在的定时器"""
        callback1 = MagicMock()
        callback2 = MagicMock()

        await timer.start_timer("test_timer", 10, callback1)
        assert await timer.has_timer("test_timer") is True

        await timer.start_timer("test_timer", 10, callback2)
        assert await timer.has_timer("test_timer") is True

        await timer.cancel_timer("test_timer")

    @pytest.mark.asyncio
    async def test_cancel_timer(self, timer):
        """测试取消定时器"""
        callback = MagicMock()

        await timer.start_timer("test_timer", 10, callback)
        assert await timer.has_timer("test_timer") is True

        result = await timer.cancel_timer("test_timer")

        assert result is True
        assert await timer.has_timer("test_timer") is False

    @pytest.mark.asyncio
    async def test_cancel_timer_not_found(self, timer):
        """测试取消不存在的定时器"""
        result = await timer.cancel_timer("non_existent_timer")

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_all(self, timer):
        """测试停止所有定时器"""
        callback = MagicMock()

        await timer.start_timer("timer1", 10, callback)
        await timer.start_timer("timer2", 10, callback)
        await timer.start_timer("timer3", 10, callback)

        assert await timer.has_timer("timer1") is True
        assert await timer.has_timer("timer2") is True
        assert await timer.has_timer("timer3") is True

        await timer.stop_all()

        assert await timer.has_timer("timer1") is False
        assert await timer.has_timer("timer2") is False
        assert await timer.has_timer("timer3") is False

    @pytest.mark.asyncio
    async def test_has_timer(self, timer):
        """测试检查定时器是否存在"""
        callback = MagicMock()

        assert await timer.has_timer("test_timer") is False

        await timer.start_timer("test_timer", 10, callback)
        assert await timer.has_timer("test_timer") is True

        await timer.cancel_timer("test_timer")
        assert await timer.has_timer("test_timer") is False

    @pytest.mark.asyncio
    async def test_timer_callback_sync(self, timer):
        """测试超时回调（同步函数）"""
        callback = MagicMock()
        callback_called = asyncio.Event()

        def sync_callback():
            callback()
            callback_called.set()

        await timer.start_timer("test_timer", 0.1, sync_callback)

        await asyncio.wait_for(callback_called.wait(), timeout=1.0)

        callback.assert_called_once()

        await asyncio.sleep(0.1)
        assert await timer.has_timer("test_timer") is False

    @pytest.mark.asyncio
    async def test_timer_callback_async(self, timer):
        """测试超时回调（异步函数）"""
        callback = AsyncMock()
        callback_called = asyncio.Event()

        async def async_callback():
            await callback()
            callback_called.set()

        await timer.start_timer("test_timer", 0.1, async_callback)

        await asyncio.wait_for(callback_called.wait(), timeout=1.0)

        callback.assert_called_once()

        await asyncio.sleep(0.1)
        assert await timer.has_timer("test_timer") is False

    @pytest.mark.asyncio
    async def test_timer_callback_exception_handling(self, timer):
        """测试超时回调异常处理"""
        callback_called = asyncio.Event()

        async def failing_callback():
            callback_called.set()
            raise ValueError("Test error")

        await timer.start_timer("test_timer", 0.1, failing_callback)

        await asyncio.wait_for(callback_called.wait(), timeout=1.0)

        await asyncio.sleep(0.1)
        assert await timer.has_timer("test_timer") is False
