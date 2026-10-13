# coding: utf-8
"""Redis 无 Lua（EVAL 被禁）兼容模式真环境验证（AGENT_RUNTIME_REDIS_LUA=off）。

目标环境：服务端以 rename-command EVAL ""（或 ACL 撤权）禁用脚本的
Redis——单实例/哨兵/Cluster 均可。验证项：
  [0] 环境自证：EVAL "return 1" 必须报错（证明目标真禁了 EVAL，防假绿）
  [1] 框架层：TickLock 续期/释放 + SingleLeaderCoordinator 选主
      （服务端拒绝 EVAL → redis_compat 惰性降级 WATCH/MULTI/EXEC）
  [2] SM 状态层（use_lua=False）：route_place 全分支 / rebind 五态 /
      touch / evict / 并发不超卖
  [3] RM 状态层（use_lua=False）：acquire 闸门与复用 / generation 烙印与
      过滤 / release 幂等计时 / pop_idle / drain surge

用法（applications/agent_runtime 下）：
  uv run --no-sync python scripts/verify_redis_no_lua.py \
      --url redis://127.0.0.1:6399 [--wipe]
  # cluster:--url redis+cluster://127.0.0.1:7001

本地一次性禁 EVAL 实例：
  docker run -d -p 6399:6379 redis:7 redis-server \
      --rename-command EVAL "" --rename-command EVALSHA ""
  # cluster 变体：三节点各加 --rename-command EVAL ""（搭建命令见
  # verify_redis_cluster.py 头注）

注意：会在目标库写入 ``{session_manager}:`` / ``{resource_manager}:``
前缀键（选主键自带 TTL 秒级自灭；业务键用 --wipe 收尾清掉）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from agent_runtime.resource_manager.state import ResourceState
from agent_runtime.session_manager.state import SessionState
from agent_runtime.util import now_ts

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  -- {detail}" if detail else ""))


async def _wipe_own_keys(client: Any) -> int:
    n = 0
    for pattern in ("{session_manager}:*", "{resource_manager}:*"):
        async for key in client.scan_iter(match=pattern, count=200):
            await client.delete(key)
            n += 1
    return n


async def verify(url: str, wipe: bool) -> int:
    if "+cluster" in url:
        from openjiuwen_runtime.service.bootstrap import (
            _build_redis_cluster_client,
        )

        client = _build_redis_cluster_client(url, {"decode_responses": False})
    else:
        from redis.asyncio import Redis

        client = Redis.from_url(url, decode_responses=False)
    await client.initialize() if hasattr(client, "initialize") else None
    if wipe:
        await _wipe_own_keys(client)

    from redis.exceptions import ResponseError

    # [0] 环境自证：EVAL 必须被服务端拒绝（unknown command / noperm 两族）
    try:
        await client.eval("return 1", 1, "verify:no-lua")
        check("环境自证: EVAL 被禁(服务端拒绝)", False, "EVAL 居然可用——目标环境未禁 EVAL?")
        return 1
    except ResponseError as exc:
        check("环境自证: EVAL 被禁(服务端拒绝)", True, str(exc)[:80])

    now = now_ts()

    # [1] 框架层：锁/选主在真禁 EVAL 服务上惰性降级
    from openjiuwen_runtime.service.context.periodic.coordinator.single_leader import (
        SingleLeaderCoordinator,
    )
    from openjiuwen_runtime.service.context.periodic.lock import TickLock

    lock = TickLock(client, lock_key="{agent_runtime:job:nolua}",
                    lock_ttl_sec=2, token_prefix="job", instance_id="v1")
    tok = await lock.try_acquire()
    renewed = await lock.renew_once(tok) if tok else False
    released = await lock.release_if_owner(tok) if tok else False
    check("框架: TickLock 抢/续/释(降级路径)", bool(tok) and renewed and released)

    coord_a = SingleLeaderCoordinator(
        client, lock_key="{agent_runtime:job:nolua2}", instance_id="a",
        gather_window_sec=0.0)
    coord_b = SingleLeaderCoordinator(
        client, lock_key="{agent_runtime:job:nolua2}", instance_id="b",
        gather_window_sec=0.0)
    ta = await coord_a.try_claim(now=now, instance_id="a", planned_fire=now)
    tb = await coord_b.try_claim(now=now, instance_id="b", planned_fire=now)
    check("框架: 选主恰一当选(降级路径)", (ta is None) != (tb is None),
          f"a={ta is not None} b={tb is not None}")
    await (coord_a if ta else coord_b).release(ta or tb)

    # [2] SM 状态层（use_lua=False；键布局与 Lua 模式逐字节一致）
    sm = SessionState(client, use_lua=False)
    act, _ = await sm.route_place("v-s1", "v-scope", now + 60, 60, 10, 10, 2, now)
    check("SM: 无 Pod 时 route_place=need_acquire", act == "need_acquire", act)
    await sm.register_pod("v-scope", "v-pod-1", "http://127.0.0.1:8086", "v1")
    act, pod = await sm.route_place("v-s1", "v-scope", now + 60, 60, 10, 10, 2, now)
    check("SM: 注册后 route_place=placed", act == "placed" and pod == "v-pod-1",
          f"{act}/{pod}")
    act, pod = await sm.route_place("v-s1", "v-scope", now + 120, 60, 10, 10, 2, now)
    check("SM: 亲和命中 refresh", act == "refresh" and pod == "v-pod-1",
          f"{act}/{pod}")
    touched, _ = await sm.touch("v-s1", now + 1)
    check("SM: touch 保活", touched is True)
    rb = await sm.rebind("v-s1", "v-real", now=now + 1)
    members = await client.smembers(sm.k.scope_sessions("v-scope"))
    check("SM: rebind 原子搬移", rb.get("action") == "rebound"
          and members == {b"v-real"}, f"{rb.get('action')}/{sorted(members)}")
    evicted = await sm.evict("v-real")
    check("SM: evict 四处同删", evicted is not None
          and evicted.get("scope_id") == "v-scope")

    # [2b] 并发不超卖（40 路 vs scope_cc=20/pod_cc=5/4 Pod）
    for i in range(4):
        await sm.register_pod("v-hammer", f"v-hp{i}", "http://x", "v1")
    outcomes: list[str] = []

    async def _one(i: int) -> None:
        act, _ = await sm.route_place(
            f"v-h{i}", "v-hammer", now + 60, 60, 20, 5, 6, now)
        outcomes.append(act)

    await asyncio.gather(*(_one(i) for i in range(40)))
    scope_n = await sm.scope_session_count("v-hammer")
    pod_counts = [
        len(await client.smembers(sm.k.pod_sessions("v-hammer", f"v-hp{i}")))
        for i in range(4)]
    pod_ok = all(n <= 5 for n in pod_counts)
    check("SM: 40 路并发无超卖(placed=20/full=20)",
          outcomes.count("placed") == 20 and scope_n == 20 and pod_ok,
          f"placed={outcomes.count('placed')} scope_n={scope_n}")

    # [3] RM 状态层
    rm = ResourceState(client, use_lua=False)
    await rm.save_scope_config("v-scope", {
        "scope_concurrency": 10, "pod_concurrency": 10, "max_pods": 2,
        "pod_ttl": 600, "min_idle_pods": 0})
    act, _, _ = await rm.acquire("v-scope", "v-ver-1", "v-tok-1")
    check("RM: acquire=need_deploy(占位)", act == "need_deploy", act)
    await rm.register_pod(
        pod_id="v-pod-r1", scope_id="v-scope",
        pod_sse_url="http://127.0.0.1:8086", pod_ip="127.0.0.1",
        namespace="default", deploy_ver="v-ver-1", deploy_token="v-tok-1",
        idle_flag=True, now=now_ts(), sse_port=8086, health_path="/health")
    act, pod, _ = await rm.acquire("v-scope", "v-ver-1", "v-tok-2")
    check("RM: 暖 Pod reuse(占位同时被 ZREM)",
          act == "reuse" and pod == "v-pod-r1", f"{act}/{pod}")
    check("RM: deploy 占位清零", await rm.deploying_count("v-scope") == 0)
    await rm.release("v-pod-r1", "v-scope", now_ts())
    t0 = await rm.idle_since("v-pod-r1")
    await rm.release("v-pod-r1", "v-scope", now_ts() + 999)   # 重放不刷新计时
    check("RM: release 幂等不刷新 idle_since",
          t0 > 0 and await rm.idle_since("v-pod-r1") == t0)
    gen = await rm.bump_generation("v-scope")
    act, _, _ = await rm.acquire("v-scope", "v-ver-1", "v-tok-3")
    check("RM: bump 后老代暖 Pod 被过滤(need_deploy)", gen == 1 and act == "need_deploy",
          f"gen={gen}/{act}")
    await rm.clear_deploy_token("v-scope", "v-tok-3")
    # drain surge：排空纪元 cap+1
    await rm.save_scope_config("v-surge", {"max_pods": 0, "pod_ttl": 600})
    act, _, _ = await rm.acquire("v-surge", "v", "v-st1")
    check("RM: max_pods=0 无排空 → max_reached", act == "max_reached", act)
    await rm.set_drain_until("v-surge", now_ts() + 600, 700)
    act, _, _ = await rm.acquire("v-surge", "v", "v-st2")
    check("RM: 排空纪元 surge cap+1 放行", act == "need_deploy", act)
    await rm.clear_deploy_token("v-surge", "v-st2")
    # pop_idle
    await rm.pop_idle("v-pod-r1", "v-scope")
    check("RM: pop_idle 摘池清计时",
          await rm.idle_pods("v-scope") == [] and await rm.idle_since("v-pod-r1") == 0)
    # reconcile 自愈（无计时 idle 成员补计时）
    await rm.release("v-pod-r1", "v-scope", now_ts())
    await client.delete(rm.k.pod_idle_since("v-pod-r1"))   # 模拟崩溃窗口
    healed = await rm.set_idle_since_if_absent("v-pod-r1", now_ts())
    check("RM: reconcile 自愈补计时(崩溃窗口兜底)",
          healed and await rm.idle_since("v-pod-r1") == now_ts())
    await rm.purge("v-pod-r1")

    if wipe:
        await _wipe_own_keys(client)
    await client.aclose()

    fails = [r for r in _RESULTS if not r[1]]
    print(f"\n== {len(_RESULTS) - len(fails)}/{len(_RESULTS)} PASS ==")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True,
                    help="redis://(禁 EVAL 单实例) 或 redis+cluster://(禁 EVAL cluster)")
    ap.add_argument("--wipe", action="store_true", help="运行前后清本脚本前缀键")
    sys.exit(asyncio.run(verify(ap.parse_args().url, ap.parse_args().wipe)))
