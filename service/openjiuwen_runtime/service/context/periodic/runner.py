# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""JobRunner：提前窗口醒来 → 协调 → 回调 → 放锁。

调度时钟：Redis TIME 对表 + 本机 monotonic 推算（少打 TIME，避免 RTT 吃窗口）。
睡觉一次睡到目标点；stop 通过 cancel 打断，或醒来后检查退出。

对外配置请看 ``SystemContext.create_single_leader_job``；本类只收组装后的零件。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .clock import RedisAlignedClock
from .coordinator.base import Coordinator
from .schedule.base import Schedule

logger = get_logger(__name__)

_STOP_TIMEOUT_SEC = 3.0
_TIME_FAIL_SLEEP_SEC = 0.5


async def _cancel_and_wait(task: asyncio.Task[Any]) -> None:
    """取消后台任务并等到它结束，避免 stop 后回调还在跑。"""
    if task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


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
        redis: Any,
        clock: RedisAlignedClock | None = None,
        gather_window_sec: float = 0.0,
        run_on_start: bool = False,
        stop_timeout_sec: float = _STOP_TIMEOUT_SEC,
    ) -> None:
        self._name = name
        self._schedule = schedule
        self._coordinator = coordinator
        self._on_tick = on_tick
        self._instance_id = instance_id
        self._clock = clock or RedisAlignedClock(redis)
        self._gather_window_sec = max(float(gather_window_sec), 0.0)
        self._run_on_start = bool(run_on_start)
        self._stop_timeout_sec = float(stop_timeout_sec)
        self._stopped = asyncio.Event()
        self._task: Optional[asyncio.Task[Any]] = None
        self._last_now: Optional[float] = None

    @property
    def name(self) -> str:
        return self._name

    def _now(self) -> float:
        return self._clock.now()

    async def _sync_now(self) -> float | None:
        """对一次 Redis 表；失败则打日志并睡觉，返回 None 让主循环重来。"""
        try:
            return await self._clock.sync()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "redis TIME failed: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
            await asyncio.sleep(_TIME_FAIL_SLEEP_SEC)
            return None

    async def start(self) -> None:
        # 旧循环未结束（含 stop 超时）时禁止再起，避免双循环
        if self._task is not None and not self._task.done():
            logger.warning(
                "JobRunner start ignored, still running: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
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
        if task is None:
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=self._stop_timeout_sec)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            # runner 被我们 cancel 后 await 会冒 CancelledError，这是正常停机；
            # 若 stop() 自己被取消，必须继续往上抛，否则关机流程会误以为停干净了。
            me = asyncio.current_task()
            if me is not None and me.cancelling():
                raise
        except Exception:
            logger.exception(
                "JobRunner stop wait failed: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
        finally:
            # 仅在仍是同一 task 时清空；超时未结束则保留引用，阻止 start 再开一条
            if self._task is task and task.done():
                self._task = None
            elif self._task is task and not task.done():
                logger.warning(
                    "JobRunner stop timed out, loop still alive: job=%s instance=%s",
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
        while not self._stopped.is_set():
            try:
                now = await self._sync_now()
                if now is None:
                    continue

                if self._run_on_start:
                    self._run_on_start = False
                    await self._safe_tick(planned_fire=now)
                    continue

                if self._last_now is not None and now < self._last_now:
                    logger.warning(
                        "clock went backwards: job=%s last=%s now=%s",
                        self._name,
                        self._last_now,
                        now,
                    )
                    now = await self._sync_now()
                    if now is None:
                        continue
                self._last_now = now

                next_ts = self._schedule.next_fire_time(now)
                if next_ts <= now:
                    now = await self._sync_now()
                    if now is None:
                        continue
                    next_ts = self._schedule.next_fire_time(now)

                gather = min(self._gather_window_sec, max(next_ts - now, 0.0))
                wake_at = next_ts - gather
                if wake_at > now:
                    await self._sleep_until(wake_at)
                if self._stopped.is_set():
                    break

                # 长睡之后再对表，集合窗口用较新的偏移
                now2 = await self._sync_now()
                if now2 is None:
                    continue
                if self._missed_fire(now2, planned_fire=next_ts, wake_at=wake_at):
                    logger.info(
                        "missed fire, skip tick: job=%s planned=%s now=%s",
                        self._name,
                        next_ts,
                        now2,
                    )
                    continue
                await self._safe_tick(planned_fire=next_ts, now=now2)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "JobRunner loop error: job=%s instance=%s",
                    self._name,
                    self._instance_id,
                )
                await asyncio.sleep(_TIME_FAIL_SLEEP_SEC)

    def _missed_fire(self, now: float, *, planned_fire: float, wake_at: float) -> bool:
        """睡过头：本拍不开火，下一圈按新时间对齐到下一拍。

        - 已进入再下一拍（``now >= next_fire(planned)``）：一定跳过。
        - 本该提前醒来（集合窗口）却已经过了开火点：也跳过。
        - gather=0 时睡到 T 可能有几毫秒抖动，只要还没到下一拍仍打。
        """
        following = self._schedule.next_fire_time(planned_fire)
        if now >= following:
            return True
        return wake_at < planned_fire and now > planned_fire

    async def _invoke_on_tick(self) -> bool:
        """跑业务回调；若协调器支持失锁事件，失锁则取消回调。

        返回 True 表示正常跑完，False 表示因失锁被中断。
        """
        lost_ev = getattr(self._coordinator, "lock_lost_event", None)
        if lost_ev is None:
            await self._on_tick()
            return True

        tick_task = asyncio.create_task(self._on_tick())
        lost_task = asyncio.create_task(lost_ev.wait())
        try:
            done, _pending = await asyncio.wait(
                {tick_task, lost_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if tick_task in done:
                await tick_task
                return True

            logger.warning(
                "on_tick aborted due to lock lost: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
            return False
        except asyncio.CancelledError:
            # 先抓住取消，把子任务收干净再往上抛（3.11 里 catch 后才能 await 子任务）
            await _cancel_and_wait(tick_task)
            await _cancel_and_wait(lost_task)
            raise
        finally:
            await _cancel_and_wait(lost_task)
            if not tick_task.done():
                await _cancel_and_wait(tick_task)

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

        ok = False
        aborted = False
        t0 = time.monotonic()
        delay_ms = max(0.0, (now_v - planned_fire) * 1000)
        try:
            finished = await self._invoke_on_tick()
            ok = finished
            aborted = not finished
        except Exception:
            logger.exception(
                "on_tick failed: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
        finally:
            duration_ms = (time.monotonic() - t0) * 1000
            try:
                await self._coordinator.release(claim)
            except Exception:
                logger.exception(
                    "release after tick failed: job=%s",
                    self._name,
                )
            logger.info(
                "tick done: job=%s instance=%s ok=%s aborted=%s delay_ms=%.1f duration_ms=%.1f",
                self._name,
                self._instance_id,
                ok,
                aborted,
                delay_ms,
                duration_ms,
            )
