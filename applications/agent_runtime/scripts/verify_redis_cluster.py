# coding: utf-8
"""Redis Cluster 兼容性验证（需真 cluster——fakeredis 测不出跨槽行为）。

对应 2026-08-28 jcl-dev 生产日志的两类故障：
1. ``RedisClusterException: EVAL - all keys must map to the same key slot``
   —— 选主抽签 Lua 双键（winner/candidates）跨槽；
2. ``ResponseError: Script attempted to access a non local key``
   —— SM/RM Lua ``numkeys=0`` 随机路由，脚本摸到非归属节点的键。

验证项：
  [0] 环境自证：旧式双键 EVAL 必炸（证明目标是真 cluster，防假绿）
  [1] bootstrap：``redis+cluster://`` 构造集群客户端；URL 带库号快速失败
  [2] 选主协调器：抽签（winner/candidates 经 hash tag 同槽）
  [3] SM 状态层：route_place / register_pod / touch / evict
  [4] RM 状态层：acquire / scope 配置 / known_scope_ids（SCAN dict 游标）

用法（applications/agent_runtime 下）：
  uv run --no-sync python scripts/verify_redis_cluster.py \
      --url redis+cluster://127.0.0.1:7001 [--wipe]

本地一次性三主 cluster：
  docker network create ar-cluster-net
  for i in 1 2 3; do docker run -d --name arc$i --network ar-cluster-net \
      -p 700$i:700$i redis:7 redis-server --port 700$i --cluster-enabled yes \
      --cluster-config-file nodes.conf --cluster-node-timeout 5000; done
  docker run --rm --network ar-cluster-net redis:7 \
      redis-cli --cluster create arc1:7001 arc2:7002 arc3:7003 --cluster-yes

注意：会在目标 cluster 写入 ``{session_manager}:`` / ``{resource_manager}:``
前缀的键（选主键自带 TTL 秒级自灭；业务键用 --wipe 收尾清掉）。
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


def check(name: bool | None, ok: bool, detail: str = "") -> None:
    _RESULTS.append((str(name), bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  -- {detail}" if detail else ""))


async def _wipe_own_keys(client: Any) -> int:
    """清本脚本前缀键（幂等起点/收尾；只碰 SM/RM 两个 tag 前缀）。"""
    n = 0
    for pattern in ("{session_manager}:*", "{resource_manager}:*"):
        async for key in client.scan_iter(match=pattern, count=200):
            await client.delete(key)
            n += 1
    return n


async def verify(url: str, wipe: bool) -> int:
    from openjiuwen_runtime.service.bootstrap import (
        RedisUnavailable,
        _build_redis_cluster_client,
    )
    from openjiuwen_runtime.service.context.periodic.coordinator.single_leader import (
        SingleLeaderCoordinator,
    )
    from redis.exceptions import RedisClusterException, ResponseError

    # [1] bootstrap 构造
    client = _build_redis_cluster_client(url, {"decode_responses": False})
    check("bootstrap: redis+cluster:// 构造集群客户端", client is not None)
    if wipe:  # 幂等起点：清掉上次运行/手工调试残留的亲和绑定
        await _wipe_own_keys(client)
    try:
        _build_redis_cluster_client(
            url + "/2", {"decode_responses": False}
        )
        check("bootstrap: URL 带库号被拒绝", False, "未抛 RedisUnavailable")
    except RedisUnavailable:
        check("bootstrap: URL 带库号被拒绝", True)

    # [0] 环境自证：旧式跨槽双键 EVAL 必炸
    # （客户端侧抛 RedisClusterException，服务端侧抛 ClusterCrossSlotError
    #   （ResponseError 子类）——两种都算命中）
    try:
        await client.eval(
            "return {KEYS[1], KEYS[2]}", 2, "verify:slot:a", "verify:slot:b"
        )
        check("环境自证: 旧式双键 EVAL 报跨槽错", False, "未报错——目标不是 cluster?")
    except (RedisClusterException, ResponseError):
        check("环境自证: 旧式双键 EVAL 报跨槽错", True)

    # [2] 选主协调器（日志里 job=rm_reclaim/rm_watch/sm_sweep/... 的故障点）
    coord = SingleLeaderCoordinator(
        client,
        lock_key="agent_runtime:job:sm_sweep",
        instance_id="cluster-verify-1",
        gather_window_sec=0.05,
    )
    token = await coord.try_claim(now=asyncio.get_event_loop().time(),
                                  instance_id="cluster-verify-1")
    check("选主: try_claim 拿到执行令牌", bool(token))
    if token:
        await coord.release(token)

    # [3] SM 状态层（日志里 POST /api/session/route 500 的故障点）
    sm = SessionState(client)
    now = now_ts()
    action, _ = await sm.route_place(
        session_id="v-s1", scope_id="v-scope", expiry_ts=now + 60,
        session_ttl=60, scope_concurrency=10, pod_concurrency=10,
        max_pods=2, now=now,
    )
    check("SM: 无 Pod 时 route_place=need_acquire", action == "need_acquire", action)
    await sm.register_pod("v-scope", "v-pod-1", "http://127.0.0.1:8086", "v1")
    action, pod = await sm.route_place(
        session_id="v-s1", scope_id="v-scope", expiry_ts=now + 60,
        session_ttl=60, scope_concurrency=10, pod_concurrency=10,
        max_pods=2, now=now,
    )
    check("SM: 注册后 route_place=placed", action == "placed" and pod == "v-pod-1",
          f"{action}/{pod}")
    touched, _ = await sm.touch("v-s1", now + 1)
    check("SM: touch 保活", touched is True)
    evicted = await sm.evict("v-s1")
    check("SM: evict 四处同删", evicted is not None
          and evicted.get("scope_id") == "v-scope")

    # [4] RM 状态层
    rm = ResourceState(client)
    await rm.save_scope_config("v-scope", {
        "scope_concurrency": 10, "pod_concurrency": 10, "max_pods": 2,
        "pod_ttl": 600, "min_idle_pods": 0,
    })
    await rm.save_scope_config("v-scope-2", {
        "scope_concurrency": 1, "pod_concurrency": 1, "max_pods": 1,
        "pod_ttl": 600, "min_idle_pods": 0,
    })
    action, _, _ = await rm.acquire("v-scope", "v-ver-1", "v-tok-1")
    check("RM: acquire=need_deploy(占位)", action in ("need_deploy", "reuse"),
          action)
    scopes = await rm.known_scope_ids()
    check("RM: known_scope_ids 扫出两个 scope(SCAN dict 游标)",
          {"v-scope", "v-scope-2"} <= set(scopes), str(scopes))

    # [4b] 代次日落(config_refresh,场景 M-R):REGISTER 烙印 + ACQUIRE 过滤
    await rm.register_pod(
        pod_id="v-pod-r1", scope_id="v-scope",
        pod_sse_url="http://127.0.0.1:8086", pod_ip="127.0.0.1",
        namespace="default", deploy_ver="v-ver-1", deploy_token="v-tok-1",
        idle_flag=True, now=now_ts(), sse_port=8086, health_path="/health",
    )
    action, pod, _ = await rm.acquire("v-scope", "v-ver-1", "v-tok-2")
    check("RM: 同版本同代暖 Pod 被 reuse(正向对照)", action == "reuse", action)
    await rm.release("v-pod-r1", "v-scope", now_ts())   # 放回 idle,保住对照前提
    gen = await rm.bump_generation("v-scope")
    check("RM: bump_generation HINCRBY 自增", gen == 1, str(gen))
    pod_gen = await client.hget(rm.k.pod_info("v-pod-r1"), "generation")
    check("RM: REGISTER 烙印注册当时代次(bump 前注册 → 空)",
          pod_gen in (b"", "", None), repr(pod_gen))
    await rm.register_pod(
        pod_id="v-pod-r2", scope_id="v-scope",
        pod_sse_url="http://127.0.0.1:8087", pod_ip="127.0.0.1",
        namespace="default", deploy_ver="v-ver-1", deploy_token="v-tok-2",
        idle_flag=True, now=now_ts(), sse_port=8086, health_path="/health",
    )
    pod2_gen = await client.hget(rm.k.pod_info("v-pod-r2"), "generation")
    check("RM: bump 后 REGISTER 烙新代(服务端读取)",
          pod2_gen in (b"1", "1"), repr(pod2_gen))
    # 老代 r1 被过滤、新代 r2 被选中(EVAL 内 HGET generation,跨槽安全)
    action, pod, _ = await rm.acquire("v-scope", "v-ver-1", "v-tok-3")
    check("RM: bump 后 acquire 跳过老代、选中新代暖 Pod",
          action == "reuse" and pod == "v-pod-r2", f"{action}/{pod}")

    # 收尾
    if wipe:
        await _wipe_own_keys(client)
    await client.aclose()

    failed = [r for r in _RESULTS if not r[1]]
    print(f"\n{'=' * 46}\nverify_redis_cluster: {len(_RESULTS) - len(failed)} passed, "
          f"{len(failed)} failed")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="redis+cluster://127.0.0.1:7001",
                        help="集群任一种子节点（redis+cluster:// scheme）")
    parser.add_argument("--wipe", action="store_true",
                        help="结束时清理写入的 SM/RM 键")
    args = parser.parse_args()
    sys.exit(asyncio.run(verify(args.url, args.wipe)))


if __name__ == "__main__":
    main()
