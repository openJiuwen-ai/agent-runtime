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
# 取消后最多再等这么久。子任务若把 CancelledError 吞掉（redis-py 关连接
# 不返回就是这种），继续 await 会把整拍和看门狗一起拖死。
_CANCEL_WAIT_SEC = 3.0
# 看门狗在 tick 超时 + 取消等待之外再留的余量。必须盖住合法长 tick
# （autoscale 等 Ready 可到 tick_timeout），只抓「超时之后仍不推进」的循环。
_WATCHDOG_SLACK_SEC = 15.0
_WATCHDOG_POLL_SEC = 5.0
# tick done 常态降为 DEBUG（1Hz 任务刷屏降噪）；异常拍与周期心跳仍打 INFO
_TICK_HEARTBEAT_EVERY = 600        # 每 N 拍打一条 INFO 心跳（1Hz ≈ 10 分钟）
_TICK_SLOW_MS = 1000.0             # 单拍超过该时长视为慢拍，提级 INFO


def _iter_coro_frames(coro: Any) -> Any:
    """沿 cr_await 链交出仍挂起的帧。"""
    seen: set[int] = set()
    while coro is not None and id(coro) not in seen:
        seen.add(id(coro))
        frame = getattr(coro, "cr_frame", None)
        if frame is not None:
            yield frame
        coro = getattr(coro, "cr_await", None)


def _detach_redis_connection(pool: Any, conn: Any) -> bool:
    """同步关掉连接并从在用集合摘掉，不放回空闲列表。

    卡死协程的 finally 不会再 release。放回池会被后续命令复用，协议状态
    已经不可信。``_close`` 是 redis-py 的同步关套接字，不走会再次挂起的
    ``disconnect()``。
    """
    in_use = getattr(pool, "_in_use_connections", None)
    close = getattr(conn, "_close", None)
    if in_use is None or not callable(close) or conn not in in_use:
        return False
    try:
        close()
    except Exception:
        logger.exception("close abandoned redis connection failed")
    try:
        in_use.discard(conn)
    except Exception:
        logger.exception("drop abandoned redis connection from pool failed")
    return True


def _drop_stuck_redis_connections(root: asyncio.Task[Any]) -> int:
    """从放弃的任务及其子任务栈上摘掉仍占着的 redis 连接。"""
    dropped = 0
    seen_tasks: set[int] = set()
    pending: list[asyncio.Task[Any]] = [root]
    while pending:
        task = pending.pop()
        if id(task) in seen_tasks:
            continue
        seen_tasks.add(id(task))
        get_coro = getattr(task, "get_coro", None)
        coro = get_coro() if callable(get_coro) else None
        for frame in _iter_coro_frames(coro):
            locals_ = list(frame.f_locals.items())
            conn = frame.f_locals.get("conn")
            pool = frame.f_locals.get("pool")
            if conn is not None and pool is not None:
                if _detach_redis_connection(pool, conn):
                    dropped += 1
            for value in (item[1] for item in locals_):
                if isinstance(value, asyncio.Task) and id(value) not in seen_tasks:
                    pending.append(value)
    return dropped


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
        tick_timeout_sec: Optional[float] = None,
        cancel_wait_sec: float = _CANCEL_WAIT_SEC,
        watchdog_slack_sec: float = _WATCHDOG_SLACK_SEC,
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
        # 单次 tick 上限：None 不限制。防 on_tick 内某个 IO await 永久挂起
        # 拖死整个循环（挂起不抛异常，except Exception 捕不到）。超时取消
        # 本拍并记日志，下一拍重试；调用方须按 tick 内最重路径（如 deploy
        # 等 ready_timeout）给足余量。
        self._tick_timeout_sec = (
            None if tick_timeout_sec is None else max(float(tick_timeout_sec), 0.001)
        )
        self._cancel_wait_sec = max(float(cancel_wait_sec), 0.001)
        self._watchdog_slack_sec = max(float(watchdog_slack_sec), 0.0)
        self._stopped = asyncio.Event()
        self._task: Optional[asyncio.Task[Any]] = None
        self._watchdog: Optional[asyncio.Task[Any]] = None
        self._generation = 0
        self._loop_alive_at = time.monotonic()
        self._stall_restarts = 0
        self._abandoned_tasks: set[asyncio.Task[Any]] = set()
        self._last_now: Optional[float] = None
        # 诊断计数器（只增不改语义；snapshot() 供 /visualization 端点读取）
        self._ticks = 0
        self._ok_ticks = 0
        self._error_ticks = 0
        self._aborted_ticks = 0
        self._timedout_ticks = 0
        self._lock_misses = 0
        self._last_tick_at: Optional[float] = None
        self._last_duration_ms: Optional[float] = None
        self._last_error: Optional[str] = None

    @property
    def name(self) -> str:
        return self._name

    def snapshot(self) -> dict[str, Any]:
        """只读计数快照（诊断端点用；非原子读，允许瞬时不一致）。"""
        last_tick_at = self._last_tick_at
        return {
            "name": self._name,
            "instance_id": self._instance_id,
            "running": self._task is not None and not self._task.done(),
            "ticks": self._ticks,
            "ok_ticks": self._ok_ticks,
            "error_ticks": self._error_ticks,
            "aborted_ticks": self._aborted_ticks,
            "timedout_ticks": self._timedout_ticks,
            "lock_misses": self._lock_misses,
            "stall_restarts": self._stall_restarts,
            "last_tick_at": last_tick_at,
            "last_duration_ms": self._last_duration_ms,
            "last_error": self._last_error,
        }

    def _now(self) -> float:
        return self._clock.now()

    async def _sync_now(self) -> float | None:
        """对一次 Redis 表；失败则打日志并睡觉，返回 None 让主循环重来。"""
        try:
            return await self._clock.sync()
        except asyncio.CancelledError:
            logger.debug(
                "redis TIME cancelled: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
            raise
        except Exception:
            logger.exception(
                "redis TIME failed: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
            await asyncio.sleep(_TIME_FAIL_SLEEP_SEC)
            return None

    def _schedule_interval_sec(self) -> float:
        raw = getattr(self._schedule, "interval_sec", 0)
        try:
            return max(float(raw), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _stall_limit_sec(self) -> float:
        """循环无推进的判定阈值。

        取 tick 上限和调度间隔的较大者，再加取消等待。只盖 tick 时，
        sys_eval 这种间隔 300 秒、tick 只有 120 秒的任务会在正常睡觉时被重启。
        """
        tick = self._tick_timeout_sec if self._tick_timeout_sec is not None else 60.0
        return (
            max(tick, self._schedule_interval_sec())
            + self._cancel_wait_sec
            + self._watchdog_slack_sec
        )

    def _generation_current(self, generation: int) -> bool:
        """被放弃的旧循环世代号对不上。它若之后从卡死里醒过来，不能再往下打。"""
        return generation == self._generation and not self._stopped.is_set()

    def _abandon_coordinator_claim(self) -> None:
        """续约独立于 tick。放弃卡死协程时先停续约，否则新循环抢不到锁。"""
        abandon = getattr(self._coordinator, "abandon_claim", None)
        if abandon is None:
            return
        try:
            abandon()
        except Exception:
            logger.exception(
                "abandon claim failed: job=%s instance=%s",
                self._name,
                self._instance_id,
            )

    async def _cancel_and_wait(self, task: asyncio.Task[Any]) -> bool:
        """取消任务并最多再等 ``cancel_wait_sec``。

        返回 False 表示任务没结束，调用方必须停止等待。用 ``shield`` 包住，
        这样超时作用在等待者身上：子任务把取消吞掉时，本函数仍能返回。
        """
        if task.done() or task in self._abandoned_tasks:
            return task.done()
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._cancel_wait_sec)
        except asyncio.TimeoutError:
            self._abandoned_tasks.add(task)
            task.add_done_callback(self._abandoned_tasks.discard)
            dropped = _drop_stuck_redis_connections(task)
            logger.warning(
                "cancel wait abandoned task: job=%s task=%s dropped_conns=%d",
                self._name,
                task.get_name(),
                dropped,
            )
            return False
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if me is not None and me.cancelling():
                raise
            return task.done()
        except Exception:
            logger.exception(
                "cancelled task raised: job=%s task=%s",
                self._name,
                task.get_name(),
            )
        return True

    def _spawn_loop(self) -> None:
        self._generation += 1
        generation = self._generation
        self._loop_alive_at = time.monotonic()
        self._task = asyncio.create_task(
            self._run_forever(generation),
            name=f"periodic-{self._name}-{self._instance_id}",
        )

    def _ensure_watchdog(self) -> None:
        if self._watchdog is not None and not self._watchdog.done():
            return
        self._watchdog = asyncio.create_task(
            self._stall_watchdog(),
            name=f"periodic-watchdog-{self._name}-{self._instance_id}",
        )

    async def _restart_abandoned_loop(self, task: asyncio.Task[Any]) -> None:
        """不等待卡死循环，换一条新的。旧协程若之后自行返回，世代号对不上即退出。"""
        if self._stopped.is_set():
            return
        self._stall_restarts += 1
        self._abandon_coordinator_claim()
        if not task.done():
            self._abandoned_tasks.add(task)
            task.add_done_callback(self._abandoned_tasks.discard)
            task.cancel()
            dropped = _drop_stuck_redis_connections(task)
            logger.warning(
                "stall restart dropped redis conns: job=%s dropped_conns=%d",
                self._name,
                dropped,
            )
        self._task = None
        if self._stopped.is_set():
            return
        self._spawn_loop()
        logger.critical(
            "JobRunner restarted after stall: job=%s instance=%s restarts=%d",
            self._name,
            self._instance_id,
            self._stall_restarts,
        )

    async def _check_loop_liveness(self) -> None:
        if self._stopped.is_set():
            return
        task = self._task
        if task is None:
            return
        if task.done():
            logger.critical(
                "JobRunner loop exited, restarting: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
            self._task = None
            if not self._stopped.is_set():
                self._stall_restarts += 1
                self._spawn_loop()
            return
        if self._tick_timeout_sec is None:
            return
        idle = time.monotonic() - self._loop_alive_at
        if idle <= self._stall_limit_sec():
            return
        logger.critical(
            "JobRunner stalled %.0fs, restarting loop: job=%s instance=%s ticks=%d",
            idle,
            self._name,
            self._instance_id,
            self._ticks,
        )
        await self._restart_abandoned_loop(task)

    async def _stall_watchdog(self) -> None:
        while not self._stopped.is_set():
            delay = min(
                _WATCHDOG_POLL_SEC,
                max(self._stall_limit_sec() / 4, 0.05),
            )
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self._check_loop_liveness()
            except Exception:
                # CancelledError 继承 BaseException，不会进这里，停机取消仍向上抛。
                logger.exception(
                    "JobRunner watchdog failed: job=%s instance=%s",
                    self._name,
                    self._instance_id,
                )

    async def start(self) -> None:
        # 旧循环未结束（含 stop 超时）时禁止再起，避免双循环。
        # 看门狗主动放弃卡死循环时会先把 _task 置空，再走 _spawn_loop。
        if self._task is not None and not self._task.done():
            logger.warning(
                "JobRunner start ignored, still running: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
            return
        self._stopped.clear()
        self._spawn_loop()
        self._ensure_watchdog()
        logger.info(
            "JobRunner started: job=%s instance=%s",
            self._name,
            self._instance_id,
        )

    async def stop(self) -> None:
        self._stopped.set()
        watchdog = self._watchdog
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
        for abandoned in list(self._abandoned_tasks):
            if not abandoned.done():
                abandoned.cancel()
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

    async def _run_forever(self, generation: int) -> None:
        while self._generation_current(generation):
            self._loop_alive_at = time.monotonic()
            try:
                now = await self._sync_now()
                if not self._generation_current(generation):
                    return
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
                    if not self._generation_current(generation):
                        return
                    if now is None:
                        continue
                self._last_now = now

                next_ts = self._schedule.next_fire_time(now)
                if next_ts <= now:
                    now = await self._sync_now()
                    if not self._generation_current(generation):
                        return
                    if now is None:
                        continue
                    next_ts = self._schedule.next_fire_time(now)

                gather = min(self._gather_window_sec, max(next_ts - now, 0.0))
                wake_at = next_ts - gather
                if wake_at > now:
                    # 睡觉单独计时。醒了立刻刷新，避免整段间隔叠到醒后的对表上。
                    self._loop_alive_at = time.monotonic()
                    await self._sleep_until(wake_at)
                if not self._generation_current(generation):
                    return
                self._loop_alive_at = time.monotonic()

                # 长睡之后再对表，集合窗口用较新的偏移
                now2 = await self._sync_now()
                if not self._generation_current(generation):
                    return
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
                if generation != self._generation or self._stopped.is_set():
                    logger.debug(
                        "JobRunner loop cancelled: job=%s instance=%s",
                        self._name,
                        self._instance_id,
                    )
                    return
                logger.debug(
                    "JobRunner loop cancelled: job=%s instance=%s",
                    self._name,
                    self._instance_id,
                )
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
            # 先抓住取消，把子任务收干净再往上抛（3.11 里 catch 后才能 await 子任务）。
            # 收不干净就放弃：停续约、关连接，不再等它把 CancelledError 交回来。
            if not await self._cancel_and_wait(tick_task):
                self._abandon_coordinator_claim()
            await self._cancel_and_wait(lost_task)
            raise
        finally:
            await self._cancel_and_wait(lost_task)
            if not tick_task.done() and not await self._cancel_and_wait(tick_task):
                self._abandon_coordinator_claim()

    async def _safe_tick(self, *, planned_fire: float, now: Optional[float] = None) -> None:
        # 睡觉结束才进入本拍。看门狗从这里起算，避免把调度间隔算进停滞。
        self._loop_alive_at = time.monotonic()
        now_v = now if now is not None else self._now()
        claim = await self._coordinator.try_claim(
            now=now_v,
            instance_id=self._instance_id,
            planned_fire=planned_fire,
        )
        if claim is None:
            self._lock_misses += 1
            logger.debug(
                "job lock miss: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
            return

        ok = False
        aborted = False
        timed_out = False
        error_text: Optional[str] = None
        t0 = time.monotonic()
        delay_ms = max(0.0, (now_v - planned_fire) * 1000)
        try:
            if self._tick_timeout_sec is None:
                finished = await self._invoke_on_tick()
            else:
                finished = await asyncio.wait_for(
                    self._invoke_on_tick(), timeout=self._tick_timeout_sec
                )
            ok = finished
            aborted = not finished
        except asyncio.TimeoutError:
            timed_out = True
            logger.warning(
                "on_tick timed out, aborted this tick: job=%s instance=%s timeout=%.3fs",
                self._name,
                self._instance_id,
                self._tick_timeout_sec,
            )
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "on_tick failed: job=%s instance=%s",
                self._name,
                self._instance_id,
            )
        finally:
            duration_ms = (time.monotonic() - t0) * 1000
            self._ticks += 1
            self._last_tick_at = time.time()
            self._last_duration_ms = duration_ms
            self._last_error = error_text
            if timed_out:
                self._timedout_ticks += 1
            elif error_text is not None:
                self._error_ticks += 1
            elif aborted:
                self._aborted_ticks += 1
            else:
                self._ok_ticks += 1
            try:
                await self._coordinator.release(claim)
            except Exception:
                logger.exception(
                    "release after tick failed: job=%s",
                    self._name,
                )
            # 常态 DEBUG（1Hz 任务降噪）；异常拍/慢拍/周期心跳保留 INFO
            heartbeat = self._ticks % _TICK_HEARTBEAT_EVERY == 0
            notable = (
                not ok or aborted or timed_out or error_text is not None
                or duration_ms > _TICK_SLOW_MS
            )
            log_tick_done = (
                logger.info if notable or heartbeat else logger.debug
            )
            log_tick_done(
                "tick done: job=%s instance=%s ok=%s aborted=%s timed_out=%s "
                "delay_ms=%.1f duration_ms=%.1f ticks=%d",
                self._name,
                self._instance_id,
                ok,
                aborted,
                timed_out,
                delay_ms,
                duration_ms,
                self._ticks,
            )
