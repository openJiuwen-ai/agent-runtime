# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Sweeper 后台循环：整秒对齐抢锁 → Pass A/B sweep → 放锁。"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Awaitable, Callable, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .config import SweeperConfig
from .lock import SweepLock
from .resource_client import NoOpResourceClient, ResourceClient
from .store import ExpiryStore
from .sweeper import Sweeper

logger = get_logger(__name__)

SleepFn = Callable[[float], Awaitable[None]]
TimeFn = Callable[[], float]


async def sleep_until_next_boundary(
    interval_sec: int,
    *,
    time_fn: TimeFn = time.time,
    sleep_fn: SleepFn = asyncio.sleep,
) -> None:
    """挂钟对齐：睡到下一个 interval 边界。"""
    interval = max(int(interval_sec), 1)
    now = time_fn()
    next_ts = (math.floor(now / interval) + 1) * interval
    delay = max(0.0, next_ts - now)
    await sleep_fn(delay)


class SweeperRunner:
    """生命周期：start / stop。"""

    def __init__(
        self,
        config: SweeperConfig,
        redis: Any,
        *,
        instance_id: str,
        resource_client: Any = None,
        http_client: Any = None,
        time_fn: TimeFn = time.time,
        sleep_fn: Optional[SleepFn] = None,
    ) -> None:
        self._config = config
        self._instance_id = instance_id
        self._time_fn = time_fn
        self._sleep_fn: SleepFn = sleep_fn or asyncio.sleep
        self._stopped = asyncio.Event()
        self._task: Optional[asyncio.Task[Any]] = None

        self._lock = SweepLock(
            redis,
            lock_key=config.lock_key,
            lock_ttl_sec=config.lock_ttl_sec,
            token_prefix=config.lock_token_prefix,
            instance_id=instance_id,
        )
        store = ExpiryStore(redis, idle_notify_ttl_sec=config.idle_notify_ttl_sec)
        if resource_client is not None:
            resource = resource_client
        elif config.resource_base_url and http_client is not None:
            resource = ResourceClient(
                http_client,
                base_url=config.resource_base_url,
                idle_consider_path=config.resource_idle_consider_path,
            )
        else:
            resource = NoOpResourceClient()
        self._sweeper = Sweeper(store, resource)

    @property
    def sweeper(self) -> Sweeper:
        return self._sweeper

    async def start(self) -> None:
        if not self._config.enabled:
            logger.info("SweeperRunner disabled, skip start")
            return
        if self._task and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run_forever(), name=f"sweeper-{self._instance_id}")
        logger.info("SweeperRunner started: instance=%s", self._instance_id)

    async def stop(self) -> None:
        self._stopped.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=self._config.stop_timeout_sec)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            logger.exception("SweeperRunner stop wait failed")
        logger.info("SweeperRunner stopped: instance=%s", self._instance_id)

    async def _run_forever(self) -> None:
        while not self._stopped.is_set():
            try:
                await sleep_until_next_boundary(
                    self._config.interval_sec,
                    time_fn=self._time_fn,
                    sleep_fn=self._sleep_fn,
                )
                if self._stopped.is_set():
                    break
                token = await self._lock.try_acquire()
                if token is None:
                    continue
                try:
                    await self._sweeper.sweep_once(now=self._time_fn())
                except Exception:
                    logger.exception("sweep_once failed")
                finally:
                    await self._lock.release_if_owner(token)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SweeperRunner loop error")
