# coding: utf-8
"""RM Lua 脚本的无 Lua 实现（WATCH/MULTI/EXEC，EVAL 禁用环境）。

设计与等价性约束同 ``session_manager/no_lua.py``（同参数同返回 / 强一致
范式 / 键布局不变，RM 键域整体在 ``{resource_manager}`` hash tag 内同槽）。
RM 特有的崩溃窗口与兜底见各 handler 注释及 design 文档语义映射表。
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from redis.exceptions import WatchError

from openjiuwen_runtime.service.context.redis_compat import backoff_delay

from ..util import s, to_int
from . import lua_scripts as lua

if TYPE_CHECKING:  # 避免与 state.py 循环导入（state 构造 ResourceNoLua）
    from .state import RMKeys

_WARM_ATTEMPTS = 5
_COLD_ATTEMPTS = 5


class ResourceNoLua:
    """RM 7 个 Lua 的无 Lua 等价实现（经 ``ResourceState.eval`` 分派调用）。"""

    def __init__(self, redis: Any, keys: "RMKeys") -> None:
        self.redis = redis
        self.k = keys

    async def dispatch(self, script: str, args: tuple) -> list[str]:
        handler = _HANDLERS.get(script)
        if handler is None:
            # 编程错误防护：新增 Lua 脚本未同步 no-Lua 实现时显式炸
            from .state import _script_tag  # 惰性导入防循环

            raise RuntimeError(
                f"no-Lua 实现缺失(新脚本未适配): script={_script_tag(script)}"
            )
        ret = await handler(self, *args)
        return list(ret)

    async def _retry(self, fn, attempts: int, base_ms: float = 2.0,
                     cap_ms: float = 50.0):
        """WatchError 乐观重试（全抖动退避）；耗尽上抛（acquire 链走既有错误路径）。"""
        for attempt in range(attempts):
            try:
                return await fn()
            except WatchError:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(
                    backoff_delay(attempt, base_ms=base_ms, cap_ms=cap_ms)
                )

    # -------------------------------------------------------------- 步5：温路径

    async def acquire(self, scope_id: str, deploy_ver: str, deploy_token: str,
                      deadline: int, now: int) -> list[str]:
        """LUA_ACQUIRE 等价：暖 Pod 复用 / 容量闸门（含 deploying 占位）/ 占位。

        两段 WATCH：先 WATCH 决策静态键（config/idle/pods/deploying/drain），
        复读 idle 现成员后补 WATCH 各自 info 键（WATCH 在 MULTI 前可多次
        下发）——此后全部复读均在 WATCH 保护下，任何并发改写（注册烙印/
        PURGE/并发 acquire 弹出/占位推进）都在 EXEC 处触发 WatchError 重试，
        与 Lua 单线程串行化等价。

        过期占位清理（ZREMRANGEBYSCORE）为 WATCH 前独立命令（幂等）：
        清理与复读之间新过期的占位使 total 偏保守（可能 max_reached），
        下一拍自愈——安全向偏差，不会超配。
        """
        cfg_key = self.k.scope_config(scope_id)
        idle_key = self.k.scope_idle(scope_id)
        pods_key = self.k.scope_pods(scope_id)
        dep_key = self.k.scope_deploying(scope_id)
        drain_key = self.k.scope_drain_until(scope_id)

        await self.redis.zremrangebyscore(dep_key, "-inf", int(now))

        async def _txn() -> list[str]:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(cfg_key, idle_key, pods_key, dep_key, drain_key)
                max_pods_raw = await pipe.hget(cfg_key, "max_pods")
                if max_pods_raw is None:
                    await pipe.unwatch()
                    return ["no_config", "", ""]
                cfg_gen = s((await pipe.hget(cfg_key, "generation")) or "")
                idle_now = sorted(
                    s(m) for m in await pipe.smembers(idle_key)
                )
                if idle_now:  # 动态键集补 WATCH（复读到的 info 由此保护）
                    await pipe.watch(
                        *(self.k.pod_info(p) for p in idle_now)
                    )
                for pod in idle_now:
                    info_key = self.k.pod_info(pod)
                    ver = s((await pipe.hget(info_key, "deploy_ver")) or "")
                    gen = s((await pipe.hget(info_key, "generation")) or "")
                    if ver == deploy_ver and gen == cfg_gen:
                        url = s((await pipe.hget(info_key, "pod_sse_url")) or "")
                        pipe.multi()
                        pipe.srem(idle_key, pod)
                        pipe.delete(self.k.pod_idle_since(pod))
                        await pipe.execute()
                        return ["reuse", pod, url]
                total = to_int(await pipe.zcard(pods_key)) \
                    + to_int(await pipe.zcard(dep_key))
                cap = to_int(max_pods_raw)
                # 排空纪元活跃（sunset drain）→ 容量上限 +SURGE_MARGIN(1)，
                # 与 sweeper.DRAIN_SURGE_MARGIN 对齐（2026-09-17 优雅排空）
                if await pipe.get(drain_key) is not None:
                    cap += 1
                if total >= cap:
                    await pipe.unwatch()
                    return ["max_reached", "", ""]
                pipe.multi()
                pipe.zadd(dep_key, {deploy_token: int(deadline)})
                await pipe.execute()
                return ["need_deploy", "", ""]

        return await self._retry(_txn, _WARM_ATTEMPTS)

    async def placeholder(self, scope_id: str, deploy_token: str,
                          deadline: int, now: int) -> list[str]:
        """LUA_PLACEHOLDER 等价：autoscale 专用占位（同 ACQUIRE 闸门，不碰 idle 池）。"""
        cfg_key = self.k.scope_config(scope_id)
        pods_key = self.k.scope_pods(scope_id)
        dep_key = self.k.scope_deploying(scope_id)
        drain_key = self.k.scope_drain_until(scope_id)

        await self.redis.zremrangebyscore(dep_key, "-inf", int(now))

        async def _txn() -> list[str]:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(cfg_key, pods_key, dep_key, drain_key)
                max_pods_raw = await pipe.hget(cfg_key, "max_pods")
                if max_pods_raw is None:
                    await pipe.unwatch()
                    return ["no_config"]
                total = to_int(await pipe.zcard(pods_key)) \
                    + to_int(await pipe.zcard(dep_key))
                cap = to_int(max_pods_raw)
                if await pipe.get(drain_key) is not None:
                    cap += 1
                if total >= cap:
                    await pipe.unwatch()
                    return ["max_reached"]
                pipe.multi()
                pipe.zadd(dep_key, {deploy_token: int(deadline)})
                await pipe.execute()
                return ["need_deploy"]

        return await self._retry(_txn, _WARM_ATTEMPTS)

    async def release(self, pod_id: str, scope_id: str, now: int) -> list[str]:
        """LUA_RELEASE 等价：转 idle 暖池 + 起 pod_ttl 计时（幂等）。

        EXISTS 守卫 + SADD 同事务（WATCH pod:info：PURGE 并发清删触发
        重试复读 → false，防重放 release 复活 idle 幽灵成员）。SADD 的
        「仅首次转入才起计时」判定取 EXEC 响应——SET idle_since 为事务后
        独立命令：进 MULTI 会使重放路径（SADD=0）也重写计时，破坏
        「重复 release 不刷新 idle_since」幂等（reclaim 将永不达成）。

        已知崩溃窗口（与 Lua 的唯一语义差异）：EXEC(SADD) 成功后、SET
        idle_since 前进程死亡 → idle 成员无计时，reclaim 跳过——由
        rm_reconcile 的 idle_since 补计时检测项自愈（reconcile_once）。
        """
        info_key = self.k.pod_info(pod_id)
        idle_key = self.k.scope_idle(scope_id)

        async def _txn() -> list[str]:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(info_key)
                # 幽灵守卫判活用 HLEN（EXISTS 在 ClusterPipeline 上
                # pre-MULTI 不立即执行，见 session_manager.no_lua 注释）
                if to_int(await pipe.hlen(info_key)) == 0:
                    await pipe.unwatch()
                    return ["false"]
                pipe.multi()
                pipe.sadd(idle_key, pod_id)
                resp = await pipe.execute()
            if resp and to_int(resp[0]) == 1:
                await self.redis.set(self.k.pod_idle_since(pod_id), int(now))
            return ["true"]

        return await self._retry(_txn, _WARM_ATTEMPTS)

    async def deploy_follower_gate(self, scope_id: str, follower_id: str,
                                   max_followers: int, deadline: int,
                                   now: int) -> list[str]:
        """LUA_DEPLOY_FOLLOWER_GATE 等价：等待室原子准入（上限内才可等待）。

        前置独立清理过期成员（幂等）+ WATCH 单键后「加前判定」：
        复读 ZCARD，n+1 超限即拒绝不加——Lua 为「先 ZADD 后 ZCARD 超限
        自退」，单键 WATCH 下两者等价（并发插入必触发 WatchError 重读，
        不可能超收）。
        """
        key = self.k.scope_deploy_followers(scope_id)
        await self.redis.zremrangebyscore(key, "-inf", int(now))

        async def _txn() -> list[str]:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                if to_int(await pipe.zcard(key)) + 1 > int(max_followers):
                    await pipe.unwatch()
                    return ["false"]
                pipe.multi()
                pipe.zadd(key, {follower_id: int(deadline)})
                await pipe.execute()
                return ["true"]

        return await self._retry(_txn, _WARM_ATTEMPTS)

    # -------------------------------------------------------------- 步4：冷路径

    async def register(self, pod_id: str, scope_id: str, url: str, ip: str,
                       ns: str, ver: str, token: str, idle_flag: str, now: int,
                       sse_port: Any, health_path: str) -> list[str]:
        """LUA_REGISTER 等价：deploy 成功登记 + generation 服务端烙印。

        WATCH scope:config 保持烙印原子性：注册与 bump_generation 并发时
        config 哈希变更 → WatchError → 重试复读新代——等价 Lua「注册与
        bump 在 Redis 单线程上原子排队」（晚于 bump 注册的 Pod 天然属新代）。
        """
        cfg_key = self.k.scope_config(scope_id)

        async def _txn() -> list[str]:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(cfg_key)
                gen_raw = await pipe.hget(cfg_key, "generation")
                gen = s(gen_raw) if gen_raw is not None else ""
                pipe.multi()
                pipe.hset(self.k.pod_info(pod_id), mapping={
                    "scope_id": scope_id, "pod_sse_url": url, "pod_ip": ip,
                    "namespace": ns, "deploy_ver": ver, "phase": "created",
                    "created_ts": int(now), "sse_port": sse_port,
                    "health_path": health_path, "generation": gen,
                })
                pipe.zadd(self.k.scope_pods(scope_id), {pod_id: int(now)})
                pipe.sadd(self.k.pods_all(), pod_id)
                pipe.zrem(self.k.scope_deploying(scope_id), token)
                if idle_flag == "1":
                    # 热备 Pod：入 idle 池（不变量 5；min_idle 底数保护）
                    pipe.sadd(self.k.scope_idle(scope_id), pod_id)
                    pipe.set(self.k.pod_idle_since(pod_id), int(now))
                await pipe.execute()
            return ["ok"]

        return await self._retry(_txn, _COLD_ATTEMPTS)

    async def pop_idle(self, pod_id: str, scope_id: str) -> list[str]:
        """LUA_POP_IDLE 等价：忙占用摘出 idle 暖池（幂等纯写集）。

        follower 接管 leader 热备 Pod 的忙记账——不摘则 autoscale 把忙 Pod
        数成热备（2026-09-17 follower_reuse 泄漏实录）。
        """
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.multi()
            pipe.srem(self.k.scope_idle(scope_id), pod_id)
            pipe.delete(self.k.pod_idle_since(pod_id))
            await pipe.execute()
        return ["ok"]

    async def purge(self, pod_id: str) -> list[str]:
        """LUA_PURGE 等价：清该 Pod 全部 RM key（幂等）。返回其 scope_id。

        WATCH pod:info：scope 从 info 复读，与清删在同一事务内——并发
        重注册（同 pod_id 换 scope）触发 WatchError 重试复读，等价 Lua
        串行化，不留"新 scope 集合残留成员"的泄漏窗口。
        """
        info_key = self.k.pod_info(pod_id)

        async def _txn() -> list[str]:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(info_key)
                scope_raw = await pipe.hget(info_key, "scope_id")
                scope = s(scope_raw) if scope_raw is not None else ""
                pipe.multi()
                pipe.delete(info_key, self.k.pod_idle_since(pod_id),
                            self.k.pod_health_fails(pod_id))
                if scope:
                    pipe.zrem(self.k.scope_pods(scope), pod_id)
                    pipe.srem(self.k.scope_idle(scope), pod_id)
                pipe.srem(self.k.pods_all(), pod_id)
                await pipe.execute()
            return ["ok", scope]

        return await self._retry(_txn, _COLD_ATTEMPTS)


_HANDLERS: dict[str, Any] = {
    lua.LUA_ACQUIRE: ResourceNoLua.acquire,
    lua.LUA_PLACEHOLDER: ResourceNoLua.placeholder,
    lua.LUA_REGISTER: ResourceNoLua.register,
    lua.LUA_RELEASE: ResourceNoLua.release,
    lua.LUA_POP_IDLE: ResourceNoLua.pop_idle,
    lua.LUA_PURGE: ResourceNoLua.purge,
    lua.LUA_DEPLOY_FOLLOWER_GATE: ResourceNoLua.deploy_follower_gate,
}
