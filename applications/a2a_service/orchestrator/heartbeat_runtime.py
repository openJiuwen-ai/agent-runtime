from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from channels.dict_to_a2a import dict_to_a2a


@dataclass
class _HeartbeatSession:
    request_id: str
    source: str
    started_monotonic: float
    stopped: bool
    task: asyncio.Task[Any] | None = None


class HeartbeatRuntimeManager:
    """会话级心跳运行时。

    负责同一请求内心跳状态机、normal 定时发送、timeout 终止、
    以及 end 事件前端只下发一次的幂等控制。
    """

    def __init__(
        self,
        *,
        conv_id: str,
        task_id: str,
        event_queue: Any,
        redis: Any | None = None,
        interval_seconds: int = 15,
        timeout_seconds: int = 1800,
        seq_ttl_seconds: int = 1800,
    ) -> None:
        self.conv_id = conv_id
        self.task_id = task_id
        self.event_queue = event_queue
        self.redis = redis
        self.interval_seconds = max(int(interval_seconds), 1)
        self.timeout_seconds = max(int(timeout_seconds), self.interval_seconds + 1)
        self.seq_ttl_seconds = max(int(seq_ttl_seconds), 1)

        self._sessions: dict[str, _HeartbeatSession] = {}
        self._end_forwarded: set[str] = set()
        self._local_seq: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def bind_context(
        self,
        *,
        task_id: str,
        event_queue: Any,
        conv_id: str | None = None,
    ) -> None:
        """在新一轮请求开始时重绑下发上下文（task/event_queue）。"""
        if conv_id is not None:
            self.conv_id = conv_id
        self.task_id = task_id
        self.event_queue = event_queue

    async def start_heartbeat(self, request_id: str, source: str = "a2a_service") -> dict[str, Any]:
        request_id = str(request_id or self.conv_id)
        await self.stop_heartbeat(request_id, reason="restart", mark_end=False)

        session = _HeartbeatSession(
            request_id=request_id,
            source=source,
            started_monotonic=time.monotonic(),
            stopped=False,
        )
        session.task = asyncio.create_task(self._normal_loop(request_id))
        async with self._lock:
            self._sessions[request_id] = session

        return {"success": True, "code": "HEARTBEAT_STARTED"}

    async def stop_heartbeat(
        self,
        request_id: str,
        *,
        reason: str = "agent_end",
        mark_end: bool = True,
    ) -> dict[str, Any]:
        request_id = str(request_id or self.conv_id)
        session: _HeartbeatSession | None
        should_forward_end = False

        async with self._lock:
            if mark_end:
                should_forward_end = request_id not in self._end_forwarded
                if should_forward_end:
                    self._end_forwarded.add(request_id)

            session = self._sessions.get(request_id)
            if session is None or session.stopped:
                return {
                    "success": True,
                    "code": "HEARTBEAT_STOP_NOOP",
                    "reason": reason,
                    "forward_to_frontend": should_forward_end,
                }

            session.stopped = True
            task = session.task

        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        return {
            "success": True,
            "code": "HEARTBEAT_STOPPED",
            "reason": reason,
            "forward_to_frontend": should_forward_end,
        }

    async def notify_heartbeat(
        self,
        *,
        request_id: str,
        heartbeat_type: str,
        status: str,
        source: str,
    ) -> dict[str, Any]:
        request_id = str(request_id or self.conv_id)
        heartbeat_type = str(heartbeat_type or "")

        if heartbeat_type == "end":
            async with self._lock:
                if request_id in self._end_forwarded:
                    return {
                        "success": True,
                        "code": "HEARTBEAT_END_SUPPRESSED",
                        "forward_to_frontend": False,
                    }
                self._end_forwarded.add(request_id)

        seq = await self._next_seq(request_id)
        frame = {
            "type": "heartbeat",
            "data": {
                "contract_version": "HB-CONTRACT-1.0",
                "request_id": request_id,
                "heartbeat_type": heartbeat_type,
                "status": status,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": source,
                "seq": seq,
            },
        }
        await self.event_queue.enqueue_event(dict_to_a2a(frame, self.task_id, self.conv_id))
        return {
            "success": True,
            "code": "HEARTBEAT_EMITTED",
            "forward_to_frontend": True,
            "seq": seq,
        }

    async def attach_seq(self, raw_event: dict[str, Any], request_id: str) -> None:
        data = raw_event.get("data") if isinstance(raw_event, dict) else None
        if not isinstance(data, dict):
            return
        if isinstance(data.get("seq"), int):
            return
        data["seq"] = await self._next_seq(request_id)

    async def cleanup(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        cancelled_tasks: list[asyncio.Task[Any]] = []
        for session in sessions:
            if session.task is not None and not session.task.done():
                session.task.cancel()
                cancelled_tasks.append(session.task)

        for task in cancelled_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _normal_loop(self, request_id: str) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)

            async with self._lock:
                session = self._sessions.get(request_id)
                if session is None or session.stopped:
                    return
                elapsed = time.monotonic() - session.started_monotonic

            if elapsed >= self.timeout_seconds:
                try:
                    await self.notify_heartbeat(
                        request_id=request_id,
                        heartbeat_type="end",
                        status="timeout",
                        source="a2a_service",
                    )
                except Exception as exc:
                    logger.warning("[HeartbeatRuntime] timeout end 发送失败: {}", exc)
                await self.stop_heartbeat(request_id, reason="timeout", mark_end=False)
                return

            try:
                await self.notify_heartbeat(
                    request_id=request_id,
                    heartbeat_type="normal",
                    status="processing",
                    source="a2a_service",
                )
            except Exception as exc:
                logger.warning("[HeartbeatRuntime] normal 发送失败: {}", exc)

    async def _next_seq(self, request_id: str) -> int:
        key = f"session:{request_id}:heartbeat_seq"

        if self.redis is not None and hasattr(self.redis, "incr"):
            try:
                seq = int(await self.redis.incr(key))
                if seq == 1 and hasattr(self.redis, "expire"):
                    await self.redis.expire(key, self.seq_ttl_seconds)
                return seq
            except Exception as exc:
                logger.warning("[HeartbeatRuntime] Redis seq 分配失败，降级本地计数: {}", exc)

        async with self._lock:
            seq = self._local_seq.get(request_id, 0) + 1
            self._local_seq[request_id] = seq
            return seq
