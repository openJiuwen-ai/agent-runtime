# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""定时器模块 - 管理异步定时任务"""
import asyncio
from typing import Callable, Dict

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import ITimer

logger = get_logger(__name__)


class Timer(ITimer):
    """定时器类，用于管理异步定时任务"""

    def __init__(self):
        self._timers: Dict[str, asyncio.Task] = {}
        logger.info("Timer initialized")

    async def start_timer(self, key: str, ttl: int, callback: Callable) -> None:
        if key in self._timers:
            logger.info("同 key 计时器已存在, 将取消旧任务: key=%s", key)
            await self.cancel_timer(key)

        task = asyncio.create_task(self._timer_task(key, ttl, callback))
        self._timers[key] = task
        logger.info("计时器已启动: key=%s ttl=%s秒, 活动计时器数=%s", key, ttl, len(self._timers))
        logger.debug("计时器协程: %s", task.get_name() if hasattr(task, "get_name") else task)

    async def cancel_timer(self, key: str) -> bool:
        if key not in self._timers:
            logger.debug("取消计时器: key=%s 不存在(幂等)", key)
            return False

        task = self._timers[key]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        if key in self._timers:
            del self._timers[key]
        logger.debug("计时器已取消: key=%s 剩余数=%s", key, len(self._timers))
        return True

    async def _timer_task(self, key: str, ttl: int, callback: Callable) -> None:
        try:
            logger.debug("计时器睡眠开始: key=%s ttl=%s", key, ttl)
            await asyncio.sleep(ttl)
            logger.info("计时器到期, 执行回调: key=%s", key)
            if asyncio.iscoroutinefunction(callback):
                await callback()
            else:
                callback()
        except asyncio.CancelledError:
            logger.debug("计时器任务被取消: key=%s", key)
            raise
        except Exception as e:
            logger.error("计时器回调异常: key=%s err=%s", key, e, exc_info=True)
        finally:
            if key in self._timers:
                del self._timers[key]

    async def stop_all(self) -> None:
        logger.info("停止全部计时器, 数量=%s", len(self._timers))
        for key in list(self._timers.keys()):
            await self.cancel_timer(key)
        logger.info("全部计时器已停止")

    async def has_timer(self, key: str) -> bool:
        return key in self._timers
