# coding: utf-8
"""Session Manager Redis 键 schema（HLD §5.1 / SM 设计 §3）。

前缀 ``{session_manager}:``（sm_sysctx.key_prefix）。本模块是 SM 侧 redis 键名的
唯一出口：所有键名构造集中在此，Lua 脚本里只拿 ``prefix`` 拼 key。

前缀整体包在花括号里 = Redis Cluster **hash tag**：SM 全部键（跨 scope/跨 Pod/
全局 ZSET）落同一 slot，多键 Lua 的原子语义在 cluster 分片下保持成立。
单实例/哨兵/fakeredis 下 ``{}`` 无语义，键名照常工作——同一套键名兼容两种部署。
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from ..util import s, to_int
from . import lua_scripts as lua

# hash tag 语义见模块 docstring；scope_id 等外部标识符禁止含 {/}（否则
# 破坏同槽性，入口校验见 orchestrator/config_store）
KEY_PREFIX = "{session_manager}"

logger = logging.getLogger("agent_runtime.session_manager")

# 单次 Lua eval 超过该时长告警（即 Redis 延迟探针；正常 <1ms）
_SLOW_EVAL_MS = 200.0


def _script_tag(script: str) -> str:
    """Lua 源 → 常量名（诊断用；未知源返回 8 字符指纹）。"""
    for name, value in vars(lua).items():
        if name.startswith("LUA_") and value == script:
            return name
    return hashlib.md5(script.encode()).hexdigest()[:8]


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

    def routing_snapshot(self) -> str:
        """STRING: 路由快照（scopes+templates 的 JSON；config_sync 原子 SET 覆盖）。"""
        return f"{self.prefix}:routing:snapshot"

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

    # 键前缀（含尾冒号）：eval 的 KEYS[1]/ARGV[1]，Redis Cluster 的 EVAL 路由锚
    @property
    def prefix(self) -> str:
        return self.k.prefix + ":"

    async def eval(self, script: str, *args: Any) -> list[str]:
        """统一 EVAL 出口：自动带键前缀，返回值逐元素转 str。

        异常策略（防刷屏）：SM 各 Lua 正常必返回非空表，None/False 属真异常
        → WARNING（route_place 的 scope_full 兜底会掩盖它，这里单独留痕）；
        单次超 _SLOW_EVAL_MS → WARNING（Redis 延迟探针）；常规 eval 仅 DEBUG。
        """
        t0 = time.monotonic()
        # KEYS[1] 声明 prefix：cluster 客户端据此把 EVAL 路由到 hash tag 的
        # 归属节点（numkeys=0 会被随机路由，脚本摸到非本节点键即报
        # "non local key"，真环境 20/30 复现）；脚本体内仍取 ARGV[1]。
        # 单实例/fakeredis 下多声明一个不访问的 KEYS 无任何影响。
        result = await self.redis.eval(script, 1, self.prefix, self.prefix, *args)
        duration_ms = (time.monotonic() - t0) * 1000
        if duration_ms > _SLOW_EVAL_MS:
            logger.warning(
                "lua eval slow: script=%s duration_ms=%.1f arg0=%s",
                _script_tag(script), duration_ms, args[0] if args else "-",
            )
        if result is None or result is False:
            logger.warning(
                "lua returned empty (anomaly): script=%s arg0=%s",
                _script_tag(script), args[0] if args else "-",
            )
            return []
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("lua eval: script=%s duration_ms=%.1f argc=%d",
                         _script_tag(script), duration_ms, len(args))
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
        """返回 (action, pod_id)。action ∈ refresh/placed/scope_full/need_acquire。

        空/异常返回兜底 "scope_full"（fail-closed，eval 层 WARNING 留痕）——
        场景 F 快失败后该兜底对外表现为立即 503 SCOPE_FULL。
        """
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
        if ret[0] == "rubble":
            # 残骸已自清（缺 scope/pod 的半成品哈希）——留痕供排障定位外部直改源
            logger.warning(
                "evict rubble session (hash missing scope/pod): session=%s",
                session_id,
            )
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

    async def refresh_lock(self, key: str, ttl: int, token: str) -> bool:
        """看门狗续期（仅持有者，Lua 防误续他人锁）；返回是否仍持有。

        不引入新键（对既有锁键 EXPIRE），Redis Cluster 同槽纪律不受影响。
        """
        ok = await self.redis.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('EXPIRE', KEYS[1], ARGV[2]) else return 0 end",
            1, key, token, ttl,
        )
        return bool(ok)

    # -------------------------------------------------------------- 诊断只读（/visualization/*）

    async def session_hash(self, session_id: str) -> dict[str, str]:
        """单会话 HASH（scope_id/pod_id/expiry/session_ttl）。"""
        raw = await self.redis.hgetall(self.k.session(session_id))
        return {s(k): s(v) for k, v in raw.items()}

    async def session_expiry_score(self, session_id: str) -> float | None:
        """全局到期 ZSET 中该会话的分值（到期时间戳；不在则 None）。"""
        score = await self.redis.zscore(self.k.session_expiry(), session_id)
        if score is None:
            return None
        try:
            return float(score)
        except (TypeError, ValueError):
            return None

    async def scope_session_count(self, scope_id: str) -> int:
        return to_int(await self.redis.scard(self.k.scope_sessions(scope_id)))

    # -------------------------------------------------------------- 路由快照

    async def routing_snapshot_raw(self) -> str:
        """读快照 JSON 原文（空串 = 无快照，调用方走 DB 重建）。"""
        raw = await self.redis.get(self.k.routing_snapshot())
        return s(raw)

    async def write_routing_snapshot(self, text: str) -> None:
        """原子 SET 覆盖快照（config_sync / 重建路径唯一写点）。"""
        await self.redis.set(self.k.routing_snapshot(), text)

