# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Redis-backed state store for the dispatch subsystem."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from .bus import EventBus, RedisEventBus
from .config import DispatchSettings
from .exceptions import CapacityAllocationError
from .models import PodInfo, SessionInfo, SessionState

try:
    import redis.asyncio as redis_asyncio
    from redis.exceptions import NoScriptError
except ModuleNotFoundError:  # pragma: no cover - exercised in environments without Redis installed
    redis_asyncio = None

    class NoScriptError(Exception):
        """Fallback error used when redis-py is unavailable."""


ALLOCATE_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return {err='pod not found'} end
local pod = cjson.decode(raw)
if pod.state == 'draining' then return {err='draining'} end
local capacity = tonumber(pod.capacity or ARGV[3] or 0)
local concurrency = tonumber(ARGV[1])
local session_id = ARGV[2]
local found = false
for _, sid in ipairs(pod.bound_sessions or {}) do
  if sid == session_id then
    found = true
    break
  end
end
if (not found) and (tonumber(pod.allocated or 0) + concurrency > capacity) then
  return {err='insufficient'}
end
if not found then
  pod.allocated = tonumber(pod.allocated or 0) + concurrency
  table.insert(pod.bound_sessions, session_id)
end
pod.idle_since = cjson.null
if tonumber(pod.allocated or 0) >= capacity then
  pod.state = 'full'
else
  pod.state = 'serving'
end
redis.call('SET', KEYS[1], cjson.encode(pod))
return cjson.encode(pod)
"""


RELEASE_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return false end
local pod = cjson.decode(raw)
local session_id = ARGV[2]
local concurrency = tonumber(ARGV[1])
local now = tonumber(ARGV[3])
local kept = {}
local removed = false
for _, sid in ipairs(pod.bound_sessions or {}) do
  if sid == session_id then
    removed = true
  else
    table.insert(kept, sid)
  end
end
pod.bound_sessions = kept
if removed then
  pod.allocated = math.max(0, tonumber(pod.allocated or 0) - concurrency)
end
local capacity = tonumber(pod.capacity or ARGV[4] or 0)
if #kept == 0 then
  pod.state = 'serving'
  pod.idle_since = now
elseif tonumber(pod.allocated or 0) >= capacity then
  pod.state = 'full'
  pod.idle_since = cjson.null
else
  pod.state = 'serving'
  pod.idle_since = cjson.null
end
redis.call('SET', KEYS[1], cjson.encode(pod))
return cjson.encode(pod)
"""


class RedisDispatchStore:
    """Dispatch state store with optional lazy Redis initialization."""

    SESSION_PREFIX = "ws:session:"
    SESSION_INDEX_KEY = "ws:session_index"
    SESSION_WS_COUNT_PREFIX = "ws:session_ws_count:"
    POD_PREFIX = "ws:pod:"
    POD_INDEX_KEY = "ws:pod_index"

    def __init__(
        self,
        settings: DispatchSettings,
        client: Any | None = None,
        bus: EventBus | None = None,
    ):
        self.settings = settings
        self._redis = client
        self.bus = bus
        self._allocate_sha: str | None = None
        self._release_sha: str | None = None

    async def connect(self) -> None:
        if self._redis is None:
            if redis_asyncio is None:
                raise ModuleNotFoundError("redis is not installed; install project dependencies first")
            self._redis = redis_asyncio.from_url(self.settings.redis_url, decode_responses=True)
        if self.bus is None:
            self.bus = RedisEventBus(self._redis)
        if hasattr(self._redis, "script_load"):
            self._allocate_sha = await self._redis.script_load(ALLOCATE_LUA)
            self._release_sha = await self._redis.script_load(RELEASE_LUA)

    async def close(self) -> None:
        if self.bus is not None:
            await self.bus.close()
        if self._redis is not None and hasattr(self._redis, "aclose"):
            await self._redis.aclose()

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def load_config(self) -> dict[str, str]:
        return await self._redis.hgetall(self.settings.config_hash_key)

    async def get_session(self, session_id: str) -> Optional[SessionInfo]:
        raw = await self._redis.get(f"{self.SESSION_PREFIX}{session_id}")
        return SessionInfo.model_validate_json(raw) if raw else None

    async def save_session(self, info: SessionInfo) -> None:
        await self._redis.set(
            f"{self.SESSION_PREFIX}{info.session_id}",
            info.model_dump_json(),
            ex=max(info.ttl_seconds + 300, 300),
        )
        await self._redis.sadd(self.SESSION_INDEX_KEY, info.session_id)

    async def delete_session(self, session_id: str) -> None:
        await self._redis.delete(f"{self.SESSION_PREFIX}{session_id}")
        await self._redis.delete(f"{self.SESSION_WS_COUNT_PREFIX}{session_id}")
        await self._redis.srem(self.SESSION_INDEX_KEY, session_id)

    async def list_sessions(self) -> list[SessionInfo]:
        session_ids = sorted(await self._redis.smembers(self.SESSION_INDEX_KEY))
        sessions: list[SessionInfo] = []
        for session_id in session_ids:
            session = await self.get_session(session_id)
            if session is None:
                await self._redis.srem(self.SESSION_INDEX_KEY, session_id)
                continue
            sessions.append(session)
        return sessions

    async def get_pod(self, pod_id: str) -> Optional[PodInfo]:
        raw = await self._redis.get(f"{self.POD_PREFIX}{pod_id}")
        return PodInfo.model_validate_json(raw) if raw else None

    async def save_pod(self, info: PodInfo) -> None:
        await self._redis.set(f"{self.POD_PREFIX}{info.pod_id}", info.model_dump_json())
        await self._redis.sadd(self.POD_INDEX_KEY, info.pod_id)

    async def remove_pod(self, pod_id: str) -> None:
        await self._redis.delete(f"{self.POD_PREFIX}{pod_id}")
        await self._redis.srem(self.POD_INDEX_KEY, pod_id)

    async def all_pod_ids(self) -> set[str]:
        return set(await self._redis.smembers(self.POD_INDEX_KEY))

    async def list_pods(self) -> list[PodInfo]:
        pod_ids = sorted(await self._redis.smembers(self.POD_INDEX_KEY))
        pods: list[PodInfo] = []
        for pod_id in pod_ids:
            pod = await self.get_pod(pod_id)
            if pod is None:
                await self._redis.srem(self.POD_INDEX_KEY, pod_id)
                continue
            pods.append(pod)
        return pods

    async def all_pods(self) -> list[PodInfo]:
        return await self.list_pods()

    async def allocate_session(self, pod: PodInfo, session: SessionInfo) -> PodInfo:
        raw = await self._evalsha(
            self._allocate_sha,
            ALLOCATE_LUA,
            keys=[f"{self.POD_PREFIX}{pod.pod_id}"],
            args=[session.concurrency, session.session_id, pod.capacity],
        )
        if not raw:
            raise CapacityAllocationError(f"failed to allocate session {session.session_id} on pod {pod.pod_id}")

        bound = session.model_copy(
            update={
                "bound_pod_id": pod.pod_id,
                "state": SessionState.RUNNING,
                "active_ws_count": max(session.active_ws_count, 0),
                "last_active_at": time.time(),
                "expire_at": 0.0,
                "orphaned": False,
            }
        )
        await self.save_session(bound)
        return PodInfo.model_validate_json(raw)

    async def release_session_capacity(self, session: SessionInfo) -> PodInfo | None:
        if not session.bound_pod_id:
            return None

        raw = await self._evalsha(
            self._release_sha,
            RELEASE_LUA,
            keys=[f"{self.POD_PREFIX}{session.bound_pod_id}"],
            args=[session.concurrency, session.session_id, time.time(), self.settings.concurrent_num],
        )
        released = SessionInfo.model_validate(
            session.model_dump()
            | {
                "bound_pod_id": None,
                "state": SessionState.IDLE,
                "active_ws_count": 0,
                "expire_at": 0.0,
                "last_active_at": time.time(),
                "orphaned": False,
            }
        )
        await self.save_session(released)
        if not raw:
            return None
        return PodInfo.model_validate_json(raw)

    async def mark_session_running(self, session_id: str) -> Optional[SessionInfo]:
        session = await self.get_session(session_id)
        if session is None:
            return None
        updated = session.model_copy(
            update={
                "state": SessionState.RUNNING,
                "expire_at": 0.0,
                "last_active_at": time.time(),
                "orphaned": False,
            }
        )
        await self.save_session(updated)
        return updated

    async def enter_ttl_waiting(self, session_id: str) -> Optional[SessionInfo]:
        session = await self.get_session(session_id)
        if session is None:
            return None
        updated = session.model_copy(
            update={
                "state": SessionState.TTL_WAITING,
                "expire_at": time.time() + session.ttl_seconds,
                "last_active_at": time.time(),
            }
        )
        await self.save_session(updated)
        return updated

    async def mark_session_orphaned(self, session_id: str) -> Optional[SessionInfo]:
        session = await self.get_session(session_id)
        if session is None:
            return None
        updated = session.model_copy(
            update={
                "bound_pod_id": None,
                "state": SessionState.ORPHANED,
                "active_ws_count": 0,
                "expire_at": 0.0,
                "orphaned": True,
                "last_active_at": time.time(),
            }
        )
        await self.save_session(updated)
        return updated

    async def incr_ws_count(self, session_id: str) -> int:
        count = int(await self._redis.incr(f"{self.SESSION_WS_COUNT_PREFIX}{session_id}"))
        session = await self.get_session(session_id)
        if session is not None:
            await self.save_session(
                session.model_copy(
                    update={
                        "state": SessionState.RUNNING,
                        "active_ws_count": count,
                        "expire_at": 0.0,
                        "last_active_at": time.time(),
                    }
                )
            )
        return count

    async def decr_ws_count(self, session_id: str) -> int:
        key = f"{self.SESSION_WS_COUNT_PREFIX}{session_id}"
        count = int(await self._redis.decr(key))
        if count <= 0:
            count = 0
            await self._redis.set(key, 0, ex=60)

        session = await self.get_session(session_id)
        if session is not None:
            await self.save_session(
                session.model_copy(
                    update={
                        "active_ws_count": count,
                        "last_active_at": time.time(),
                    }
                )
            )
        return count

    async def enqueue_scale_event(
        self,
        reason: str,
        session_id: str | None = None,
        concurrency: int | None = None,
        demand: int = 1,
        pod_id: str | None = None,
    ) -> str:
        payload = {
            "reason": reason,
            "created_at": str(time.time()),
            "demand": str(demand),
        }
        if session_id is not None:
            payload["session_id"] = session_id
        if concurrency is not None:
            payload["concurrency"] = str(concurrency)
        if pod_id is not None:
            payload["pod_id"] = pod_id
        return await self.bus.enqueue(self.settings.scale_event_stream, payload)

    async def request_scale_up(
        self,
        reason: str,
        session_id: str | None = None,
        concurrency: int | None = None,
        pod_id: str | None = None,
    ) -> str:
        return await self.enqueue_scale_event(
            reason=reason,
            session_id=session_id,
            concurrency=concurrency,
            demand=1,
            pod_id=pod_id,
        )

    async def enqueue_admin_event(self, reason: str, **payload: Any) -> str:
        fields = {
            "reason": reason,
            "created_at": str(time.time()),
        }
        for key, value in payload.items():
            if value is None:
                continue
            fields[key] = str(value)
        return await self.bus.enqueue(self.settings.admin_event_stream, fields)

    async def publish_pod_ready(self, pod_id: str) -> None:
        await self.bus.publish(self.settings.pod_ready_channel, pod_id)

    async def wait_for_pod_ready(self, timeout: float) -> str | None:
        if timeout <= 0:
            return None
        subscription = self.bus.subscribe(self.settings.pod_ready_channel)
        try:
            async with asyncio.timeout(timeout):
                async for _, payload in subscription:
                    return payload
        except TimeoutError:
            return None
        finally:
            aclose = getattr(subscription, "aclose", None)
            if callable(aclose):
                await aclose()
        return None

    async def load_cursor(self, cursor_key: str) -> str:
        cursor = await self._redis.get(cursor_key)
        return cursor or "0-0"

    async def save_cursor(self, cursor_key: str, cursor: str) -> None:
        await self._redis.set(cursor_key, cursor)

    async def load_scale_cursor(self) -> str:
        return await self.load_cursor(self.settings.scale_cursor_key)

    async def save_scale_cursor(self, cursor: str) -> None:
        await self.save_cursor(self.settings.scale_cursor_key, cursor)

    async def load_admin_cursor(self) -> str:
        return await self.load_cursor(self.settings.admin_cursor_key)

    async def save_admin_cursor(self, cursor: str) -> None:
        await self.save_cursor(self.settings.admin_cursor_key, cursor)

    async def consume_events(
        self,
        topic: str,
        cursor: str,
        block_ms: int,
        count: int = 100,
    ) -> list[tuple[str, dict[str, str]]]:
        return await self.bus.consume(topic, cursor, count=count, block_ms=block_ms)

    async def consume_scale_events(
        self,
        cursor: str,
        block_ms: int,
        count: int = 100,
    ) -> list[tuple[str, dict[str, str]]]:
        return await self.consume_events(self.settings.scale_event_stream, cursor, block_ms=block_ms, count=count)

    async def consume_admin_events(
        self,
        cursor: str,
        block_ms: int,
        count: int = 16,
    ) -> list[tuple[str, dict[str, str]]]:
        return await self.consume_events(self.settings.admin_event_stream, cursor, block_ms=block_ms, count=count)

    async def _evalsha(self, sha: str | None, script: str, keys: list[str], args: list[Any]) -> Any:
        if sha is None:
            if hasattr(self._redis, "eval"):
                return await self._redis.eval(script, len(keys), *keys, *args)
            raise CapacityAllocationError("redis client does not support Lua script execution")

        try:
            return await self._redis.evalsha(sha, len(keys), *keys, *args)
        except NoScriptError:
            reloaded = await self._redis.script_load(script)
            if script == ALLOCATE_LUA:
                self._allocate_sha = reloaded
            elif script == RELEASE_LUA:
                self._release_sha = reloaded
            return await self._redis.evalsha(reloaded, len(keys), *keys, *args)
