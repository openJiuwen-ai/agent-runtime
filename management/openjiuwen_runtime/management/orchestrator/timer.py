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
            logger.warning(f"Timer with key '{key}' already exists, cancelling old timer")
            await self.cancel_timer(key)

        task = asyncio.create_task(self._timer_task(key, ttl, callback))
        self._timers[key] = task
        logger.info(f"Timer started: key='{key}', ttl={ttl}s")

    async def cancel_timer(self, key: str) -> bool:
        if key not in self._timers:
            logger.warning(f"Timer with key '{key}' not found")
            return False

        task = self._timers[key]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        del self._timers[key]
        logger.info(f"Timer cancelled: key='{key}'")
        return True

    async def _timer_task(self, key: str, ttl: int, callback: Callable) -> None:
        try:
            await asyncio.sleep(ttl)
            logger.info(f"Timer expired: key='{key}', executing callback")
            if asyncio.iscoroutinefunction(callback):
                await callback()
            else:
                callback()
        except asyncio.CancelledError:
            logger.debug(f"Timer task cancelled: key='{key}'")
            raise
        except Exception as e:
            logger.error(f"Timer callback error: key='{key}', error={e}")
        finally:
            if key in self._timers:
                del self._timers[key]

    async def stop_all(self) -> None:
        logger.info(f"Stopping all timers, count={len(self._timers)}")
        for key in list(self._timers.keys()):
            await self.cancel_timer(key)
        logger.info("All timers stopped")

    async def has_timer(self, key: str) -> bool:
        return key in self._timers
