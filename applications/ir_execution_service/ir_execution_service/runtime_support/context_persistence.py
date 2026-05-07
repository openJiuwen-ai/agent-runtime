# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""
SessionModelContext persistence for ir_execution_service.

- Persist ONLY user input messages (role=user) into Redis as JSON.
- Key prefix / TTL / lock TTL are controlled by environment variables.
- A simple Redis distributed lock is used to prevent concurrent execution
  or stale overwrites for the same conversation_id.

This module intentionally does NOT re-implement SessionModelContext. It only
creates / restores it by using the SDK implementation.
"""

from __future__ import annotations

import json
import os
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Optional
from urllib.parse import urlparse

from openjiuwen_runtime.foundation.log import get_logger

from .alarm_logger import AlarmServerName, AlarmSeverity, log_alarm
from .interface_logger import log_client
from .runtime_env import clean_env_value, get_int_env

_log = get_logger(__name__)


def _env_prefix() -> str:
    # Use a fixed prefix to avoid cross-business key conflicts.
    # Default is explicit to indicate "workflow dialogue context".
    raw = clean_env_value("LOWCODE_CONTEXT_REDIS_KEY_PREFIX", "lowcode:workflow_context")
    raw = raw.strip(":").strip()
    return raw or "lowcode:workflow_context"


def _env_ttl_seconds() -> int:
    # TTL for persisted context state; refreshed on each save.
    return max(1, get_int_env("LOWCODE_CONTEXT_REDIS_TTL_SECONDS", 3600))


def _env_lock_ttl_seconds() -> int:
    # Lock TTL to avoid deadlock when a worker crashes.
    return max(1, get_int_env("LOWCODE_CONTEXT_REDIS_LOCK_TTL_SECONDS", 120))


def _redis_url() -> str:
    # 每个业务场景只用自己的 Redis URL；对话上下文使用默认/基础 Redis（可配前缀避免冲突）。
    url = clean_env_value("LOWCODE_DEFAULT_REDIS_URL")
    if not url:
        raise RuntimeError("Context persistence requires LOWCODE_DEFAULT_REDIS_URL.")
    return url


def _redis_dest_ip() -> str:
    url = _redis_url()
    try:
        host = urlparse(url).hostname or ""
        return socket.gethostbyname(host) if host else ""
    except Exception:
        return ""


def _context_state_key(conversation_id: str, context_id: str) -> str:
    # ctx:{prefix}:{conversation}:{context}
    return f"{_env_prefix()}:{conversation_id}:state:{context_id}"


def _lock_key(conversation_id: str) -> str:
    return f"{_env_prefix()}:{conversation_id}:lock"


# Only delete the lock if the value still matches our token (avoids deleting a successor lock after TTL expiry).
_UNLOCK_IF_TOKEN_MATCHES_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class PersistedContextState:
    """JSON-friendly payload saved in Redis."""

    # Outer mapping expected by SessionModelContext.load_state:
    # {context_id: {"messages": [...], "offload_messages": {...}}}
    states: dict[str, Any]


def _filter_user_messages(messages: Iterable[Any]) -> list[dict[str, Any]]:
    """Keep ONLY role=user messages; serialize to JSON-friendly dict."""
    out: list[dict[str, Any]] = []
    for m in messages or []:
        role = getattr(m, "role", None)
        if role != "user":
            continue
        dump = getattr(m, "model_dump", None)
        if callable(dump):
            d = dump()
            if isinstance(d, dict):
                # Ensure role/content exist for reconstruction.
                d.setdefault("role", "user")
                out.append(d)
                continue
        content = getattr(m, "content", None)
        out.append({"role": "user", "content": content})
    return out


def _restore_user_messages(message_dicts: list[dict[str, Any]]) -> list[Any]:
    """Rebuild user messages for SessionModelContext history."""
    from openjiuwen.core.foundation.llm import UserMessage

    restored: list[Any] = []
    for d in message_dicts or []:
        if not isinstance(d, dict):
            continue
        content = d.get("content")
        # UserMessage accepts role/content; extra fields are ignored by pydantic if not declared.
        restored.append(UserMessage(role="user", content=content))
    return restored


class RedisContextPersistence:
    """Persist/restore SessionModelContext state by (conversation_id, context_id)."""

    def __init__(self) -> None:
        self._redis = None

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            from redis.asyncio import Redis  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError("redis-py (redis.asyncio) is required for context persistence") from e
        url = _redis_url()
        self._redis = Redis.from_url(url, decode_responses=True)
        safe = url.split("@")[-1] if "@" in url else url
        _log.info("Context persistence Redis client ready (%s)", safe)
        return self._redis

    async def load_context(self, *, conversation_id: str, context_id: str, config: Any) -> Any:
        """
        Create SessionModelContext and restore persisted user messages (if any).

        Returns:
            SessionModelContext instance (SDK type).
        """
        from openjiuwen.core.context_engine.context.context import SessionModelContext

        ctx = SessionModelContext(
            context_id=context_id,
            session_id=conversation_id,
            config=config,
            history_messages=[],
            processors=[],
        )

        key = _context_state_key(conversation_id, context_id)
        t0 = time.perf_counter()
        raw = await self._get_redis().get(key)
        log_client(
            interface_name="redis.get",
            cost_ms=(time.perf_counter() - t0) * 1000.0,
            ok=True,
            return_code=0,
            return_info="hit" if raw else "miss",
            dest_ip=_redis_dest_ip(),
            add_info={"key": key, "scene": "workflow_context"},
        )
        if not raw:
            return ctx

        try:
            payload = json.loads(raw)
        except Exception:
            _log.warning("Context state JSON decode failed, key=%s", key, exc_info=True)
            log_alarm(
                server_name=AlarmServerName.REDIS,
                level=AlarmSeverity.MINOR,
                module="redis.get",
                message=f"Context state JSON decode failed, key={key}",
                ip=_redis_dest_ip(),
            )
            return ctx

        # Expected format: {"context_id": {"messages":[...], "offload_messages":{...}}}
        if not isinstance(payload, dict):
            return ctx
        ctx_bucket = payload.get(context_id)
        if not isinstance(ctx_bucket, dict):
            return ctx
        msg_dicts = ctx_bucket.get("messages", [])
        if not isinstance(msg_dicts, list):
            msg_dicts = []
        restored_user_messages = _restore_user_messages(msg_dicts)  # only user messages
        if restored_user_messages:
            # Seed as history; we intentionally do not restore offload cache.
            ctx = SessionModelContext(
                context_id=context_id,
                session_id=conversation_id,
                config=config,
                history_messages=restored_user_messages,
                processors=[],
            )
        return ctx

    async def save_on_interaction(self, *, conversation_id: str, context_id: str, context: Any) -> None:
        """Persist current user-message history when workflow yields interaction."""
        try:
            saved = context.save_state()
        except Exception:
            _log.warning("Context save_state failed; skip persistence", exc_info=True)
            log_alarm(
                server_name=AlarmServerName.IR_EXECUTION_SERVICE,
                level=AlarmSeverity.MINOR,
                module="context.save_state",
                message="Context save_state failed; skip persistence",
                ip="",
            )
            return

        # Keep ONLY user messages.
        msgs = []
        if isinstance(saved, dict):
            msgs = saved.get("messages", []) or []
        user_msgs = _filter_user_messages(msgs)

        payload = {context_id: {"messages": user_msgs, "offload_messages": {}}}
        key = _context_state_key(conversation_id, context_id)
        t0 = time.perf_counter()
        await self._get_redis().set(key, json.dumps(payload, ensure_ascii=False), ex=_env_ttl_seconds())
        log_client(
            interface_name="redis.set",
            cost_ms=(time.perf_counter() - t0) * 1000.0,
            ok=True,
            return_code=0,
            return_info="saved",
            dest_ip=_redis_dest_ip(),
            add_info={"key": key, "ttl": _env_ttl_seconds(), "scene": "workflow_context"},
        )

    async def delete(self, *, conversation_id: str, context_id: str) -> None:
        key = _context_state_key(conversation_id, context_id)
        try:
            t0 = time.perf_counter()
            await self._get_redis().delete(key)
            log_client(
                interface_name="redis.delete",
                cost_ms=(time.perf_counter() - t0) * 1000.0,
                ok=True,
                return_code=0,
                return_info="deleted",
                dest_ip=_redis_dest_ip(),
                add_info={"key": key, "scene": "workflow_context"},
            )
        except Exception:
            _log.warning("Context delete failed, key=%s", key, exc_info=True)
            log_alarm(
                server_name=AlarmServerName.REDIS,
                level=AlarmSeverity.MAJOR,
                module="redis.delete",
                message=f"Context delete failed, key={key}",
                ip=_redis_dest_ip(),
            )

    @asynccontextmanager
    async def conversation_lock(self, *, conversation_id: str) -> AsyncIterator[None]:
        """
        Acquire a simple distributed lock for a conversation.

        Strategy: SET lock_key value NX EX ttl
        """
        redis = self._get_redis()
        key = _lock_key(conversation_id)
        ttl = _env_lock_ttl_seconds()
        token = f"{os.getpid()}-{_now_ms()}"

        acquired = False
        try:
            # redis-py returns True/False for set(..., nx=True)
            t0 = time.perf_counter()
            acquired = bool(await redis.set(key, token, ex=ttl, nx=True))
            log_client(
                interface_name="redis.setnx",
                cost_ms=(time.perf_counter() - t0) * 1000.0,
                ok=acquired,
                return_code=0 if acquired else 1,
                return_info="acquired" if acquired else "busy",
                dest_ip=_redis_dest_ip(),
                add_info={"key": key, "ttl": ttl, "scene": "workflow_context_lock"},
            )
            if not acquired:
                log_alarm(
                    server_name=AlarmServerName.REDIS,
                    level=AlarmSeverity.MINOR,
                    module="redis.setnx",
                    message=f"conversation lock busy: {conversation_id}",
                    ip=_redis_dest_ip(),
                )
                raise RuntimeError(f"conversation lock busy: {conversation_id}")
            yield
        finally:
            if acquired:
                try:
                    t1 = time.perf_counter()
                    await redis.eval(_UNLOCK_IF_TOKEN_MATCHES_LUA, 1, key, token)
                    log_client(
                        interface_name="redis.eval_unlock",
                        cost_ms=(time.perf_counter() - t1) * 1000.0,
                        ok=True,
                        return_code=0,
                        return_info="released",
                        dest_ip=_redis_dest_ip(),
                        add_info={"key": key, "scene": "workflow_context_lock"},
                    )
                except Exception:
                    _log.warning("Lock release failed, key=%s", key, exc_info=True)
                    log_alarm(
                        server_name=AlarmServerName.REDIS,
                        level=AlarmSeverity.MAJOR,
                        module="redis.eval_unlock",
                        message=f"Lock release failed, key={key}",
                        ip=_redis_dest_ip(),
                    )

