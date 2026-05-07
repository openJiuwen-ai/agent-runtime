# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

from __future__ import annotations

"""
IR 内容拉取（二级缓存实现）：

- 进程内 LRU（可选，带 TTL）
- Redis（二级缓存，可选，带 TTL + 分布式锁避免并发击穿）
- OBS（最终源）

开关要求：
- 内存缓存与 Redis 缓存各有独立开关；开关同时控制读写（即关闭后既不读也不写）。
"""

import asyncio
import hashlib
import json
import os
import socket
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from openjiuwen_runtime.foundation.log import get_logger

from .alarm_logger import AlarmServerName, AlarmSeverity, log_alarm
from .interface_logger import log_client
from .runtime_env import clean_env_value
from .studio_secrets import resolve_secret_env


_SECRET_HEADER_BOOL_TRUE = {"1", "true", "yes", "on"}

_LOG = get_logger(__name__)

try:  # redis-py async client exceptions (best-effort import)
    from redis.exceptions import RedisError as _RedisClientError  # type: ignore
except Exception:  # pragma: no cover

    class _RedisClientError(Exception):  # type: ignore[misc]
        """Fallback when redis-py isn't installed / exceptions unavailable."""

# NOTE: redis-py 的 RedisError 在部分版本继承自 OSError；不要在同一个 except 里同时捕获父子类（G.ERR.09）。
_REDIS_OP_ERRORS: tuple[type[BaseException], ...] = (_RedisClientError, TimeoutError)



# Only delete the lock if the value still matches our token.
_UNLOCK_IF_TOKEN_MATCHES_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""


def _bool_env(name: str, default: bool) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if not v:
        return default
    return v in _SECRET_HEADER_BOOL_TRUE


def _normalize_object_key(ir_path: str) -> str:
    ir_path_stripped = (ir_path or "").strip()
    if not ir_path_stripped:
        raise HTTPException(status_code=400, detail="ir_path is empty")
    object_key = ir_path_stripped.replace("\\", "/").lstrip("/")
    key_segments = [segment for segment in object_key.split("/") if segment and segment != "."]
    if any(segment == ".." for segment in key_segments):
        raise HTTPException(status_code=400, detail="ir_path must not contain '..'")
    if not key_segments:
        raise HTTPException(status_code=400, detail="invalid ir_path as object key")
    return object_key


def _dedup_token(bucket: str, object_key: str) -> str:
    return hashlib.sha256(f"{bucket}\n{object_key}".encode("utf-8")).hexdigest()


def _memory_enabled() -> bool:
    return _bool_env("LOWCODE_IR_MEMORY_CACHE_ENABLED", True)


def _memory_lru_max() -> int:
    raw = (os.environ.get("LOWCODE_IR_MEMORY_LRU_MAX") or "256").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 256


def _memory_ttl_seconds() -> int:
    raw = (os.environ.get("LOWCODE_IR_MEMORY_TTL_SECONDS") or "300").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 300


def _redis_enabled() -> bool:
    return _bool_env("LOWCODE_IR_REDIS_CACHE_ENABLED", True)


def _redis_ttl_seconds() -> int:
    raw = (os.environ.get("LOWCODE_IR_REDIS_TTL_SECONDS") or "86400").strip()
    try:
        return max(60, int(raw))
    except ValueError:
        return 86400


def _redis_lock_ttl_seconds() -> int:
    raw = (os.environ.get("LOWCODE_IR_REDIS_LOCK_TTL_SECONDS") or "45").strip()
    try:
        return max(5, int(raw))
    except ValueError:
        return 45


def _cross_lock_max_wait_s() -> float:
    raw = (os.environ.get("LOWCODE_IR_FETCH_LOCK_TIMEOUT_S") or "120").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 120.0


def _local_lock_ttl_seconds() -> float:
    raw = (os.environ.get("LOWCODE_IR_LOCAL_LOCK_TTL_SECONDS") or "300").strip()
    try:
        return max(10.0, float(raw))
    except ValueError:
        return 300.0


def _build_redis_url() -> str | None:
    if not _redis_enabled():
        return None
    # 每个业务场景只用自己的 Redis URL；IR 二级缓存使用默认/基础 Redis（可配 key 前缀避免冲突）。
    u = (os.environ.get("LOWCODE_DEFAULT_REDIS_URL") or "").strip()
    return u or None


class _IrJsonLru:
    def __init__(self) -> None:
        # dedup -> (raw_json, expires_at_monotonic)
        self._data: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, dedup: str) -> str | None:
        cap = _memory_lru_max()
        if not _memory_enabled() or cap <= 0:
            return None
        ttl = float(_memory_ttl_seconds())
        now = asyncio.get_running_loop().time()
        async with self._lock:
            cell = self._data.get(dedup)
            if not cell:
                return None
            raw_json, exp = cell
            if exp <= now:
                self._data.pop(dedup, None)
                return None
            # sliding TTL：命中后延长
            self._data.move_to_end(dedup)
            self._data[dedup] = (raw_json, now + ttl)
            return raw_json

    async def put(self, dedup: str, raw_json: str) -> None:
        cap = _memory_lru_max()
        if not _memory_enabled() or cap <= 0:
            return
        now = asyncio.get_running_loop().time()
        ttl = float(_memory_ttl_seconds())
        async with self._lock:
            self._data.pop(dedup, None)
            self._data[dedup] = (raw_json, now + ttl)
            while len(self._data) > cap:
                self._data.popitem(last=False)


_ir_lru = _IrJsonLru()
_local_fetch_locks: dict[str, tuple[asyncio.Lock, float]] = {}
_local_locks_guard = asyncio.Lock()
_redis_client: Any = None
_redis_lock_init = asyncio.Lock()


async def _get_local_fetch_lock(dedup: str) -> asyncio.Lock:
    # TTL cleanup to avoid unbounded growth when many different IRs are requested.
    # Local lock TTL is a per-process memory hygiene knob; do not mix with cross-process lock timeout.
    ttl_seconds = _local_lock_ttl_seconds()
    now = asyncio.get_running_loop().time()
    async with _local_locks_guard:
        # Best-effort cleanup (bounded work): drop expired locks that are not currently held.
        if _local_fetch_locks:
            expired: list[str] = []
            for k, (lk, last_used) in _local_fetch_locks.items():
                if (now - last_used) > ttl_seconds and not lk.locked():
                    expired.append(k)
            for k in expired:
                _local_fetch_locks.pop(k, None)

        cell = _local_fetch_locks.get(dedup)
        if cell is None:
            lk = asyncio.Lock()
            _local_fetch_locks[dedup] = (lk, now)
            return lk
        lk, _last = cell
        _local_fetch_locks[dedup] = (lk, now)
        return lk


async def _get_redis():
    global _redis_client
    url = _build_redis_url()
    if not url:
        return None
    async with _redis_lock_init:
        if _redis_client is None:
            try:
                import redis.asyncio as redis_mod  # type: ignore
            except Exception:
                # 未安装 redis 包时直接退化（不抛错）
                return None
            _redis_client = redis_mod.from_url(url, decode_responses=True)
        return _redis_client


def _ir_redis_key_root() -> str:
    """可配置 Redis 命名空间根，避免多业务/多服务共用 DB 时 key 冲突。默认与历史实现一致为 ir_exec。"""
    raw = clean_env_value("LOWCODE_IR_REDIS_KEY_PREFIX", "ir_exec").strip()
    if not raw:
        raw = "ir_exec"
    return raw.strip(":").strip()


def _data_key(dedup: str) -> str:
    return f"{_ir_redis_key_root()}:data:{dedup}"


def _lock_key(dedup: str) -> str:
    return f"{_ir_redis_key_root()}:lock:{dedup}"


def _parse_root(raw_json: str) -> dict[str, Any]:
    try:
        root = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"ir content is not valid json: {exc}") from exc
    if not isinstance(root, dict):
        raise HTTPException(status_code=502, detail="ir root must be a json object")
    return root


async def _read_obs_bytes(bucket: str, object_key: str) -> bytes:
    from openjiuwen.core.foundation.store.object.aioboto_storage_client import AioBotoClient

    storage = AioBotoClient(
        server=(os.environ.get("OBS_SERVER") or "").strip() or None,
        access_key_id=resolve_secret_env("OBS_ACCESS_KEY_ID", ""),
        secret_access_key=resolve_secret_env("OBS_SECRET_ACCESS_KEY", ""),
        region_name=(os.environ.get("OBS_REGION") or "").strip() or None,
    )
    t0 = time.perf_counter()
    try:
        # 直接走 S3 get_object 读取 bytes，避免任何临时文件落盘。
        async with storage.create_client() as s3:
            resp = await s3.get_object(Bucket=bucket, Key=object_key)
            body = resp.get("Body")
            if body is None:
                raise HTTPException(status_code=502, detail=f"obs get_object missing Body: {object_key}")
            out = await body.read()
            obs_dest = ""
            raw_server = (os.environ.get("OBS_SERVER") or "").strip()
            if raw_server:
                try:
                    host = urlparse(raw_server).hostname or raw_server
                    obs_dest = socket.gethostbyname(host)
                except Exception:
                    obs_dest = ""
            log_client(
                interface_name="obs.get_object",
                cost_ms=(time.perf_counter() - t0) * 1000.0,
                ok=True,
                return_code=0,
                return_info="success",
                dest_ip=obs_dest,
                add_info={"bucket": bucket, "object_key": object_key, "size": len(out)},
            )
            return out
    except Exception as exc:
        obs_dest = ""
        raw_server = (os.environ.get("OBS_SERVER") or "").strip()
        if raw_server:
            try:
                host = urlparse(raw_server).hostname or raw_server
                obs_dest = socket.gethostbyname(host)
            except Exception:
                obs_dest = ""
        log_client(
            interface_name="obs.get_object",
            cost_ms=(time.perf_counter() - t0) * 1000.0,
            ok=False,
            return_code=2002,
            return_info=str(exc),
            dest_ip=obs_dest,
            add_info={"bucket": bucket, "object_key": object_key},
        )
        log_alarm(
            server_name=AlarmServerName.OBS,
            level=AlarmSeverity.MAJOR,
            module="obs.get_object",
            message=str(exc),
            ip=obs_dest,
        )
        # obs/s3 侧异常统一转为 502
        raise HTTPException(status_code=502, detail=f"failed to download ir from obs: {object_key}") from exc


async def _load_under_locks(bucket: str, object_key: str, dedup: str) -> dict[str, Any]:
    r = await _get_redis()
    lock_ttl = _redis_lock_ttl_seconds()
    ttl = _redis_ttl_seconds()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _cross_lock_max_wait_s()
    redis_lock_held = False
    redis_lock_token: str | None = None

    async with (await _get_local_fetch_lock(dedup)):
        # 1) memory
        hit = await _ir_lru.get(dedup)
        if hit is not None:
            return _parse_root(hit)

        # 2) redis (read)
        if r is not None and _redis_enabled():
            redis_dest = ""
            raw_url = (os.environ.get("LOWCODE_DEFAULT_REDIS_URL") or "").strip()
            if raw_url:
                try:
                    host = urlparse(raw_url).hostname or ""
                    redis_dest = socket.gethostbyname(host) if host else ""
                except Exception:
                    redis_dest = ""
            try:
                t_redis = time.perf_counter()
                cached = await r.get(_data_key(dedup))
            except _REDIS_OP_ERRORS as exc:
                _LOG.warning("redis get failed for ir cache read (dedup=%s): %s", dedup, exc)
                log_client(
                    interface_name="redis.get",
                    cost_ms=(time.perf_counter() - t_redis) * 1000.0,
                    ok=False,
                    return_code=1,
                    return_info=str(exc),
                    dest_ip=redis_dest,
                    add_info={"key": _data_key(dedup)},
                )
                log_alarm(
                    server_name=AlarmServerName.REDIS,
                    level=AlarmSeverity.MAJOR,
                    module="redis.get",
                    message=str(exc),
                    ip=redis_dest,
                )
                cached = None
            if cached:
                if _memory_enabled():
                    await _ir_lru.put(dedup, cached)
                log_client(
                    interface_name="redis.get",
                    cost_ms=(time.perf_counter() - t_redis) * 1000.0,
                    ok=True,
                    return_code=0,
                    return_info="hit",
                    dest_ip=redis_dest,
                    add_info={"key": _data_key(dedup)},
                )
                return _parse_root(cached)

        # 3) redis lock to avoid thundering herd
        if r is not None and _redis_enabled():
            redis_dest = ""
            raw_url = (os.environ.get("LOWCODE_DEFAULT_REDIS_URL") or "").strip()
            if raw_url:
                try:
                    host = urlparse(raw_url).hostname or ""
                    redis_dest = socket.gethostbyname(host) if host else ""
                except Exception:
                    redis_dest = ""
            redis_lock_token = f"{os.getpid()}-{int(loop.time() * 1000)}-{dedup[:12]}"
            while loop.time() < deadline:
                try:
                    t_lock = time.perf_counter()
                    redis_lock_held = bool(await r.set(_lock_key(dedup), redis_lock_token, nx=True, ex=lock_ttl))
                except _REDIS_OP_ERRORS as exc:
                    _LOG.warning(
                        "redis setnx failed for ir fetch lock (dedup=%s): %s; fallback to obs fetch",
                        dedup,
                        exc,
                    )
                    log_client(
                        interface_name="redis.setnx",
                        cost_ms=(time.perf_counter() - t_lock) * 1000.0,
                        ok=False,
                        return_code=1,
                        return_info=str(exc),
                        dest_ip=redis_dest,
                        add_info={"key": _lock_key(dedup)},
                    )
                    log_alarm(
                        server_name=AlarmServerName.REDIS,
                        level=AlarmSeverity.MAJOR,
                        module="redis.setnx",
                        message=str(exc),
                        ip=redis_dest,
                    )
                    redis_lock_held = False
                    break
                if redis_lock_held:
                    log_client(
                        interface_name="redis.setnx",
                        cost_ms=(time.perf_counter() - t_lock) * 1000.0,
                        ok=True,
                        return_code=0,
                        return_info="acquired",
                        dest_ip=redis_dest,
                        add_info={"key": _lock_key(dedup), "ttl": lock_ttl},
                    )
                    break
                await asyncio.sleep(0.05)
                try:
                    t_peer = time.perf_counter()
                    peer = await r.get(_data_key(dedup))
                except _REDIS_OP_ERRORS as exc:
                    _LOG.warning("redis get failed while waiting for peer fill (dedup=%s): %s", dedup, exc)
                    log_client(
                        interface_name="redis.get",
                        cost_ms=(time.perf_counter() - t_peer) * 1000.0,
                        ok=False,
                        return_code=1,
                        return_info=str(exc),
                        dest_ip=redis_dest,
                        add_info={"key": _data_key(dedup)},
                    )
                    log_alarm(
                        server_name=AlarmServerName.REDIS,
                        level=AlarmSeverity.MAJOR,
                        module="redis.get",
                        message=str(exc),
                        ip=redis_dest,
                    )
                    peer = None
                if peer:
                    if _memory_enabled():
                        await _ir_lru.put(dedup, peer)
                    log_client(
                        interface_name="redis.get",
                        cost_ms=(time.perf_counter() - t_peer) * 1000.0,
                        ok=True,
                        return_code=0,
                        return_info="peer_fill_hit",
                        dest_ip=redis_dest,
                        add_info={"key": _data_key(dedup)},
                    )
                    return _parse_root(peer)

        # 4) obs fetch
        try:
            body = await _read_obs_bytes(bucket, object_key)
            try:
                raw_json = body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HTTPException(status_code=502, detail="ir file is not valid utf-8") from exc
            root = _parse_root(raw_json)

            # write back
            if r is not None and _redis_enabled():
                redis_dest = ""
                raw_url = (os.environ.get("LOWCODE_DEFAULT_REDIS_URL") or "").strip()
                if raw_url:
                    try:
                        host = urlparse(raw_url).hostname or ""
                        redis_dest = socket.gethostbyname(host) if host else ""
                    except Exception:
                        redis_dest = ""
                try:
                    t_set = time.perf_counter()
                    await r.set(_data_key(dedup), raw_json, ex=ttl)
                except _REDIS_OP_ERRORS as exc:
                    _LOG.warning("redis set failed for ir cache write-back (dedup=%s): %s", dedup, exc)
                    log_client(
                        interface_name="redis.set",
                        cost_ms=(time.perf_counter() - t_set) * 1000.0,
                        ok=False,
                        return_code=1,
                        return_info=str(exc),
                        dest_ip=redis_dest,
                        add_info={"key": _data_key(dedup)},
                    )
                    log_alarm(
                        server_name=AlarmServerName.REDIS,
                        level=AlarmSeverity.MAJOR,
                        module="redis.set",
                        message=str(exc),
                        ip=redis_dest,
                    )
                else:
                    log_client(
                        interface_name="redis.set",
                        cost_ms=(time.perf_counter() - t_set) * 1000.0,
                        ok=True,
                        return_code=0,
                        return_info="written",
                        dest_ip=redis_dest,
                        add_info={"key": _data_key(dedup), "ttl": ttl},
                    )
            if _memory_enabled():
                await _ir_lru.put(dedup, raw_json)
            return root
        finally:
            if r is not None and redis_lock_held and redis_lock_token is not None:
                try:
                    raw_url = (os.environ.get("LOWCODE_DEFAULT_REDIS_URL") or "").strip()
                    redis_dest = ""
                    if raw_url:
                        try:
                            host = urlparse(raw_url).hostname or ""
                            redis_dest = socket.gethostbyname(host) if host else ""
                        except Exception:
                            redis_dest = ""
                    t_unlock = time.perf_counter()
                    await r.eval(_UNLOCK_IF_TOKEN_MATCHES_LUA, 1, _lock_key(dedup), redis_lock_token)
                except _REDIS_OP_ERRORS as exc:
                    _LOG.warning("redis unlock failed for ir fetch lock (dedup=%s): %s", dedup, exc)
                    log_client(
                        interface_name="redis.eval_unlock",
                        cost_ms=(time.perf_counter() - t_unlock) * 1000.0,
                        ok=False,
                        return_code=1,
                        return_info=str(exc),
                        dest_ip=redis_dest,
                        add_info={"key": _lock_key(dedup)},
                    )
                    log_alarm(
                        server_name=AlarmServerName.REDIS,
                        level=AlarmSeverity.MAJOR,
                        module="redis.eval_unlock",
                        message=str(exc),
                        ip=redis_dest,
                    )
                else:
                    log_client(
                        interface_name="redis.eval_unlock",
                        cost_ms=(time.perf_counter() - t_unlock) * 1000.0,
                        ok=True,
                        return_code=0,
                        return_info="released",
                        dest_ip=redis_dest,
                        add_info={"key": _lock_key(dedup)},
                    )


async def ensure_ir_root(ir_path: str) -> dict[str, Any]:
    bucket = (os.environ.get("LOWCODE_IR_OBS_BUCKET") or "").strip()
    if not bucket:
        raise HTTPException(status_code=500, detail="LOWCODE_IR_OBS_BUCKET is not configured")
    object_key = _normalize_object_key(ir_path)
    return await _load_under_locks(bucket=bucket, object_key=object_key, dedup=_dedup_token(bucket, object_key))

