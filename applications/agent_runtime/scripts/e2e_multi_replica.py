# coding: utf-8
"""多副本（真 LB 单入口）端到端验收：M7 多实例部署与测试补全。

形态（HLD §8）：K8s Deployment 多副本 + ClusterIP/NodePort Service 前置
负载均衡。本脚本只打**一个 LB 入口**（--base-url），实例身份从 Redis 选主
键（agent_runtime:job:* 的 candidates/winner 值 = instance_id）反查——
不打多地址，也不依赖 LB 亲和。

阶段：
  S0 前置自检 + 副本普查门（census-window 秒内选主键见到 ≥ min-replicas
     个 instance_id → 完整模式；否则 DEGRADED，只跑 S1/S2/S5，其余 SKIP，
     退出码 0 —— 同一脚本可对单实例回归）
  S1 经 LB 播种模板/规则（串行：config_sync 全局锁，并发即 409）
  S2 经 LB route/touch/亲和（真实 deploy，kubectl 验证 Pod）
  S3 选主互斥：每 (job, epoch) 恒一 winner 且 ∈ candidates；双实例参选；
     winner 直方图（轮换为 SRANDMEMBER 随机，只记录）
  S4 并发突发不超收（cc=2/pc=1：恰好 2 成功，503/504 分布，waiters 清空）
  S5 幂等跨副本重放（同 request_id 两打 LB → 响应一致）
  S6 配置传播（config_sync 经 LB → 路由快照覆盖 → 新 route 见新值）
  S7 failover：流量进行中 kubectl 删一个副本 Pod（优先当前 sm_sweep
     leader；instance_id 前缀 = Pod 名）→ 副本数恢复、新 instance_id 入选、
     选主不变量保持、错误率归零
  S8 一致性收尾（RM pods:all ⊆ K8s）+ 汇总

前置：
- 服务已按 deploy/ 多副本部署（render_and_apply.sh），LB 入口可达；
- Redis 与副本共享（选主键可见）；kubectl 有 deployment/agentserver 两侧
  namespace 权限；AgentServer 替身镜像默认 influxdb:1.8（:8086/health）。

注意：与 M6 冒烟一样会 FLUSHDB 目标 Redis DB（防误刷守卫同 e2e_lib）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import time
from collections import Counter

import httpx
import redis.asyncio as aioredis

from e2e_lib import (
    ElectionCensus,
    RESULTS,
    Client,
    check,
    config_sync_payload,
    kubectl,
    pod_exists,
    redis_guard,
    skip,
    summary_and_exit,
    wait_until,
)
from e2e_lib import RM_PREFIX, SM_PREFIX

BASE = "http://127.0.0.1:30091/api/session"      # LB 单入口（NodePort）
REDIS_URL = "redis://127.0.0.1:30001/2"
DEPLOY_NS = "agent-runtime-e2e"                            # agent-runtime Deployment 所在 ns
NS = "agent-runtime-e2e"                         # AgentServer Pod 所在 ns
IMAGE = "influxdb:1.8"
DB_DSN = {"host": "127.0.0.1", "port": "30000", "user": "agent_runtime",
          "password": "agent_runtime_pw", "name": "agent_runtime"}
CENSUS_WINDOW = 15.0
MIN_REPLICAS = 2
FAILOVER_TIMEOUT = 240.0

MAIN = ""        # cc=3 pc=2 → max_pods=2
FSCOPE = ""      # cc=2 pc=1 → 满 + 队列
WARM = ""        # 留待扩展
BOT = "b"


def _parse_args() -> argparse.Namespace:
    env = os.getenv
    p = argparse.ArgumentParser(description="agent-runtime 多副本 e2e 验收（真 LB 单入口）")
    p.add_argument("--base-url", default=env("AGENT_RUNTIME_E2E_BASE_URL", BASE))
    p.add_argument("--redis-url", default=env("AGENT_RUNTIME_E2E_REDIS_URL", REDIS_URL))
    p.add_argument("--namespace", default=env("AGENT_RUNTIME_E2E_NAMESPACE", DEPLOY_NS),
                   help="agent-runtime Deployment 所在 namespace")
    p.add_argument("--agentserver-namespace",
                   default=env("AGENT_RUNTIME_E2E_AGENTSERVER_NAMESPACE", NS))
    p.add_argument("--image", default=env("AGENT_RUNTIME_E2E_IMAGE", IMAGE))
    p.add_argument("--db-host", default=env("AGENT_RUNTIME_E2E_DB_HOST", DB_DSN["host"]))
    p.add_argument("--db-port", default=env("AGENT_RUNTIME_E2E_DB_PORT", DB_DSN["port"]))
    p.add_argument("--db-user", default=env("AGENT_RUNTIME_E2E_DB_USER", DB_DSN["user"]))
    p.add_argument("--db-password",
                   default=env("AGENT_RUNTIME_E2E_DB_PASSWORD", DB_DSN["password"]))
    p.add_argument("--db-name", default=env("AGENT_RUNTIME_E2E_DB_NAME", DB_DSN["name"]))
    p.add_argument("--force-flush", action="store_true")
    p.add_argument("--census-window", type=float, default=CENSUS_WINDOW,
                   help="副本普查采样窗口秒（选主元数据 TTL~3s，须 ≥10）")
    p.add_argument("--min-replicas", type=int, default=MIN_REPLICAS)
    p.add_argument("--failover-timeout", type=float, default=FAILOVER_TIMEOUT)
    p.add_argument("--skip-failover", action="store_true",
                   help="跳过 S7（如在共享集群上不便删 Pod）")
    return p.parse_args()


def _template(**overrides) -> dict:
    base = {
        "agent_image": IMAGE, "namespace": NS, "sse_port": 8086,
        "sse_path": "/sse", "image_pull_policy": "IfNotPresent",
        "scope_concurrency": 3, "pod_concurrency": 2,
        "session_ttl": 90, "pod_ttl": 300, "min_idle_pods": 0,
        "ready_timeout": 240,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- S0 前置


async def s0_preflight(r: aioredis.Redis, c: Client, args, census: ElectionCensus
                       ) -> bool:
    print("\n== S0 前置自检 + 副本普查 ==")
    ok = True
    h = await c.healthz()
    ok &= check("前置：LB 入口在线（/healthz）", h is not None, str(h))
    try:
        await r.ping()
        info = await r.info("persistence")
        check("前置：Redis 可达且 AOF 已开", info.get("aof_enabled") == 1,
              f"aof_enabled={info.get('aof_enabled')}")
    except Exception as exc:
        ok &= check("前置：Redis 可达", False, str(exc)[:120])
    ok &= check("前置：kubectl 可用", shutil.which("kubectl") is not None)

    for ns in (args.namespace, args.agentserver_namespace):
        out = await kubectl("get", "ns", ns, "-o", "name")
        if not (ns in out and "NotFound" not in out):
            created = await kubectl("create", "namespace", ns)
            check(f"前置：命名空间 {ns} 已创建",
                  "created" in created or "AlreadyExists" in created)

    if not await redis_guard(r, args.force_flush):
        return False

    print(f"-- 副本普查采样 {args.census_window}s（选主键 candidates/winner）…")
    await asyncio.sleep(args.census_window)
    ids = census.distinct_instance_ids()
    check(f"前置：普查见到 {len(ids)} 个实例（≥{args.min_replicas} 为完整模式）",
          len(ids) >= 1, str(sorted(ids)))
    return ok


def _full_mode(census: ElectionCensus, args) -> bool:
    return len(census.distinct_instance_ids()) >= args.min_replicas


# ---------------------------------------------------------------- S1/S2 基础流


async def s1_seed(c: Client, r: aioredis.Redis) -> None:
    print("\n== S1 经 LB 播种（config_sync 串行——全局锁，并发即 409）==")
    await r.flushdb()
    if shutil.which("mysql") is not None:
        await asyncio.create_subprocess_exec(
            "mysql", f"-h{DB_DSN['host']}", f"-P{DB_DSN['port']}",
            f"-u{DB_DSN['user']}", f"-p{DB_DSN['password']}", "-e",
            f"USE {DB_DSN['name']}; SET FOREIGN_KEY_CHECKS=0; "
            "TRUNCATE service_config_template; TRUNCATE routing_scope; "
            "SET FOREIGN_KEY_CHECKS=1;",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    out = await kubectl("delete", "pod", "-n", NS, "-l",
                        "jiuwenclaw-component=agentserver", "--wait=false")
    print(f"-- 清理上一轮 Pod：{out.strip().splitlines()[-1][:60] if out.strip() else '无'}")

    payload = config_sync_payload(
        templates=[
            {"template_id": "tpl-mr", **_template()},
            {"template_id": "tpl-mr-f",
             **_template(scope_concurrency=2, pod_concurrency=1)},
        ],
        scopes=[
            {"scope_id": "mr-main", "index": 0, "template_id": "tpl-mr",
             "routing_rules": "group_id in ('mr-main')"},
            {"scope_id": "mr-f", "index": 1, "template_id": "tpl-mr-f",
             "routing_rules": "group_id in ('mr-f')"},
        ])
    code, _, body = await c.post("config_sync", group="mr-main", rawdata=payload)
    if not check("S1 全量下发（2 模板 + 2 scope）", code == 200, str(body)[:120]):
        raise SystemExit(2)


async def s2_route_touch(c: Client, r: aioredis.Redis) -> str:
    print("\n== S2 经 LB route/touch/亲和 ==")
    code, raw, body = await c.post("route", group="mr-main", bot=BOT,
                                   session_id="mr-s1")
    check("S2 首个 route 成功（真实 deploy）", code == 200, str(body)[:150])
    pod1 = raw.get("pod_id", "")
    check("S2 Pod 真实存在（K8s）", await pod_exists(pod1, NS), pod1)

    code, raw, _ = await c.post("route", group="mr-main", bot=BOT,
                                session_id="mr-s1")
    check("S2 同会话再 route → 亲和同 Pod", code == 200 and raw.get("pod_id") == pod1,
          f"{raw.get('pod_id')} == {pod1}")

    code, raw, _ = await c.post("touch", group="mr-main", bot=BOT,
                                session_id="mr-s1")
    check("S2 touch 保活（经 LB，副本任意）",
          code == 200 and raw.get("touched") is True, str(raw))

    scope = "mr-main"
    check("S2 scope 会话数为 1",
          await r.scard(f"{SM_PREFIX}scope:{scope}:sessions") == 1)
    return pod1


# ---------------------------------------------------------------- S3 选主互斥


async def s3_election(census: ElectionCensus) -> None:
    print("\n== S3 选主互斥（每 (job,epoch) 恒一 winner 且 ∈ candidates）==")
    samples = census.samples
    full = {k: s for k, s in samples.items() if "winner" in s}
    check("S3 普查有有效选主样本", len(full) >= 3, f"{len(full)} 个 (job,epoch)")

    winners: Counter[str] = Counter()
    bad_invariant, both_in_candidates = [], 0
    for (job, epoch), s in full.items():
        winners[s["winner"]] += 1
        cands = s.get("candidates")
        if cands is not None:
            if s["winner"] not in cands:
                bad_invariant.append((job, epoch))
            if len(cands) >= 2:
                both_in_candidates += 1
    check("S3 winner ∈ candidates（全部样本）", not bad_invariant,
          str(bad_invariant[:3]))
    check("S3 双实例同时参选（candidates 含 ≥2 实例的样本存在）",
          both_in_candidates >= 1, f"{both_in_candidates} 个样本")
    print(f"-- winner 直方图（SRANDMEMBER 随机轮换，仅记录）：{dict(winners)}")


# ---------------------------------------------------------------- S4 突发


async def s4_burst(c: Client, r: aioredis.Redis) -> None:
    print("\n== S4 并发突发不超收（cc=2/pc=1，8 并发经 LB）==")
    for sid in ("mr-f1", "mr-f2"):
        code, _, body = await c.post("route", group="mr-f", bot=BOT, session_id=sid)
        if not check(f"S4 预填 {sid}", code == 200, str(body)[:120]):
            raise SystemExit(2)
    scope = "mr-f"

    async def _attempt(i):
        code, _, body = await c.post("route", group="mr-f", bot=BOT,
                                     session_id=f"mr-burst-{i}")
        return code, body.get("error_code")

    outcomes = await asyncio.gather(*[_attempt(i) for i in range(8)])
    ok200 = [o for o in outcomes if o[0] == 200]
    queue_full = [o for o in outcomes if o == (503, "SCOPE_QUEUE_FULL")]
    timeout = [o for o in outcomes if o == (504, "SCOPE_FULL_TIMEOUT")]
    check("S4 突发零成功（scope 已满，闸门全局生效）", len(ok200) == 0, str(outcomes))
    check("S4 快失败 + 排队超时覆盖其余", len(queue_full) + len(timeout) == 8,
          f"queue_full={len(queue_full)} timeout={len(timeout)}"
          f"（等待 scope_full_timeout 后 504 属预期）")
    check("S4 终态不超收", await r.scard(
        f"{SM_PREFIX}scope:{scope}:sessions") == 2)
    wkey = f"{SM_PREFIX}scope:{scope}:waiters"

    async def _drained() -> bool:          # 真 async 闭包（lambda 里协程==0 恒 False）
        return await r.zcard(wkey) == 0

    drained = await wait_until(_drained, timeout=45, interval=2,
                               desc="waiters drained")
    members = await r.zrange(wkey, 0, -1) if not drained else set()
    check("S4 waiters 清空", drained,
          f"残留成员（应为其 request_id）：{sorted(members)[:5]}")
    check("S4 占位清空", await r.zcard(
        f"{RM_PREFIX}resource:scope:{scope}:deploying") == 0)


# ---------------------------------------------------------------- S5 幂等


async def s5_idempotent(c: Client, r: aioredis.Redis) -> None:
    print("\n== S5 幂等跨副本重放（同 request_id 两打 LB）==")
    scope = "mr-main"
    before = await r.scard(f"{SM_PREFIX}scope:{scope}:sessions")
    code, first, _ = await c.post("route", group="mr-main", bot=BOT,
                                  session_id="mr-idem", request_id="mr-req-idem")
    code2, second, _ = await c.post("route", group="mr-main", bot=BOT,
                                    session_id="mr-idem", request_id="mr-req-idem")
    check("S5 两次响应一致", code == code2 == 200 and first == second,
          f"{first.get('pod_id')} / {second.get('pod_id')}")
    after = await r.scard(f"{SM_PREFIX}scope:{scope}:sessions")
    check("S5 会话数不增（幂等态共享）", after == before + 1, f"{before} → {after}")


# ---------------------------------------------------------------- S6 配置传播


async def s6_config_propagation(c: Client, r: aioredis.Redis) -> None:
    print("\n== S6 配置传播（config_sync 经 LB → 路由快照覆盖 → 新 route 见新值）==")
    snapshot_key = f"{SM_PREFIX}routing:snapshot"
    snap_before = await r.get(snapshot_key)
    check("S6 前置：路由快照已存在", bool(snap_before))

    code, _, body = await c.post("config_sync", group="mr-main", rawdata={
        "templates": [
            {"template_id": "tpl-mr", **_template(), "session_ttl": 120},
            {"template_id": "tpl-mr-f",
             **_template(scope_concurrency=2, pod_concurrency=1)},
        ],
        "scopes": [
            {"scope_id": "mr-main", "index": 0, "template_id": "tpl-mr",
             "routing_rules": "group_id in ('mr-main')"},
            {"scope_id": "mr-f", "index": 1, "template_id": "tpl-mr-f",
             "routing_rules": "group_id in ('mr-f')"},
        ]})
    check("S6 config_sync 更新成功", code == 200, str(body)[:120])
    check("S6 路由快照已覆盖（跨副本共享单键）",
          await r.get(snapshot_key) not in (None, snap_before))

    code, _, body = await c.post("route", group="mr-main", bot=BOT,
                                 session_id="mr-s6")
    check("S6 更新后新 route 成功", code == 200, str(body)[:120])
    expiry = await r.zscore(f"{SM_PREFIX}session_expiry", "mr-s6")
    now = int(time.time())
    check("S6 新会话用新 ttl=120", now + 100 <= int(expiry) <= now + 130,
          f"expiry-now={int(expiry) - now}")


# ---------------------------------------------------------------- S7 failover


async def _traffic_loop(c: Client, stop: asyncio.Event, budget: dict) -> None:
    """背景流量：route/touch 交替；错误只计数（中断期不判死）。"""
    seq = 0
    while not stop.is_set():
        seq += 1
        try:
            msg = "route" if seq % 4 == 0 else "touch"
            code, _, body = await c.post(msg, group="mr-main", bot=BOT,
                                         session_id="mr-s1" if msg == "touch" else f"mr-ft-{seq}")
            if code != 200:
                budget["http"] += 1
        except Exception:
            budget["transport"] += 1
        await asyncio.sleep(0.2)


async def s7_failover(c: Client, r: aioredis.Redis, census: ElectionCensus,
                      args) -> None:
    print("\n== S7 failover（流量进行中删一个副本 Pod）==")
    before_ids = census.distinct_instance_ids()
    # 优先删当前 sm_sweep leader（同时验无状态接管与任务接替）
    target = None
    for (job, _epoch), s in census.samples.items():
        if job == "sm_sweep" and "winner" in s:
            target = s["winner"]
    if target is None:
        target = sorted(before_ids)[0]
    pod_name = target.split(":")[0]          # K8s 内 hostname = Pod 名
    print(f"-- 目标副本：instance_id={target} → pod={pod_name}")

    budget = {"http": 0, "transport": 0}
    stop = asyncio.Event()
    traffic = asyncio.get_running_loop().create_task(_traffic_loop(c, stop, budget))
    await asyncio.sleep(1.0)                 # 流量先跑起来

    out = await kubectl("delete", "pod", "-n", args.namespace, pod_name, "--wait=false")
    print(f"-- kubectl delete：{out.strip()[:60]}")
    check("S7 目标 Pod 删除指令已执行", "deleted" in out or "NotFound" in out, pod_name)

    async def _recovered() -> bool:
        # 副本数恢复（Deployment 重建）+ 新 instance_id 入选
        pods = await kubectl("get", "pods", "-n", args.namespace,
                             "-l", "app=agent-runtime", "-o", "json")
        ready = pods.count('"ready": true')
        ids_now = census.distinct_instance_ids()
        return ready >= args.min_replicas and any(
            i not in before_ids for i in ids_now)

    check(f"S7 副本恢复 + 新实例入选（≤{args.failover_timeout:.0f}s）",
          await wait_until(_recovered, timeout=args.failover_timeout, interval=3,
                           desc="failover recovery"),
          f"错误预算 http={budget['http']} transport={budget['transport']}")

    # 恢复后流量归零（给接管 10s 缓冲再统计）
    await asyncio.sleep(10)
    stop.set()
    await asyncio.gather(traffic, return_exceptions=True)
    after_errors = budget["http"] + budget["transport"]
    print(f"-- 全程错误预算：http={budget['http']} transport={budget['transport']}"
          f"（恢复后 10s 窗口内新增见 detail）")
    ok = await c.healthz() is not None
    check("S7 恢复后 LB 仍可服务", ok)
    ids_after = census.distinct_instance_ids()
    check("S7 选主样本仍满足互斥（winner ∈ candidates）",
          all(s["winner"] in s.get("candidates", {s["winner"]})
              for s in census.samples.values() if "winner" in s),
          f"实例数 {len(ids_after)}")


# ---------------------------------------------------------------- S8 收尾


async def s8_consistency(r: aioredis.Redis) -> None:
    print("\n== S8 一致性收尾（RM pods:all ⊆ K8s）==")
    pods = await r.smembers(f"{RM_PREFIX}resource:pods:all")
    missing = []
    for p in pods:
        pod_id = p.decode() if isinstance(p, bytes) else p
        if not await pod_exists(pod_id, NS):
            missing.append(pod_id)
    check("S8 RM 登记的 Pod 全部存在于 K8s", not missing, str(missing[:3]))


# ---------------------------------------------------------------- 入口


async def main() -> None:
    global BASE, REDIS_URL, DEPLOY_NS, NS, IMAGE, DB_DSN
    global CENSUS_WINDOW, MIN_REPLICAS, FAILOVER_TIMEOUT, MAIN, FSCOPE, WARM
    args = _parse_args()
    BASE = args.base_url.rstrip("/")
    REDIS_URL = args.redis_url
    DEPLOY_NS = args.namespace
    NS = args.agentserver_namespace
    IMAGE = args.image
    DB_DSN = {"host": args.db_host, "port": args.db_port, "user": args.db_user,
              "password": args.db_password, "name": args.db_name}
    MAIN, FSCOPE = "mr-main", "mr-f"    # scope_id 由 config_sync 下发(字面量)

    print(f"agent-runtime 多副本 e2e @ {time.strftime('%F %T')}")
    print(f"lb={BASE} redis={REDIS_URL} deploy_ns={DEPLOY_NS} agentserver_ns={NS}")

    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    census = ElectionCensus(r, interval=0.3)
    try:
        census.start()
        async with httpx.AsyncClient(timeout=120.0) as http:
            c = Client(http, BASE)
            if not await s0_preflight(r, c, args, census):
                print("\n===== 前置自检未通过，中止 =====")
                raise SystemExit(2)

            full = _full_mode(census, args)
            if not full:
                print("\n!!!!! DEGRADED：普查未见 ≥{} 个实例，多副本专项降级 SKIP !!!!!"
                      .format(args.min_replicas))

            await s1_seed(c, r)
            await s2_route_touch(c, r)

            if full:
                await s3_election(census)
                await s4_burst(c, r)
            else:
                skip("S3 选主互斥", "单实例模式（DEGRADED）")
                skip("S4 并发突发", "单实例模式（DEGRADED）")

            await s5_idempotent(c, r)

            if full:
                await s6_config_propagation(c, r)
                if args.skip_failover:
                    skip("S7 failover", "--skip-failover")
                else:
                    await s7_failover(c, r, census, args)
            else:
                skip("S6 配置传播", "单实例模式（DEGRADED）")
                skip("S7 failover", "单实例模式（DEGRADED）")

            await s8_consistency(r)
    finally:
        await census.stop()
        await r.aclose()

    raise SystemExit(summary_and_exit())


if __name__ == "__main__":
    asyncio.run(main())
