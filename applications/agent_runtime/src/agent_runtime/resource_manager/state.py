# coding: utf-8
"""Resource Manager Redis 键 schema + 状态门面（HLD §5.2 / RM 设计 §3）。

前缀 ``resource_manager:``，业务键再带 ``resource:`` 段。所有计数派生自
SET/ZSET（SCARD/ZCARD），无独立计数器。
"""

from __future__ import annotations

from typing import Any

from ..util import s, to_int
from . import lua_scripts as lua

KEY_PREFIX = "resource_manager"


class RMKeys:
    """RM Redis 键构造器（全部返回含前缀的完整键名）。"""

    def __init__(self, prefix: str = KEY_PREFIX) -> None:
        self.prefix = prefix

    # ---- per-scope Pod 池
    def scope_pods(self, scope_id: str) -> str:
        """ZSET: pod_id → 创建序（该 scope 全部 Pod = in_use ∪ idle）。ZCARD 参与 max_pods 判定。"""
        return f"{self.prefix}:resource:scope:{scope_id}:pods"

    def scope_idle(self, scope_id: str) -> str:
        """SET: idle pod_id。SCARD = idle Pod 数；acquire 从此取暖 Pod。"""
        return f"{self.prefix}:resource:scope:{scope_id}:idle"

    def scope_config(self, scope_id: str) -> str:
        """HASH: min_idle_pods / max_pods / pod_ttl / pod_spec(json) / deploy_ver。"""
        return f"{self.prefix}:resource:scope:{scope_id}:config"

    def scope_deploying(self, scope_id: str) -> str:
        """SET: deploy 占位 token（计入 max_pods，防并发超配）。"""
        return f"{self.prefix}:resource:scope:{scope_id}:deploying"

    def scope_deploy_followers(self, scope_id: str) -> str:
        """ZSET: deploy follower（request_id → deadline 秒级时间戳）。

        deploy 锁输家的等待室：ZCARD ≤ pod_concurrency-1（leader 会话之外
        新 Pod 恰剩这些槽）；score=deadline 供闸门原子清理崩溃遗留。
        """
        return f"{self.prefix}:resource:scope:{scope_id}:deploy_followers"

    # ---- Pod 级
    def pod_info(self, pod_id: str) -> str:
        """HASH: scope_id / pod_sse_url / pod_ip / namespace / phase / created_ts / deploy_ver。"""
        return f"{self.prefix}:resource:pod:{pod_id}:info"

    def pod_idle_since(self, pod_id: str) -> str:
        """STRING: idle 起始时间戳（reclaim 计时）。存在 ⟺ 在 scope:idle。"""
        return f"{self.prefix}:resource:pod:{pod_id}:idle_since"

    def pod_health_fails(self, pod_id: str) -> str:
        """STRING: 健康探测连续失败次数（场景 N；成功清零，purge 时清）。"""
        return f"{self.prefix}:resource:pod:{pod_id}:health_fails"

    def pods_all(self) -> str:
        """SET: 全部 pod_id（孤儿对账 / 枚举）。"""
        return f"{self.prefix}:resource:pods:all"

    # ---- 选主锁（deploy per-scope；autoscale / reclaim / watch / reconcile tick 级）
    def lock_deploy(self, scope_id: str) -> str:
        return f"{self.prefix}:lock:rm:deploy:{scope_id}"

    def lock_autoscale(self) -> str:
        return f"{self.prefix}:lock:rm:autoscale"

    def lock_reclaim(self) -> str:
        return f"{self.prefix}:lock:rm:reclaim"

    def lock_watch(self) -> str:
        return f"{self.prefix}:lock:rm:watch"

    def lock_reconcile(self) -> str:
        return f"{self.prefix}:lock:rm:reconcile"


class ResourceState:
    """RM 运行态读写门面：键 schema + Lua 调用唯一出口。"""

    def __init__(self, redis: Any, keys: RMKeys | None = None) -> None:
        self.redis = redis
        self.k = keys or RMKeys()

    @property
    def prefix(self) -> str:
        return self.k.prefix + ":"

    async def eval(self, script: str, *args: Any) -> list[str]:
        result = await self.redis.eval(script, 0, self.prefix, *args)
        if result is None or result is False:
            return []
        if isinstance(result, (list, tuple)):
            return [s(item) for item in result]
        return [s(result)]

    # -------------------------------------------------------------- acquire 链路

    async def acquire(
        self, scope_id: str, deploy_ver: str, deploy_token: str
    ) -> tuple[str, str, str]:
        """LUA_ACQUIRE：返回 (action, pod_id, pod_sse_url)。

        action ∈ reuse（取暖 Pod）/ need_deploy（已占位）/ max_reached / no_config。
        """
        ret = await self.eval(lua.LUA_ACQUIRE, scope_id, deploy_ver, deploy_token)
        return (
            ret[0] if ret else "no_config",
            ret[1] if len(ret) > 1 else "",
            ret[2] if len(ret) > 2 else "",
        )

    async def register_pod(
        self,
        pod_id: str,
        scope_id: str,
        pod_sse_url: str,
        pod_ip: str,
        namespace: str,
        deploy_ver: str,
        deploy_token: str,
        idle_flag: bool,
        now: int,
    ) -> None:
        """LUA_REGISTER：deploy 成功后登记（清 deploying 占位；idle_flag=热备入 idle 池）。"""
        await self.eval(
            lua.LUA_REGISTER, pod_id, scope_id, pod_sse_url, pod_ip,
            namespace, deploy_ver, deploy_token, "1" if idle_flag else "0", now,
        )

    async def release(self, pod_id: str, scope_id: str, now: int) -> bool:
        """LUA_RELEASE（idle_consider）：转 idle 暖池，起 pod_ttl 计时。幂等。"""
        ret = await self.eval(lua.LUA_RELEASE, pod_id, scope_id, now)
        return bool(ret and ret[0] == "true")

    async def purge(self, pod_id: str) -> str:
        """LUA_PURGE：清该 Pod 全部 RM key。返回其 scope_id（空串=本就不存在）。"""
        ret = await self.eval(lua.LUA_PURGE, pod_id)
        return ret[1] if len(ret) > 1 else ""

    async def deploy_placeholder(self, scope_id: str, deploy_token: str) -> str:
        """LUA_PLACEHOLDER：autoscale 专用占位（不碰 idle 池）。

        返回 need_deploy / max_reached / no_config。
        """
        ret = await self.eval(lua.LUA_PLACEHOLDER, scope_id, deploy_token)
        return ret[0] if ret else "no_config"

    async def clear_deploy_token(self, scope_id: str, token: str) -> None:
        """deploy 失败/放弃时清占位（错误路径必须清，防 max_pods 永久虚高）。"""
        await self.redis.srem(self.k.scope_deploying(scope_id), token)

    # -------------------------------------------------------------- follower 等待室

    async def try_add_deploy_follower(
        self, scope_id: str, follower_id: str,
        max_followers: int, deadline: int, now: int,
    ) -> bool:
        """LUA_DEPLOY_FOLLOWER_GATE：原子准入（先清过期 → ZADD → 超限自退）。"""
        ret = await self.eval(
            lua.LUA_DEPLOY_FOLLOWER_GATE,
            scope_id, follower_id, max_followers, deadline, now,
        )
        return bool(ret and ret[0] == "true")

    async def remove_deploy_follower(self, scope_id: str, follower_id: str) -> None:
        """follower 退出等待室（错误路径必须清，防虚占 pc-1 名额）。"""
        await self.redis.zrem(self.k.scope_deploy_followers(scope_id), follower_id)

    async def deploy_follower_count(self, scope_id: str) -> int:
        return to_int(await self.redis.zcard(self.k.scope_deploy_followers(scope_id)))

    # -------------------------------------------------------------- 池参数缓存

    async def load_scope_config(self, scope_id: str) -> dict[str, str]:
        raw = await self.redis.hgetall(self.k.scope_config(scope_id))
        return {s(k_): s(v) for k_, v in raw.items()}

    async def save_scope_config(self, scope_id: str, mapping: dict[str, Any]) -> None:
        await self.redis.hset(
            self.k.scope_config(scope_id), mapping={k_: str(v) for k_, v in mapping.items()}
        )

    async def has_scope_config(self, scope_id: str) -> bool:
        return to_int(
            await self.redis.hlen(self.k.scope_config(scope_id))
        ) > 0

    # -------------------------------------------------------------- 枚举 / 对账

    async def all_pod_ids(self) -> list[str]:
        members = await self.redis.smembers(self.k.pods_all())
        return sorted(s(m) for m in members)

    async def pod_scope(self, pod_id: str) -> str:
        return s(await self.redis.hget(self.k.pod_info(pod_id), "scope_id"))

    async def pod_info(self, pod_id: str) -> dict[str, str]:
        raw = await self.redis.hgetall(self.k.pod_info(pod_id))
        return {s(k_): s(v) for k_, v in raw.items()}

    async def idle_pods(self, scope_id: str) -> list[str]:
        members = await self.redis.smembers(self.k.scope_idle(scope_id))
        return sorted(s(m) for m in members)

    async def idle_since(self, pod_id: str) -> int:
        return to_int(await self.redis.get(self.k.pod_idle_since(pod_id)))

    async def pod_count(self, scope_id: str) -> int:
        return to_int(await self.redis.zcard(self.k.scope_pods(scope_id)))

    async def pod_ids(self, scope_id: str) -> list[str]:
        """该 scope 全部 pod_id（follower 检测 leader 注册进展用）。"""
        members = await self.redis.zrange(self.k.scope_pods(scope_id), 0, -1)
        return [s(m) for m in members]

    async def deploying_count(self, scope_id: str) -> int:
        return to_int(await self.redis.scard(self.k.scope_deploying(scope_id)))

    async def known_scope_ids(self) -> list[str]:
        """扫全部 scope:config 键（autoscale / reclaim 的 per-scope 遍历源）。"""
        pattern = f"{self.prefix}resource:scope:*:config"
        scope_ids: list[str] = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=200)
            for key in keys:
                # resource_manager:resource:scope:{scope_id}:config → scope_id
                parts = s(key).split(":")
                scope_ids.append(parts[-2])
            if to_int(cursor) == 0:
                break
        return sorted(set(scope_ids))

    # -------------------------------------------------------------- 健康探测（场景 N）

    async def bump_health_fail(self, pod_id: str) -> int:
        """连续失败 +1，返回当前次数。"""
        return to_int(await self.redis.incr(self.k.pod_health_fails(pod_id)))

    async def reset_health_fail(self, pod_id: str) -> None:
        await self.redis.delete(self.k.pod_health_fails(pod_id))

    # -------------------------------------------------------------- 选主锁

    async def try_lock(self, key: str, ttl: int, token: str) -> bool:
        ok = await self.redis.set(key, token, nx=True, ex=ttl)
        return bool(ok)

    async def lock_held(self, key: str) -> bool:
        """锁是否被持有（follower 判 leader 已放弃/失败：锁空闲且无进展）。"""
        return bool(await self.redis.get(key))

    async def unlock(self, key: str, token: str) -> None:
        await self.redis.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]) else return 0 end",
            1, key, token,
        )
