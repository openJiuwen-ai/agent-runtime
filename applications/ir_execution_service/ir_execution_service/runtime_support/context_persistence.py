# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

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
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .memory_engine_start import MemoryEngineManager
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
    # 与 MemoryEngine 的 Redis KV 一致：显式 URL 优先，否则用 REDIS_HOST/... 组装
    url = clean_env_value("REDIS_URL")
    if url:
        return url
    return MemoryEngineManager.build_redis_url()


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
        raw = await self._get_redis().get(key)
        if not raw:
            return ctx

        try:
            payload = json.loads(raw)
        except Exception:
            _log.warning("Context state JSON decode failed, key=%s", key, exc_info=True)
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
            return

        # Keep ONLY user messages.
        msgs = []
        if isinstance(saved, dict):
            msgs = saved.get("messages", []) or []
        user_msgs = _filter_user_messages(msgs)

        payload = {context_id: {"messages": user_msgs, "offload_messages": {}}}
        key = _context_state_key(conversation_id, context_id)
        await self._get_redis().set(key, json.dumps(payload, ensure_ascii=False), ex=_env_ttl_seconds())

    async def delete(self, *, conversation_id: str, context_id: str) -> None:
        key = _context_state_key(conversation_id, context_id)
        try:
            await self._get_redis().delete(key)
        except Exception:
            _log.warning("Context delete failed, key=%s", key, exc_info=True)

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
            acquired = bool(await redis.set(key, token, ex=ttl, nx=True))
            if not acquired:
                raise RuntimeError(f"conversation lock busy: {conversation_id}")
            yield
        finally:
            if acquired:
                try:
                    await redis.eval(_UNLOCK_IF_TOKEN_MATCHES_LUA, 1, key, token)
                except Exception:
                    _log.warning("Lock release failed, key=%s", key, exc_info=True)

