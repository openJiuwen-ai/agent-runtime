# coding: utf-8
"""SM Lua 脚本的无 Lua 实现（WATCH/MULTI/EXEC，EVAL 禁用环境）。

设计约束（与 ``lua_scripts.py`` 一一对应,评审对照按脚本名索引）：
- **同参数同返回**：各 handler 接收与 Lua ARGV 完全一致的参数序（不含
  键前缀——那是 eval 出口的 ARGV[1]，此处直接用 ``SMKeys`` 拼完整键），
  返回同一扁平字符串表——门面层解析零改动；
- **强一致范式**：read-decide-write 一律「预读 → WATCH 决策依赖键 →
  pipe 复读关键前提 → MULTI 写集 → EXEC」；被 WATCH 键在提交前被并发
  改写即 WatchError → jitter 退避重试；纯写集脚本只 MULTI/EXEC（Redis
  事务本身原子）。与 Lua 单线程串行化在并发语义上等价（不超卖/不断
  亲和/不泄漏额度）；仅进程在「独立命令与 EXEC 之间」崩溃的极小窗口
  存在差异,由 sweeper/reconcile 周期自愈；
- **键布局不变**：SM 键域整体在 ``{session_manager}`` hash tag 内同槽,
  WATCH/MULTI/EXEC 在 Redis Cluster 下同样成立（ClusterPipeline 同槽
  事务,跨槽客户端侧即拒绝）。
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from redis.exceptions import WatchError

from openjiuwen_runtime.service.context.redis_compat import backoff_delay

from ..util import s, to_int
from . import lua_scripts as lua

if TYPE_CHECKING:  # 避免与 state.py 循环导入（state 构造 SessionNoLua）
    from .state import SMKeys

# 热路径（route/touch/rebind）重试次数多、退避上限大；温/冷路径收敛快。
# route 的 WATCH 是 scope 级热点（每次放置提交必 SADD scope:sessions，
# 踢掉全部竞争者——乐观锁惊群），真环境 40 路并发实测 8×[0,50ms) 仍可能
# 耗尽，故热路径用更宽的 12×[0,120ms)（正常零竞争一遍过，风暴才用满）。
_HOT_ATTEMPTS = 12
_HOT_BACKOFF = {"base_ms": 5.0, "cap_ms": 120.0}
_WARM_ATTEMPTS = 5

# 残骸自清后的落穿重入哨兵（route_place 专用，对齐 Lua 落穿语义）
_REDO = object()


class SessionNoLua:
    """SM 7 个 Lua 的无 Lua 等价实现（经 ``SessionState.eval`` 分派调用）。"""

    def __init__(self, redis: Any, keys: "SMKeys") -> None:
        self.redis = redis
        self.k = keys
        # 放置分支的进程内 per-scope 串行锁（见 _placement_lock 注释）
        self._place_locks: dict[str, asyncio.Lock] = {}

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
        """WatchError 乐观重试（全抖动退避）；耗尽上抛（热路径→503，周期→下拍）。"""
        for attempt in range(attempts):
            try:
                return await fn()
            except WatchError:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(
                    backoff_delay(attempt, base_ms=base_ms, cap_ms=cap_ms)
                )

    # -------------------------------------------------------------- 步6：热路径

    def _placement_lock(self, scope_id: str) -> asyncio.Lock:
        """放置分支的进程内 per-scope 串行锁。

        乐观锁惊群实录（真 cluster 40 路并发实测）：每次放置提交必 SADD
        scope:sessions，踢掉**全部**竞争者的 WATCH——40 任务互踩 349 次
        abort、18 任务 12 次重试全耗尽（协同调度下输家恒定排在队尾）。
        风暴主体是单进程内并发：放置分支进程内串行化后，WATCH 只需应对
        跨副本竞争（副本数 ≤ 个位数，乐观重试收敛）。锁仅约束本 State
        实例（生产每进程一个 SessionState）；多 State 共享同一 Redis 的
        测试拓扑退回纯乐观路径，由重试兜底。
        """
        lock = self._place_locks.get(scope_id)
        if lock is None:
            lock = self._place_locks.setdefault(scope_id, asyncio.Lock())
        return lock

    async def route_place(self, session_id: str, scope_id: str, expiry_ts: Any,
                          session_ttl: Any, scope_cc: Any, pod_cc: Any,
                          max_pods: Any, now: Any) -> list[str]:
        """LUA_ROUTE_PLACE 等价：route 原子核心（亲和/闸门/first-fit/提交）。

        两段式：
        - 绑定段（窄 WATCH：session 键 + 亲和 Pod info）——亲和命中仅续期
          （场景 A）；过期/判死绑定惰性回收后返回 rebind 由调用方换
          first-fit 结果重试（本调用内不落放置，scope 亲和保持，
          2026-09-scope-affinity-hold）；残骸自清后 _REDO 落穿全新放置。
        - 放置段（进程内 per-scope 锁 + 最小 WATCH 集预读复读提交）：
          三重闸门（scope_cc/pod_cc/max_pods）与「四处同写」不变量经
          WATCH 闭环强校验——任何并发放置必 SADD scope:sessions →
          WatchError 重试，闸门不可能超卖。偏差：并发下 first-fit 可能
          选中接入序稍后的 Pod（公平性微偏，非不变量——Lua 本身也只是
          任意串行序）。
        """
        expiry_ts, session_ttl = int(expiry_ts), int(session_ttl)
        scope_cc, pod_cc, max_pods, now = (
            int(scope_cc), int(pod_cc), int(max_pods), int(now))
        skey = self.k.session(session_id)

        for attempt in range(_HOT_ATTEMPTS):
            try:
                binding = await self._binding_attempt(
                    skey, session_id, scope_id, expiry_ts, session_ttl, now,
                )
                if binding is None:
                    # 无既有绑定 → 放置段（进程内串行 + 乐观提交）
                    async with self._placement_lock(scope_id):
                        return await self._placement_attempt(
                            skey, session_id, scope_id, expiry_ts, session_ttl,
                            scope_cc, pod_cc, max_pods,
                        )
                if binding is not _REDO:
                    return binding
            except WatchError:
                if attempt == _HOT_ATTEMPTS - 1:
                    raise
            await asyncio.sleep(backoff_delay(attempt, **_HOT_BACKOFF))
        raise WatchError(  # pragma: no cover - 竞争风暴兜底（503 快失败）
            f"route_place watch contention exhausted: sid={session_id}")

    async def _binding_attempt(self, skey: str, sid: str, scope: str,
                               expiry: int, sttl: int,
                               now: int) -> list[str] | None:
        """绑定段：返回 refresh/rebind/_REDO；None = 无绑定（走放置段）。"""
        async with self.redis.pipeline(transaction=True) as pipe:
            await pipe.watch(skey)
            flat = await pipe.hgetall(skey)
            if not flat:
                await pipe.unwatch()
                return None
            m = {s(k_): s(v) for k_, v in flat.items()}
            # 残骸自卫：缺 scope/pod/expiry 的半成品 → 自清后落穿全新放置
            if m.get("scope_id") is None or m.get("pod_id") is None \
                    or m.get("expiry") is None:
                pipe.multi()
                pipe.zrem(self.k.session_expiry(), sid)
                pipe.delete(skey)
                await pipe.execute()
                return _REDO
            bound_pod = m["pod_id"]
            info_key = self.k.pod_info(scope, bound_pod)
            # 亲和命中且未过期且 Pod 注册仍在 → 仅续期（场景 A）。
            # 注意判活用 HLEN 不用 EXISTS：ClusterPipeline 上 EXISTS 属可拆分
            # 多键命令，pre-MULTI 不立即执行（返回 pipeline 对象）——单键
            # 命令（HLEN/GET/SCARD/HGETALL…）才走立即执行路径
            if m["scope_id"] == scope and int(m["expiry"]) > now:
                await pipe.watch(info_key)
                if to_int(await pipe.hlen(info_key)) > 0:
                    pipe.multi()
                    pipe.hset(skey, mapping={"expiry": expiry,
                                             "session_ttl": sttl})
                    pipe.zadd(self.k.session_expiry(), {sid: expiry})
                    await pipe.execute()
                    return ["refresh", bound_pod]
            # 已过期 / Pod 注册消失 / scope 已不同 → 惰性回收旧绑定
            old_scope = m["scope_id"]
            pipe.multi()
            pipe.srem(self.k.scope_sessions(old_scope), sid)
            pipe.srem(self.k.pod_sessions(old_scope, bound_pod), sid)
            pipe.zrem(self.k.session_expiry(), sid)
            pipe.delete(skey)
            await pipe.execute()
            return ["rebind", ""]

    async def _placement_attempt(self, skey: str, sid: str, scope: str,
                                 expiry: int, sttl: int, scope_cc: int,
                                 pod_cc: int, max_pods: int) -> list[str]:
        """放置段：预读决策 → 最小 WATCH 集复读 → 原子提交。

        进程内锁已消除本地惊群，WATCH 仅防跨副本/其他写者；skey 一并
        WATCH（防御同 sid 并发绑定创建）。复读间选中 Pod 被占满 → 抛
        WatchError 由外层重试重解（闸门不超卖）。
        """
        scope_sessions = self.k.scope_sessions(scope)
        scope_pods = self.k.scope_pods(scope)
        # 预读（普通读，锁内本进程无竞争）：闸门短路 + first-fit 决策
        if to_int(await self.redis.scard(scope_sessions)) >= scope_cc:
            return ["scope_full", ""]
        pods = [s(p) for p in await self.redis.zrange(scope_pods, 0, -1)]
        chosen = ""
        for pod in pods:
            if to_int(await self.redis.scard(
                    self.k.pod_sessions(scope, pod))) < pod_cc:
                chosen = pod
                break
        if not chosen:
            # 现有 Pod 都满：达 max_pods → scope_full；否则 need_acquire
            if len(pods) >= max_pods:
                return ["scope_full", ""]
            return ["need_acquire", ""]
        chosen_sessions = self.k.pod_sessions(scope, chosen)
        async with self.redis.pipeline(transaction=True) as pipe:
            await pipe.watch(skey, scope_sessions, scope_pods, chosen_sessions)
            # 权威复读：闸门（scope 计数 + 选中 Pod 容量）
            if to_int(await pipe.scard(scope_sessions)) >= scope_cc:
                await pipe.unwatch()
                return ["scope_full", ""]
            if to_int(await pipe.scard(chosen_sessions)) >= pod_cc:
                raise WatchError(  # 复读间被占满 → 外层重解（不超卖）
                    f"chosen pod filled during recheck: pod={chosen}")
            pipe.multi()
            pipe.sadd(scope_sessions, sid)
            pipe.sadd(chosen_sessions, sid)
            pipe.hset(skey, mapping={"scope_id": scope, "pod_id": chosen,
                                     "expiry": expiry, "session_ttl": sttl})
            pipe.zadd(self.k.session_expiry(), {sid: expiry})
            pipe.delete(self.k.pod_idle_notified(scope, chosen))
            await pipe.execute()
            return ["placed", chosen]

    async def evict(self, session_id: str) -> list[str]:
        """LUA_EVICT 等价：session 移除唯一原语（四处同删，幂等）。

        WATCH skey 覆盖身份决策：并发 evict/rebind/touch 改写或删除该
        哈希均触发 WatchError → 重试复读（空 → noop，与 Lua 串行等价）。
        remaining 经 MULTI 末位 SCARD 从 EXEC 响应取（事务内自洽）。
        """
        skey = self.k.session(session_id)

        async def _txn() -> list[str]:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(skey)
                flat = await pipe.hgetall(skey)
                if not flat:
                    await pipe.unwatch()
                    return ["noop", "", "", "0"]
                m = {s(k_): s(v) for k_, v in flat.items()}
                scope, pod = m.get("scope_id"), m.get("pod_id")
                if scope is None or pod is None:
                    # 残骸自清（不上抛——单坏键不得让到期 pass 崩溃循环）
                    pipe.multi()
                    pipe.zrem(self.k.session_expiry(), session_id)
                    pipe.delete(skey)
                    await pipe.execute()
                    return ["rubble", "", "", "0"]
                pod_sessions = self.k.pod_sessions(scope, pod)
                pipe.multi()
                pipe.srem(self.k.scope_sessions(scope), session_id)
                pipe.srem(pod_sessions, session_id)
                pipe.zrem(self.k.session_expiry(), session_id)
                pipe.delete(skey)
                pipe.scard(pod_sessions)
                resp = await pipe.execute()
            return ["evicted", scope, pod, str(to_int(resp[4]))]

        return await self._retry(_txn, _WARM_ATTEMPTS)

    async def rebind(self, from_id: str, to_id: str, now: Any,
                     default_ttl: int = 60) -> list[str]:
        """LUA_REBIND 等价：临时路由 key 原子改绑（五态）。

        WATCH {from 键, to 键, 绑定 Pod info}（两段补 WATCH，各判定读前
        落位）：to 占用判定（overtaken）与注册存活判定（evicted）均在
        WATCH 保护下复读，与 Lua CAS 串行化等价。四处不变量整体搬移为
        单事务（rebound）；remaining 经 MULTI 末位 SCARD 取。
        """
        now = int(now)
        default_ttl = int(default_ttl)
        fkey = self.k.session(from_id)

        async def _clear_from(pipe: Any, scope: str, pod: str,
                              with_remaining: bool) -> list[str]:
            pod_sessions = self.k.pod_sessions(scope, pod)
            pipe.multi()
            pipe.srem(self.k.scope_sessions(scope), from_id)
            pipe.srem(pod_sessions, from_id)
            pipe.zrem(self.k.session_expiry(), from_id)
            pipe.delete(fkey)
            if with_remaining:
                pipe.scard(pod_sessions)
                resp = await pipe.execute()
                return str(to_int(resp[4]))
            await pipe.execute()
            return "0"

        async def _txn() -> list[str]:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(fkey)
                flat = await pipe.hgetall(fkey)
                if not flat:
                    await pipe.unwatch()
                    return ["noop", "", "", "0"]
                m = {s(k_): s(v) for k_, v in flat.items()}
                if m.get("scope_id") is None or m.get("pod_id") is None:
                    # 残骸自清（不上抛，防 create 收尾对同一坏键永久失败）
                    pipe.multi()
                    pipe.zrem(self.k.session_expiry(), from_id)
                    pipe.delete(fkey)
                    await pipe.execute()
                    return ["rubble", "", "", "0"]
                scope, pod = m["scope_id"], m["pod_id"]

                # to 为空 = 驱逐（create 失败立即释放槽位）；to==from 幂等
                if to_id == "" or to_id == from_id:
                    if to_id == "":
                        remaining = await _clear_from(pipe, scope, pod, True)
                        return ["evicted", scope, pod, remaining]
                    await pipe.unwatch()
                    return ["noop", scope, pod, "0"]

                tkey = self.k.session(to_id)
                await pipe.watch(tkey)
                # to 已有绑定（首条 chat.send 抢先放置）：保留 to 仅清 from
                # （判存在用 HLEN——EXISTS 在 ClusterPipeline 上 pre-MULTI
                #  不立即执行，见 _binding_attempt 注释）
                if to_int(await pipe.hlen(tkey)) > 0:
                    remaining = await _clear_from(pipe, scope, pod, True)
                    return ["overtaken", scope, pod, remaining]
                # 绑定 Pod 注册已消失（notify_pod_dead 清理窗口）→ evicted
                info_key = self.k.pod_info(scope, pod)
                await pipe.watch(info_key)
                if to_int(await pipe.hlen(info_key)) == 0:
                    remaining = await _clear_from(pipe, scope, pod, False)
                    return ["evicted", scope, pod, remaining]

                # 搬移：四处不变量整体 from → to，TTL 刷新（含 from 过期未扫
                # 的复活场景）；scope/pod 槽位计数不变（一删一加）
                try:
                    ttl = int(m["session_ttl"])
                except (TypeError, ValueError):
                    ttl = default_ttl
                new_expiry = now + ttl
                pod_sessions = self.k.pod_sessions(scope, pod)
                pipe.multi()
                pipe.sadd(self.k.scope_sessions(scope), to_id)
                pipe.srem(self.k.scope_sessions(scope), from_id)
                pipe.sadd(pod_sessions, to_id)
                pipe.srem(pod_sessions, from_id)
                pipe.hset(tkey, mapping={
                    "scope_id": scope, "pod_id": pod,
                    "expiry": str(new_expiry), "session_ttl": str(ttl),
                })
                pipe.delete(fkey)
                pipe.zrem(self.k.session_expiry(), from_id)
                pipe.zadd(self.k.session_expiry(), {to_id: new_expiry})
                pipe.delete(self.k.pod_idle_notified(scope, pod))
                pipe.scard(pod_sessions)
                resp = await pipe.execute()
            return ["rebound", scope, pod, str(to_int(resp[9]))]

        return await self._retry(_txn, _HOT_ATTEMPTS, **_HOT_BACKOFF)

    async def touch(self, session_id: str, now: Any,
                    default_ttl: int = 60) -> list[str]:
        """LUA_TOUCH 等价：保活续期（已过期当场惰性 evict）。

        WATCH skey 覆盖全部决策（身份/expiry/session_ttl 均在该哈希）：
        evict/rebind 必 DEL 该键、route/refresh 必 HSET 该键——任何并发
        生命周期写都在 EXEC 处触发重试复读。session_ttl 就地读（不依赖
        scope:config）。
        """
        now = int(now)
        default_ttl = int(default_ttl)
        skey = self.k.session(session_id)

        async def _txn() -> list[str]:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(skey)
                flat = await pipe.hgetall(skey)
                if not flat:
                    await pipe.unwatch()
                    return ["false", ""]
                m = {s(k_): s(v) for k_, v in flat.items()}
                # 残骸自清（tonumber nil 比较是 Lua runtime error 的 Python
                # 等价防护）→ 返回不存在，gateway 回退重新 route
                if m.get("scope_id") is None or m.get("pod_id") is None \
                        or m.get("expiry") is None:
                    pipe.multi()
                    pipe.zrem(self.k.session_expiry(), session_id)
                    pipe.delete(skey)
                    await pipe.execute()
                    return ["false", ""]
                # 惰性兜底：已过期当场 evict
                if int(m["expiry"]) <= now:
                    pipe.multi()
                    pipe.srem(self.k.scope_sessions(m["scope_id"]), session_id)
                    pipe.srem(
                        self.k.pod_sessions(m["scope_id"], m["pod_id"]),
                        session_id)
                    pipe.zrem(self.k.session_expiry(), session_id)
                    pipe.delete(skey)
                    await pipe.execute()
                    return ["false", ""]
                try:
                    ttl = int(m["session_ttl"])
                except (TypeError, ValueError):
                    ttl = default_ttl
                new_expiry = now + ttl
                pipe.multi()
                pipe.hset(skey, "expiry", str(new_expiry))
                pipe.zadd(self.k.session_expiry(), {session_id: new_expiry})
                await pipe.execute()
            return ["true", m["pod_id"]]

        return await self._retry(_txn, _HOT_ATTEMPTS, **_HOT_BACKOFF)

    # -------------------------------------------------------------- 步5：温路径

    async def sweep_idle_notify(self, scope_id: str, pod_id: str) -> list[str]:
        """LUA_SWEEP_IDLE_NOTIFY 等价：空 Pod pass 原子判定 + NX 去重 + 摘候选。

        WATCH {pod:sessions, scope:pods} + 复读 SCARD==0 + MULTI
        (SET NX 去重标记 + ZREM 退出 first-fit)——SET NX 结果从 EXEC 响应
        判定（输给并发通知者 → false）。route 在复读与 EXEC 之间往该 Pod
        落会话 → pod:sessions 变更 → WatchError → 重试后 SCARD≠0 → false，
        堵「reclaim 窗口内 route 直选」竞态（竞态 A）与 Lua 等价。
        """
        pod_sessions = self.k.pod_sessions(scope_id, pod_id)
        scope_pods = self.k.scope_pods(scope_id)
        notified_key = self.k.pod_idle_notified(scope_id, pod_id)

        async def _txn() -> list[str]:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(pod_sessions, scope_pods)
                if to_int(await pipe.scard(pod_sessions)) != 0:
                    await pipe.unwatch()
                    return ["false"]
                pipe.multi()
                pipe.set(notified_key, "1", nx=True, ex=60)
                pipe.zrem(scope_pods, pod_id)
                resp = await pipe.execute()
            # SET NX 失败（EXEC 响应为 None/False）= 60s 去重窗内已通知
            return ["true"] if resp and resp[0] else ["false"]

        return await self._retry(_txn, _WARM_ATTEMPTS)

    # -------------------------------------------------------------- 步4：冷路径

    async def register_pod(self, scope_id: str, pod_id: str, sse_url: str,
                           deploy_ver: str) -> list[str]:
        """LUA_REGISTER_POD 等价：三处注册同写 + 接入序（不变量 5）。

        seq 经独立 INCR（单命令原子）；崩溃于 INCR 与 MULTI 之间仅留 seq
        空洞——接入序只要求单调，空洞无害。纯写集 MULTI（幂等重放 =
        ZADD/HSET 覆盖同 Lua），无需 WATCH。
        """
        seq = await self.redis.incr(self.k.scope_pod_seq(scope_id))
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.multi()
            pipe.zadd(self.k.scope_pods(scope_id), {pod_id: seq})
            pipe.hset(self.k.pod_info(scope_id, pod_id),
                      mapping={"sse_url": sse_url, "deploy_ver": deploy_ver})
            pipe.sadd(self.k.pods_registered(), f"{scope_id}:{pod_id}")
            pipe.sadd(self.k.pod_scopes(pod_id), scope_id)
            pipe.delete(self.k.pod_idle_notified(scope_id, pod_id))
            await pipe.execute()
        return ["ok"]

    async def cleanup_pod(self, scope_id: str, pod_id: str) -> list[str]:
        """LUA_CLEANUP_POD 等价：清该 (scope,pod) 的全部注册（幂等纯写集）。"""
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.multi()
            pipe.zrem(self.k.scope_pods(scope_id), pod_id)
            pipe.delete(
                self.k.pod_sessions(scope_id, pod_id),
                self.k.pod_info(scope_id, pod_id),
                self.k.pod_idle_notified(scope_id, pod_id),
            )
            pipe.srem(self.k.pods_registered(), f"{scope_id}:{pod_id}")
            pipe.srem(self.k.pod_scopes(pod_id), scope_id)
            await pipe.execute()
        return ["ok"]


_HANDLERS: dict[str, Any] = {
    lua.LUA_ROUTE_PLACE: SessionNoLua.route_place,
    lua.LUA_EVICT: SessionNoLua.evict,
    lua.LUA_REBIND: SessionNoLua.rebind,
    lua.LUA_TOUCH: SessionNoLua.touch,
    lua.LUA_SWEEP_IDLE_NOTIFY: SessionNoLua.sweep_idle_notify,
    lua.LUA_REGISTER_POD: SessionNoLua.register_pod,
    lua.LUA_CLEANUP_POD: SessionNoLua.cleanup_pod,
}
