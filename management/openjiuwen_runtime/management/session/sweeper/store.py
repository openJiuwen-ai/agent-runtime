# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""会话到期扫描与原子解绑（四处）。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from openjiuwen_runtime.foundation.log import get_logger

from . import keys

logger = get_logger(__name__)

# KEYS[1]=session:{id} KEYS[2]=scope:sessions KEYS[3]=pod:sessions KEYS[4]=session_expiry
# ARGV[1]=session_id ARGV[2]=free_channel
_EVICT_LUA = """
local sid = ARGV[1]
local free_ch = ARGV[2]
if redis.call('EXISTS', KEYS[1]) == 0 then
    redis.call('ZREM', KEYS[4], sid)
    return {0, '', '', -1}
end
local service_id = redis.call('HGET', KEYS[1], 'service_id') or ''
local endpoint_id = redis.call('HGET', KEYS[1], 'endpoint_id') or ''
redis.call('SREM', KEYS[2], sid)
redis.call('SREM', KEYS[3], sid)
redis.call('ZREM', KEYS[4], sid)
redis.call('DEL', KEYS[1])
local remaining = redis.call('SCARD', KEYS[3])
if free_ch ~= '' then
    redis.call('PUBLISH', free_ch, '1')
end
return {1, service_id, endpoint_id, remaining}
"""


@dataclass(frozen=True)
class EvictResult:
    session_id: str
    evicted: bool
    service_id: str
    endpoint_id: str
    pod_remaining: int


class ExpiryStore:
    """读写 session_expiry / session / scope / pod 集合。"""

    def __init__(self, redis: Any, *, idle_notify_ttl_sec: int = 60) -> None:
        self._redis = redis
        self._idle_notify_ttl_sec = max(int(idle_notify_ttl_sec), 1)

    async def list_expired(self, now: Optional[float] = None) -> List[str]:
        ts = int(now if now is not None else time.time())
        members: Sequence[Any] = await self._redis.zrangebyscore(keys.SESSION_EXPIRY, "-inf", ts)
        return [m.decode() if isinstance(m, (bytes, bytearray)) else str(m) for m in members]

    async def evict(self, session_id: str) -> EvictResult:
        """原子解绑四处，并 publish scope free。"""
        sess = keys.session_key(session_id)
        # 先读定位 keys（若不存在，Lua 内也会处理 ZREM）
        meta = await self._redis.hgetall(sess)
        meta = _normalize_hash(meta)
        service_id = meta.get("service_id", "")
        endpoint_id = meta.get("endpoint_id", "")

        scope_key = keys.scope_sessions_key(service_id) if service_id else sess + ":noop_scope"
        pod_key = (
            keys.pod_sessions_key(service_id, endpoint_id)
            if service_id and endpoint_id
            else sess + ":noop_pod"
        )
        free_ch = keys.scope_free_channel(service_id) if service_id else ""

        raw = await self._redis.eval(
            _EVICT_LUA,
            4,
            sess,
            scope_key,
            pod_key,
            keys.SESSION_EXPIRY,
            session_id,
            free_ch,
        )
        evicted, svc, ep, remaining = _parse_evict_raw(raw)
        if not service_id:
            service_id = svc
        if not endpoint_id:
            endpoint_id = ep
        logger.info(
            "evict session=%s evicted=%s service=%s endpoint=%s pod_remaining=%s",
            session_id,
            evicted,
            service_id,
            endpoint_id,
            remaining,
        )
        return EvictResult(
            session_id=session_id,
            evicted=bool(evicted),
            service_id=service_id,
            endpoint_id=endpoint_id,
            pod_remaining=int(remaining),
        )

    async def try_mark_idle_notified(self, service_id: str, endpoint_id: str) -> bool:
        """NX EX 去重：首次通知返回 True。"""
        key = keys.pod_idle_notified_key(service_id, endpoint_id)
        ok = await self._redis.set(key, "1", nx=True, ex=self._idle_notify_ttl_sec)
        return bool(ok)


def _normalize_hash(meta: Any) -> dict[str, str]:
    if not meta:
        return {}
    if isinstance(meta, dict):
        out: dict[str, str] = {}
        for k, v in meta.items():
            kk = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
            vv = v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
            out[kk] = vv
        return out
    return {}


def _parse_evict_raw(raw: Any) -> tuple[int, str, str, int]:
    if not raw or not isinstance(raw, (list, tuple)):
        return 0, "", "", -1

    def _as_str(x: Any) -> str:
        if isinstance(x, (bytes, bytearray)):
            return x.decode()
        return str(x) if x is not None else ""

    def _as_int(x: Any) -> int:
        if isinstance(x, (bytes, bytearray)):
            return int(x.decode())
        return int(x)

    return _as_int(raw[0]), _as_str(raw[1]), _as_str(raw[2]), _as_int(raw[3])
