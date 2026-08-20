# coding: utf-8
"""Session Manager Redis 键 schema（HLD §5.1 / SM 设计 §3）。

前缀 ``session_manager:``（sm_sysctx.key_prefix）。本模块是 SM 侧 redis 键名的
唯一出口：所有键名构造集中在此，Lua 脚本里只拿 ``prefix`` 拼 key。
"""

from __future__ import annotations

from typing import Any

from ..util import s, to_int
from . import lua_scripts as lua

KEY_PREFIX = "session_manager"


class SMKeys:
    """SM Redis 键构造器（全部返回含前缀的完整键名）。"""

    def __init__(self, prefix: str = KEY_PREFIX) -> None:
        self.prefix = prefix

    # ---- 会话四处（不变量 1：一个活跃会话同时存在于此四处）
    def session_expiry(self) -> str:
        """ZSET: session_id → 到期时间戳（全局，sweeper 扫它）。"""
        return f"{self.prefix}:session_expiry"

    def session(self, session_id: str) -> str:
        """HASH: scope_id / pod_id / expiry / session_ttl（单会话亲和绑定）。"""
        return f"{self.prefix}:session:{session_id}"

    def scope_sessions(self, scope_id: str) -> str:
        """SET: 该 scope 活跃 session_id。SCARD = scope_concurrency 闸门。"""
        return f"{self.prefix}:scope:{scope_id}:sessions"

    def pod_sessions(self, scope_id: str, pod_id: str) -> str:
        """SET: 该 (scope, Pod) 上的 session_id。SCARD < pod_concurrency = 容量闸门。"""
        return f"{self.prefix}:pod:{scope_id}:{pod_id}:sessions"

    # ---- scope 级
    def scope_pods(self, scope_id: str) -> str:
        """ZSET: pod_id → 接入序（first-fit 按序遍历；sweeper ZREM 使 Pod 退出候选）。"""
        return f"{self.prefix}:scope:{scope_id}:pods"

    def scope_pod_seq(self, scope_id: str) -> str:
        """STRING: 单调递增计数，scope:pods 的 score 来源。"""
        return f"{self.prefix}:scope:{scope_id}:pod_seq"

    def scope_config(self, scope_id: str) -> str:
        """HASH: resolve 缓存（config_sync 主动 DEL 失效）。"""
        return f"{self.prefix}:scope:{scope_id}:config"

    def scope_waiters(self, scope_id: str) -> str:
        """SET: 等待中的 request_id。SCARD < max_waiters = 等待队列上限（场景 F）。"""
        return f"{self.prefix}:scope:{scope_id}:waiters"

    def scope_free_channel(self, scope_id: str) -> str:
        """PubSub 通道：额度释放信号（EVICT 发布 / 阻塞 route 订阅）。"""
        return f"{self.prefix}:scope:{scope_id}:free"

    # ---- Pod 注册三处（不变量 5：scope:pods ⊆ pods:registered）
    def pod_info(self, scope_id: str, pod_id: str) -> str:
        """HASH: sse_url / deploy_ver。"""
        return f"{self.prefix}:pod:{scope_id}:{pod_id}:info"

    def pod_idle_notified(self, scope_id: str, pod_id: str) -> str:
        """STRING(NX EX 60)：空 Pod 通知去重标记。"""
        return f"{self.prefix}:pod:{scope_id}:{pod_id}:idle_notified"

    def pods_registered(self) -> str:
        """SET: 全部 "{scope_id}:{pod_id}"（sweeper 空 Pod pass 枚举）。"""
        return f"{self.prefix}:pods:registered"

    def pod_scopes(self, pod_id: str) -> str:
        """SET: 该 Pod 被哪些 scope 引用（notify_pod_dead 反查）。"""
        return f"{self.prefix}:pods:{pod_id}:scopes"

    # ---- 锁
    def lock_sweep(self) -> str:
        """sweeper tick 级选主锁（SET NX EX 2）。"""
        return f"{self.prefix}:lock:sweep"

    def lock_config_sync(self) -> str:
        """config_sync 串行化分布式锁（SET NX EX，场景 M）。"""
        return f"{self.prefix}:lock:config_sync"


class SessionState:
    """SM 运行态读写门面：持 redis client + Lua 脚本，SM 各组件经它访问 Redis。

    业务组件（orchestrator / sweeper / facade / config_store）不直接拼键名、
    不直接 eval Lua——统一走本类方法，键 schema 与脚本调用集中一处。
    """

    def __init__(self, redis: Any, keys: SMKeys | None = None) -> None:
        self.redis = redis
        self.k = keys or SMKeys()

    # 保留给需要 raw client 的场景（pubsub 订阅）
    @property
    def prefix(self) -> str:
        return self.k.prefix + ":"

    async def eval(self, script: str, *args: Any) -> list[str]:
        """统一 EVAL 出口：自动带键前缀，返回值逐元素转 str。"""
        result = await self.redis.eval(script, 0, self.prefix, *args)
        if result is None or result is False:
            return []
        if isinstance(result, (list, tuple)):
            return [s(item) for item in result]
        return [s(result)]

    # -------------------------------------------------------------- route 核心

    async def route_place(
        self,
        session_id: str,
        scope_id: str,
        expiry_ts: int,
        session_ttl: int,
        scope_concurrency: int,
        pod_concurrency: int,
        max_pods: int,
        now: int,
    ) -> tuple[str, str]:
        """返回 (action, pod_id)。action ∈ refresh/placed/scope_full/need_acquire。"""
        ret = await self.eval(
            lua.LUA_ROUTE_PLACE,
            session_id, scope_id, expiry_ts, session_ttl,
            scope_concurrency, pod_concurrency, max_pods, now,
        )
        return (ret[0] if ret else "scope_full", ret[1] if len(ret) > 1 else "")

    async def evict(self, session_id: str) -> dict[str, str] | None:
        """移除会话（幂等）。返回 {'scope_id','pod_id','remaining'} 或 None（本就不存在）。"""
        ret = await self.eval(lua.LUA_EVICT, session_id)
        if not ret or ret[0] == "noop":
            return None
        return {"scope_id": ret[1], "pod_id": ret[2], "remaining": ret[3]}

    async def touch(self, session_id: str, now: int, default_ttl: int = 60) -> tuple[bool, str]:
        """保活。返回 (touched, pod_id)。"""
        ret = await self.eval(lua.LUA_TOUCH, session_id, now, default_ttl)
        return (bool(ret and ret[0] == "true"), ret[1] if len(ret) > 1 else "")

    # -------------------------------------------------------------- Pod 注册

    async def register_pod(self, scope_id: str, pod_id: str, sse_url: str, deploy_ver: str) -> None:
        """acquire 成功后登记新 Pod 到本 scope 候选集（三处注册 + 接入序）。"""
        await self.eval(lua.LUA_REGISTER_POD, scope_id, pod_id, sse_url, deploy_ver)

    async def cleanup_pod(self, scope_id: str, pod_id: str) -> None:
        """notify_pod_dead 时清该 (scope, pod) 的全部 SM 注册（幂等）。"""
        await self.eval(lua.LUA_CLEANUP_POD, scope_id, pod_id)

    async def pod_sse_url(self, scope_id: str, pod_id: str) -> str:
        """读 Pod 的 SSE 地址（route 返回给 gateway 直连用）。"""
        url = await self.redis.hget(self.k.pod_info(scope_id, pod_id), "sse_url")
        return s(url)

    async def pod_deploy_ver(self, scope_id: str, pod_id: str) -> str:
        ver = await self.redis.hget(self.k.pod_info(scope_id, pod_id), "deploy_ver")
        return s(ver)

    async def scope_pod_ids(self, scope_id: str) -> list[str]:
        """scope 候选集成员（接入序）。"""
        members = await self.redis.zrange(self.k.scope_pods(scope_id), 0, -1)
        return [s(m) for m in members]

    async def registered_pods(self) -> list[str]:
        """全部 "{scope_id}:{pod_id}"（sweeper 空 Pod pass 枚举）。"""
        members = await self.redis.smembers(self.k.pods_registered())
        return sorted(s(m) for m in members)

    async def pod_scopes(self, pod_id: str) -> list[str]:
        """反查 Pod 被哪些 scope 引用（notify_pod_dead 用）。"""
        members = await self.redis.smembers(self.k.pod_scopes(pod_id))
        return sorted(s(m) for m in members)

    async def pod_session_ids(self, scope_id: str, pod_id: str) -> list[str]:
        members = await self.redis.smembers(self.k.pod_sessions(scope_id, pod_id))
        return sorted(s(m) for m in members)

    # -------------------------------------------------------------- sweeper

    async def sweep_idle_notify(self, scope_id: str, pod_id: str) -> bool:
        """空 Pod pass 原子判定：notified=True 时调用方才 fire idle_consider。"""
        ret = await self.eval(lua.LUA_SWEEP_IDLE_NOTIFY, scope_id, pod_id)
        return bool(ret and ret[0] == "true")

    async def due_session_ids(self, now: int, limit: int = 1000) -> list[str]:
        """到期 pass：全局到期集合中已过期的 session（最多 limit 个）。"""
        members = await self.redis.zrangebyscore(self.k.session_expiry(), "-inf", now)
        return [s(m) for m in members[:limit]]

    # -------------------------------------------------------------- 等待队列（场景 F）

    async def waiter_count(self, scope_id: str) -> int:
        return to_int(await self.redis.scard(self.k.scope_waiters(scope_id)))

    async def try_add_waiter(self, scope_id: str, request_id: str,
                             max_waiters: int) -> bool:
        """原子入队（LUA_WAITER_GATE）：SADD 先行 + SCARD 超限自退。

        「先查后加」在并发同时到达时都会读到旧计数而全部入队（M6 验收发现的
        竞态）；SADD/SCARD/SREM 必须同一脚本内原子完成。
        """
        ret = await self.eval(lua.LUA_WAITER_GATE, scope_id, request_id, max_waiters)
        return bool(ret and ret[0] == "true")

    async def add_waiter(self, scope_id: str, request_id: str) -> None:
        await self.redis.sadd(self.k.scope_waiters(scope_id), request_id)

    async def remove_waiter(self, scope_id: str, request_id: str) -> None:
        await self.redis.srem(self.k.scope_waiters(scope_id), request_id)

    async def try_lock(self, key: str, ttl: int, token: str) -> bool:
        """SET NX EX 抢锁（tick 级选主 / config_sync 串行化）。"""
        ok = await self.redis.set(key, token, nx=True, ex=ttl)
        return bool(ok)

    async def unlock(self, key: str, token: str) -> None:
        """释放锁（仅持有者，Lua 防误删他人锁）。"""
        await self.redis.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]) else return 0 end",
            1, key, token,
        )

