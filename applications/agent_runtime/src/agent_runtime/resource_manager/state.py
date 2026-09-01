# coding: utf-8
"""Resource Manager Redis 键 schema + 状态门面（HLD §5.2 / RM 设计 §3）。

前缀 ``{resource_manager}:``，业务键再带 ``resource:`` 段。所有计数派生自
SET/ZSET（SCARD/ZCARD），无独立计数器。

前缀整体包在花括号里 = Redis Cluster **hash tag**：RM 全部键落同一 slot，
多键 Lua 的原子语义在 cluster 分片下保持成立；单实例/哨兵/fakeredis 下
``{}`` 无语义，同一套键名兼容两种部署（对齐 SM 侧 session_manager.state）。
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from ..util import now_ts, s, to_int
from . import lua_scripts as lua

# hash tag 语义见模块 docstring
KEY_PREFIX = "{resource_manager}"

logger = logging.getLogger("agent_runtime.resource_manager")

# deploying 占位 deadline（对齐 orchestrator 的 DEPLOY_LOCK_TTL=360：
# 盖住 ready_timeout 300s + 余量；崩溃遗留经此窗口自愈）
DEPLOY_TOKEN_TTL = 360

# 单次 Lua eval 超过该时长告警（即 Redis 延迟探针；正常 <1ms）
_SLOW_EVAL_MS = 200.0


def _script_tag(script: str) -> str:
    """Lua 源 → 常量名（诊断用；未知源返回 8 字符指纹）。"""
    for name, value in vars(lua).items():
        if name.startswith("LUA_") and value == script:
            return name
    return hashlib.md5(script.encode()).hexdigest()[:8]


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
        """HASH: min_idle_pods / max_pods / pod_ttl / pod_spec(json) / deploy_ver /
        generation（config_refresh 的代次日落标记，唯一写点 = bump_generation）。"""
        return f"{self.prefix}:resource:scope:{scope_id}:config"

    def scope_deploying(self, scope_id: str) -> str:
        """ZSET: deploy 占位 token → deadline（秒级时间戳）。

        计入 max_pods（防并发超配）；score=deadline 供闸门原子清理崩溃遗留
        （进程硬崩后进程内清理不存在，占位不得永久虚占 max_pods）。
        """
        return f"{self.prefix}:resource:scope:{scope_id}:deploying"

    def scope_deploy_followers(self, scope_id: str) -> str:
        """ZSET: deploy follower（request_id → deadline 秒级时间戳）。

        deploy 锁输家的等待室：ZCARD ≤ pod_concurrency-1（leader 会话之外
        新 Pod 恰剩这些槽）；score=deadline 供闸门原子清理崩溃遗留。
        """
        return f"{self.prefix}:resource:scope:{scope_id}:deploy_followers"

    # ---- Pod 级
    def pod_info(self, pod_id: str) -> str:
        """HASH: scope_id / pod_sse_url / pod_ip / namespace / phase / created_ts /
        deploy_ver / sse_port / health_path / generation（注册时刻代次烙印）。"""
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
        """统一 EVAL 出口（含异常留痕，见 session_manager.state.eval 同款策略）。

        RM 各 Lua 正常必返回非空表，None/False 属真异常 → WARNING（acquire 的
        no_config 兜底会掩盖它）；单次超 _SLOW_EVAL_MS → WARNING；常规仅 DEBUG。
        """
        t0 = time.monotonic()
        # KEYS[1] 声明 prefix 作路由锚（cluster 路由到 tag 归属节点），脚本
        # 体内仍取 ARGV[1]——同 session_manager.state.eval 的实测结论
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

    # -------------------------------------------------------------- acquire 链路

    async def acquire(
        self, scope_id: str, deploy_ver: str, deploy_token: str
    ) -> tuple[str, str, str]:
        """LUA_ACQUIRE：返回 (action, pod_id, pod_sse_url)。

        action ∈ reuse（取暖 Pod）/ need_deploy（已占位）/ max_reached / no_config。
        占位 deadline = now + DEPLOY_TOKEN_TTL（对齐 deploy 锁窗口，崩溃遗留
        由下一次闸门 ZREMRANGEBYSCORE 自清）。
        """
        now = now_ts()
        ret = await self.eval(
            lua.LUA_ACQUIRE, scope_id, deploy_ver, deploy_token,
            now + DEPLOY_TOKEN_TTL, now,
        )
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
        *,
        sse_port: int | None = None,
        health_path: str = "",
    ) -> None:
        """LUA_REGISTER：deploy 成功后登记（清 deploying 占位；idle_flag=热备入 idle 池）。

        sse_port/health_path 随 Pod 烘焙进 info——健康探测按 Pod 自己的契约
        参数进行（A 类变更后 scope 当前配置已换代，不能拿新参数探老 Pod）。
        """
        await self.eval(
            lua.LUA_REGISTER, pod_id, scope_id, pod_sse_url, pod_ip,
            namespace, deploy_ver, deploy_token, "1" if idle_flag else "0", now,
            int(sse_port) if sse_port else "", health_path,
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
        """LUA_PLACEHOLDER：autoscale 专用占位（不碰 idle 池；deadline 同 acquire）。"""
        now = now_ts()
        ret = await self.eval(
            lua.LUA_PLACEHOLDER, scope_id, deploy_token,
            now + DEPLOY_TOKEN_TTL, now,
        )
        return ret[0] if ret else "no_config"

    async def clear_deploy_token(self, scope_id: str, token: str) -> None:
        """deploy 失败/放弃时清占位（错误路径必须清，防 max_pods 永久虚高）。"""
        await self.redis.zrem(self.k.scope_deploying(scope_id), token)

    async def reap_expired_deploying(self, scope_id: str) -> int:
        """清过期的 deploying 占位（score ≤ now）：崩溃遗留自愈，返回清除数。"""
        return to_int(await self.redis.zremrangebyscore(
            self.k.scope_deploying(scope_id), "-inf", now_ts()
        ))

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

    async def bump_generation(self, scope_id: str) -> int:
        """scope 代次 +1（HINCRBY 原子自增），返回新代次。

        config_refresh 的日落标记唯一写点：save_scope_config 的 mapping 永不含
        generation（config_sync 推送不重置代次，只单调递增）。缺省键从 1 起。
        """
        return to_int(await self.redis.hincrby(
            self.k.scope_config(scope_id), "generation", 1
        ))

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
        return to_int(await self.redis.zcard(self.k.scope_deploying(scope_id)))

    async def known_scope_ids(self) -> list[str]:
        """扫全部 scope:config 键（autoscale / reclaim 的 per-scope 遍历源）。

        cluster 客户端的 SCAN 默认扫全部主节点，游标返回 {节点: 游标} dict
        （单实例/fakeredis 返回 int）——两者都要全部归零才算扫尽。
        """
        pattern = f"{self.prefix}resource:scope:*:config"
        scope_ids: list[str] = []
        cursor: Any = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=200)
            for key in keys:
                # {resource_manager}:resource:scope:{scope_id}:config → scope_id
                parts = s(key).split(":")
                scope_ids.append(parts[-2])
            if isinstance(cursor, dict):
                if not any(to_int(c) for c in cursor.values()):
                    break
            elif to_int(cursor) == 0:
                break
        return sorted(set(scope_ids))

    # -------------------------------------------------------------- 健康探测（场景 N）

    async def bump_health_fail(self, pod_id: str) -> int:
        """连续失败 +1，返回当前次数。"""
        return to_int(await self.redis.incr(self.k.pod_health_fails(pod_id)))

    async def reset_health_fail(self, pod_id: str) -> None:
        await self.redis.delete(self.k.pod_health_fails(pod_id))

    async def health_fails(self, pod_id: str) -> int:
        """诊断只读：当前连续失败次数（/visualization/scope 用）。"""
        return to_int(await self.redis.get(self.k.pod_health_fails(pod_id)))

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
