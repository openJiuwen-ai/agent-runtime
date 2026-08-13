# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""JobRunner：提前窗口醒来 → 协调 → 回调 → 放锁。

使用本机墙钟 ``time.time``。睡觉一次睡到目标点；
stop 通过 cancel 打断，或醒来后检查退出。
上一拍未结束则跳过本拍（写死）。

对外配置请看工厂 ``create_single_leader_job``；本类只收组装后的零件。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional

from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_runtime.foundation.periodic.coordinator.base import Coordinator
from openjiuwen_runtime.foundation.periodic.schedule.base import Schedule

logger = get_logger(__name__)

_STOP_TIMEOUT_SEC = 3.0


class JobRunner:
    """进程内周期任务生命周期管理。"""

    def __init__(
        self,
        *,
        name: str,
        schedule: Schedule,
        coordinator: Coordinator,
        on_tick: Callable[[], Awaitable[None]],
        instance_id: str,
        gather_window_sec: float = 0.0,
        run_on_start: bool = False,
        stop_timeout_sec: float = _STOP_TIMEOUT_SEC,
    ) -> None:
        self._name = name
        self._schedule = schedule
        self._coordinator = coordinator
        self._on_tick = on_tick
        self._instance_id = instance_id
        self._gather_window_sec = max(float(gather_window_sec), 0.0)
        self._run_on_start = bool(run_on_start)
        self._stop_timeout_sec = float(stop_timeout_sec)
        self._stopped = asyncio.Event()
        self._task: Optional[asyncio.Task[Any]] = None
        self._busy = False
        self._last_now: Optional[float] = None

    @property
    def name(self) -> str:
        return self._name

    def _now(self) -> float:
        return float(time.time())

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(
            self._run_forever(),
            name=f"periodic-{self._name}-{self._instance_id}",
        )
        logger.info(
            "JobRunner started: job=%s instance=%s",
            self._name,
            self._instance_id,
        )

    async def stop(self) -> None:
        self._stopped.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=self._stop_timeout_sec)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            logger.exception(
                "JobRunner stop wait failed: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
        logger.info(
            "JobRunner stopped: job=%s instance=%s",
            self._name,
            self._instance_id,
        )

    async def _sleep_until(self, target: float) -> None:
        """一次睡到目标时间；stop 靠 cancel 打断，或醒来后由循环检查 _stopped。"""
        if self._stopped.is_set():
            return
        delay = target - self._now()
        if delay <= 0:
            return
        await asyncio.sleep(delay)

    async def _run_forever(self) -> None:
        if self._run_on_start and not self._stopped.is_set():
            await self._safe_tick(planned_fire=self._now())

        while not self._stopped.is_set():
            try:
                now = self._now()
                if self._last_now is not None and now < self._last_now:
                    logger.warning(
                        "clock went backwards: job=%s last=%s now=%s",
                        self._name,
                        self._last_now,
                        now,
                    )
                    now = self._now()
                self._last_now = now

                next_ts = self._schedule.next_fire_time(now)
                if next_ts <= now:
                    next_ts = self._schedule.next_fire_time(self._now())

                gather = min(self._gather_window_sec, max(next_ts - now, 0.0))
                wake_at = next_ts - gather
                if wake_at > now:
                    await self._sleep_until(wake_at)
                if self._stopped.is_set():
                    break

                now2 = self._now()
                await self._safe_tick(planned_fire=next_ts, now=now2)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "JobRunner loop error: job=%s instance=%s",
                    self._name,
                    self._instance_id,
                )

    async def _safe_tick(self, *, planned_fire: float, now: Optional[float] = None) -> None:
        now_v = now if now is not None else self._now()
        claim = await self._coordinator.try_claim(
            now=now_v,
            instance_id=self._instance_id,
            planned_fire=planned_fire,
        )
        if claim is None:
            logger.debug(
                "job lock miss: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
            return

        if self._busy:
            logger.info(
                "tick skipped overlap: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
            await self._coordinator.release(claim)
            return

        self._busy = True
        ok = False
        t0 = time.monotonic()
        delay_ms = max(0.0, (now_v - planned_fire) * 1000)
        try:
            await self._on_tick()
            ok = True
        except Exception:
            logger.exception(
                "on_tick failed: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
        finally:
            duration_ms = (time.monotonic() - t0) * 1000
            self._busy = False
            try:
                await self._coordinator.release(claim)
            except Exception:
                logger.exception(
                    "release after tick failed: job=%s",
                    self._name,
                )
            logger.info(
                "tick done: job=%s instance=%s ok=%s delay_ms=%.1f duration_ms=%.1f",
                self._name,
                self._instance_id,
                ok,
                delay_ms,
                duration_ms,
            )
