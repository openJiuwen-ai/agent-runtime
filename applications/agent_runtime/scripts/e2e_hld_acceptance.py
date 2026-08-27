# coding: utf-8
"""集成冒烟测试（M6 server 模式）：HLD §6 场景 A–L 端到端（真 Redis + MySQL + K8s）。

由 M6 验收用例整理而来，供以后每次部署/升级后回归。场景 N（半死探测）待
AgentServer 原生支持 GET /health 后补验（单测已覆盖）。

前置（全部可用 -- 参数或 AGENT_RUNTIME_E2E_* 环境变量覆盖）：
- agent-runtime 服务已以 server 模式运行（默认 http://127.0.0.1:8091）；
- Redis（默认 redis://127.0.0.1:30001/1，AOF/RDB 已开）；
- kubectl 已配置集群权限；验收 Pod 专用命名空间默认 agent-runtime-e2e；
- 验收镜像默认 influxdb:1.8（默认 :8086/health=200，满足 readiness/watch
  探测契约；AgentServer 支持 /health 后可换回真镜像）；
- mysql 客户端 + 只读权限（可选，仅校验配置落库；缺失自动 SKIP）。

用法（在 applications/agent_runtime 下）：
    uv run --no-sync python scripts/e2e_hld_acceptance.py [--参数]
    ./scripts/integration_smoke.sh                    # 等价包装（含前置自检）

场景 → 步骤映射：
  A 亲和续期 / B first-fit / C 扩 Pod   → 阶段 2（main scope 真实 deploy 2 个 Pod）
  M 配置热更新（B 类 + A 类）           → 阶段 3（pod_ttl 热更）/ 阶段 10（deploy 字段日落）
  H0 无请求预热（配置驱动）             → 阶段 1（零 Pod 基线）/ 阶段 1b（下发后即预热 min_idle）
  D 老化回收 / E 保活                   → 阶段 4（session_ttl 到期 → idle 暖池）
  K reclaim 自治                        → 阶段 5（回拨 idle_since，真删 K8s Pod）
  I acquire deploy 失败分支             → 阶段 6（不可拉镜像 → NO_POD_AVAILABLE + 占位清）
  F 容量满（队列 + 快失败/超时）        → 阶段 7（并发 5 请求 → 503 + 504）
  H min_idle 热备                       → 阶段 8（autoscale 预建热备 Pod）
  G 死 Pod 会话清洗 / J 死 Pod 探测     → 阶段 9（kubectl 删 Pod → watch 兜底 → 会话失效）
  N 半死探测                            → 【暂缓】待 AgentServer 原生支持 GET /health
  L 孤儿对账 + cleanup 运维端点         → 阶段 12（Redis↔K8s 一致性 + 批删）

注意：脚本会 FLUSHDB 目标 Redis DB（干净起点）。若 DB 中存在非
session_manager:/resource_manager: 前缀的 key，视为指错库，直接中止
（除非显式传 --force-flush）。请用独立的 DB 编号。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import time

import httpx
import redis.asyncio as aioredis

# 公共件抽到 e2e_lib（与 e2e_multi_replica.py 共享；改语义须两边同步）
from e2e_lib import (  # noqa: F401 (check/skip/envelope 供各 stage 直接用)
    RESULTS,
    Client,
    check,
    envelope,
    kubectl,
    redis_guard,
    skip,
    wait_until,
)
from e2e_lib import pod_exists as _lib_pod_exists

# 由 CLI 参数注入（默认值见 _parse_args；main() 里回填全局）
BASE = "http://127.0.0.1:8091/api/session"
REDIS_URL = "redis://127.0.0.1:30001/1"
NS = "agent-runtime-e2e"
IMAGE = "influxdb:1.8"            # 默认 :8086/health=200（readiness/watch 探测可通过）
DB_DSN = {                        # 仅阶段 1 落库校验用（可选）
    "host": "127.0.0.1", "port": "30000",
    "user": "agent_runtime", "password": "agent_runtime_pw", "name": "agent_runtime",
    "type": "mysql",              # mysql | postgresql（选择 mysql/psql 客户端）
}

MAIN = ""     # scope_id（main：cc=3 pc=2 → max_pods=2）
FSCOPE = ""   # （f：cc=2 pc=1 → max_pods=2，满 + 队列）
WARM = ""     # （warm：min_idle=1）
BAD = ""      # （bad：不可拉镜像，deploy 失败分支）


async def pod_exists(pod_id: str) -> bool:
    """薄委托（保留 NS 全局闭包，stage 调用点零改动）。"""
    return await _lib_pod_exists(pod_id, NS)


# ---------------------------------------------------------------- 前置自检

async def preflight(r: aioredis.Redis, force_flush: bool) -> bool:
    """服务/Redis/kubectl/命名空间/DB 归属自检；返回 False 则中止。"""
    ok = True

    async with httpx.AsyncClient(timeout=5.0) as probe:
        from urllib.parse import urlsplit
        root = urlsplit(BASE)
        docs_url = f"{root.scheme}://{root.netloc}/docs"
        try:
            resp = await probe.get(docs_url)
            ok &= check("前置：agent-runtime 服务在线", resp.status_code == 200,
                        f"{docs_url} → {resp.status_code}")
        except Exception as exc:
            ok &= check("前置：agent-runtime 服务在线", False, str(exc)[:120])

    try:
        await r.ping()
        info = await r.info("persistence")
        check("前置：Redis 可达且 AOF 已开", info.get("aof_enabled") == 1,
              f"aof_enabled={info.get('aof_enabled')}")
    except Exception as exc:
        ok &= check("前置：Redis 可达", False, str(exc)[:120])

    if shutil.which("kubectl") is None:
        ok &= check("前置：kubectl 可用", False)
    else:
        version = await kubectl("version", "--client", "--short")
        check("前置：kubectl 可用", "ersion" in version, version.strip()[:40])

    ns_out = await kubectl("get", "ns", NS, "-o", "name")
    if ns_out and "NotFound" not in ns_out:
        check(f"前置：命名空间 {NS} 存在", True)
    else:
        created = await kubectl("create", "namespace", NS)
        check(f"前置：命名空间 {NS} 已创建", "created" in created or "AlreadyExists" in created,
              created.strip()[:60])

    # 防误刷守卫（共享 e2e_lib.redis_guard，语义改动须同步 e2e_multi_replica）
    if not await redis_guard(r, force_flush):
        return False
    return ok


# ---------------------------------------------------------------- 模板

def template(**overrides) -> dict:
    base = {
        "agent_image": IMAGE,
        "namespace": NS,
        "sse_port": 8086,
        "sse_path": "/sse",
        "image_pull_policy": "IfNotPresent",
        "scope_concurrency": 3,
        "pod_concurrency": 2,
        "session_ttl": 30,
        "pod_ttl": 60,
        "min_idle_pods": 0,
        "ready_timeout": 240,
    }
    base.update(overrides)
    return base


TPL = {}
# (scope_id, template_id, routing_rules 表达式串)——scope 由 config_sync 下发;
# 不播种通配兜底,使「未知属性组合 → CONFIG_NOT_FOUND」可验收。
# e2e-main 故意带 or 用户白名单支:验收 and/or 任意组合的表达式路由(新 wire 格式)。
SCOPES_DEF = [
    ("e2e-main", "tpl-e2e", "group_id in ('e2e-main') or user_id in ('e2e-vip')"),
    ("e2e-f", "tpl-f", "group_id in ('e2e-f')"),
    ("e2e-warm", "tpl-warm", "group_id in ('e2e-warm')"),
    ("e2e-bad", "tpl-bad", "group_id in ('e2e-bad')"),
    ("e2e-nat", "tpl-nat", "group_id in ('e2e-nat')"),   # 自然老化专用(短 TTL,零回拨)
]


def full_sync_payload(tpl_overrides: dict | None = None) -> dict:
    """config_sync 全量载荷:模板集 + scope 集(routing_rules 表达式串)。

    tpl_overrides: {template_id: {字段: 新值}} —— B/A 类热更新阶段复用。
    """
    templates = [
        {"template_id": tid, **tpl, **(tpl_overrides or {}).get(tid, {})}
        for tid, tpl in TPL.items()
    ]
    scopes = [
        {"scope_id": sid, "index": i, "template_id": tid, "routing_rules": expr}
        for i, (sid, tid, expr) in enumerate(SCOPES_DEF)
    ]
    return {"templates": templates, "scopes": scopes}


def build_templates() -> None:
    TPL.clear()
    TPL.update({
        "tpl-e2e": template(),
        "tpl-f": template(scope_concurrency=2, pod_concurrency=1),
        "tpl-warm": template(scope_concurrency=2, pod_concurrency=1,
                             min_idle_pods=1, session_ttl=90),
        "tpl-bad": template(agent_image="agent-runtime-e2e-missing:1",
                            image_pull_policy="Always", ready_timeout=25),
        # 自然老化专用:短 TTL + min_idle=0(回收无保护)——阶段 5b 零回拨真等
        "tpl-nat": template(scope_concurrency=2, pod_concurrency=2,
                            session_ttl=15, pod_ttl=20, min_idle_pods=0),
    })


async def clean_previous(c: Client, r: aioredis.Redis) -> None:
    """清掉上一轮残留：Redis 编排态 + DB 配置表 + 验收命名空间的 Pod。"""
    await r.flushdb()
    if DB_DSN.get("type") == "postgresql" and shutil.which("psql") is not None:
        # PG：create 是裸 INSERT（唯一约束防重），重跑必须先清种子行
        await asyncio.create_subprocess_exec(
            "psql", f"-h{DB_DSN['host']}", f"-p{DB_DSN['port']}",
            f"-U{DB_DSN['user']}", "-d", DB_DSN["name"], "-c",
            "TRUNCATE service_config_template, routing_scope;",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "PGPASSWORD": DB_DSN["password"]})
    elif shutil.which("mysql") is not None:
        await asyncio.create_subprocess_exec(
            "mysql", f"-h{DB_DSN['host']}", f"-P{DB_DSN['port']}",
            f"-u{DB_DSN['user']}", f"-p{DB_DSN['password']}", "-e",
            f"USE {DB_DSN['name']}; "
            "SET FOREIGN_KEY_CHECKS=0; TRUNCATE service_config_template; "
            "TRUNCATE routing_scope; SET FOREIGN_KEY_CHECKS=1;",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    out = await kubectl("delete", "pod", "-n", NS, "-l",
                        "jiuwenclaw-component=agentserver", "--wait=false")
    print(f"-- 清理上一轮残留 Pod：{out.strip().splitlines()[-1][:80] if out.strip() else '无'}")


# ---------------------------------------------------------------- 阶段

async def stage1_seed(c: Client, r) -> None:
    print("\n== 阶段 1：config_sync 全量下发模板 + scope（含 DB 落库）==")
    # 前置观察：清场后（无任何配置）集群零 AgentServer Pod——配置驱动预热的基线
    async def no_pods() -> bool:
        out = await kubectl("get", "pods", "-n", NS, "-l",
                            "jiuwenclaw-component=agentserver", "--no-headers")
        return not out.strip() or "No resources found" in out
    check("H0-无配置时零 AgentServer Pod（不因服务启动而拉起）",
          await wait_until(no_pods, 30, 2))
    check("H0-无路由快照（配置未下发）",
          not await r.exists("session_manager:routing:snapshot"))

    code, raw, body = await c.post("config_sync", rawdata=full_sync_payload())
    check("config_sync 全量下发（5 模板 + 5 scope）",
          code == 200 and raw.get("ok") is True
          and raw.get("templates_synced") == 5 and raw.get("scopes_synced") == 5,
          json.dumps(body, ensure_ascii=False)[:200])
    snap = await r.get("session_manager:routing:snapshot")
    check("M-路由快照已写入 Redis（routing:snapshot）", bool(snap),
          f"len={len(snap or '')}")
    if DB_DSN.get("type") == "postgresql":
        if shutil.which("psql") is None:
            skip("DB(service_config_template/routing_scope) 落库", "psql 客户端不可用")
            return
        env = {**os.environ, "PGPASSWORD": DB_DSN["password"]}
        proc = await asyncio.create_subprocess_exec(
            "psql", f"-h{DB_DSN['host']}", f"-p{DB_DSN['port']}",
            f"-U{DB_DSN['user']}", "-d", DB_DSN["name"], "-t", "-A", "-c",
            "SELECT (SELECT COUNT(*) FROM service_config_template), "
            "(SELECT COUNT(*) FROM routing_scope);",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env)
        out, err = await proc.communicate()
        text = out.decode().strip()
        if not text:
            check("DB(service_config_template/routing_scope) 落库", False,
                  f"psql 空输出 rc={proc.returncode} err={err.decode()[:200]}")
            return
        counts = [int(x) for x in text.split("|")]
    else:
        if shutil.which("mysql") is None:
            skip("DB(service_config_template/routing_scope) 落库", "mysql 客户端不可用")
            return
        proc = await asyncio.create_subprocess_exec(
            "mysql", f"-h{DB_DSN['host']}", f"-P{DB_DSN['port']}",
            f"-u{DB_DSN['user']}", f"-p{DB_DSN['password']}", "-N", "-e",
            f"USE {DB_DSN['name']}; SELECT COUNT(*) FROM service_config_template; "
            "SELECT COUNT(*) FROM routing_scope;",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        counts = [int(x) for x in out.decode().split()]
    check("DB(service_config_template/routing_scope) 落库", counts == [5, 5], str(counts))


async def stage1b_warm_up_without_request(c: Client, r) -> None:
    print("\n== 阶段 1b：无请求预热（config_sync → autoscale 预备 min_idle 热备）==")
    # 此刻除播种外无任何 route；tpl-warm 的 scope（min_idle=1）应被 autoscale 预热
    async def warm_ready() -> bool:
        return await r.scard(f"resource_manager:resource:scope:{WARM}:idle") >= 1
    ok = await wait_until(warm_ready, 60, 2)
    idle = await r.smembers(f"resource_manager:resource:scope:{WARM}:idle")
    check("H0-config_sync 后零 route → autoscale 预热 min_idle=1 热备 Pod",
          ok and len(idle) == 1, str(idle))
    if idle:
        pod = next(iter(idle))
        check("H0-热备 Pod 真实存在于 K8s（配置驱动,无请求拉起）",
              await pod_exists(pod), pod)


async def stage2_route_abc(c: Client, r) -> dict:
    print("\n== 阶段 2：场景 A/B/C/E —— route 亲和 / first-fit / 扩 Pod / touch ==")
    state = {}
    t0 = time.monotonic()
    code, raw, body = await c.post("route", session_id="s1")
    ok = code == 200 and raw.get("pod_id", "").startswith("agentserver-")
    check("C-route s1 首会话真实 deploy Pod", ok,
          f"{code} {raw} ({time.monotonic()-t0:.0f}s)")
    if not ok:
        return state
    pod1 = raw["pod_id"]
    state["pod1"] = pod1
    check("C-新 Pod 存在于 K8s 且 Ready", await pod_exists(pod1), pod1)
    check("C-pod_sse_url 指向 Pod IP",
          raw.get("pod_sse_url", "").startswith("http://"), raw.get("pod_sse_url", ""))
    check("C-RM 池 ZCARD=1", await r.zcard(f"resource_manager:resource:scope:{MAIN}:pods") == 1)

    code, raw2, _ = await c.post("route", session_id="s1")
    check("A-同 session 再 route 返回原 Pod（零冷启动）",
          code == 200 and raw2["pod_id"] == pod1)
    check("A-SM scope:sessions 仍只有 1 个会话",
          await r.scard(f"session_manager:scope:{MAIN}:sessions") == 1)

    code, raw3, _ = await c.post("route", session_id="s2")
    check("B-s2 first-fit 打包进 pod1（2/2 满）",
          code == 200 and raw3["pod_id"] == pod1)
    check("B-per-Pod 容量闸门 SCARD=2",
          await r.scard(f"session_manager:pod:{MAIN}:{pod1}:sessions") == 2)

    t0 = time.monotonic()
    code, raw4, _ = await c.post("route", session_id="s3")
    ok = code == 200 and raw4["pod_id"] != pod1
    check("C-s3 触发扩 Pod（deploy pod2）", ok, f"{time.monotonic()-t0:.0f}s")
    if ok:
        state["pod2"] = raw4["pod_id"]
        check("C-SM 候选集 2 个 Pod（接入序）",
              await r.zcard(f"session_manager:scope:{MAIN}:pods") == 2)

    # E：touch 保活（远端到期时间被刷新）
    before = await r.zscore("session_manager:session_expiry", "s1")
    await asyncio.sleep(1.2)
    code, raw5, _ = await c.post("touch", session_id="s1")
    after = await r.zscore("session_manager:session_expiry", "s1")
    check("E-touch 保活刷新到期时间",
          code == 200 and raw5.get("touched") is True and after > before,
          f"{before:.0f} → {after:.0f}")
    code, raw6, _ = await c.post("touch", session_id="nope")
    check("E-touch 不存在会话 → touched=false",
          code == 200 and raw6.get("touched") is False)

    # 幂等回放
    env = envelope("route", session_id="s3", group="e2e-main")
    req_id = env["metadata"]["request_id"]
    _, first, _ = await c.post("route", session_id="s3", request_id=req_id)
    _, second, _ = await c.post("route", session_id="s3", request_id=req_id)
    check("route 幂等回放（同 request_id 同结果，不重抢额度）",
          first.get("pod_id") == second.get("pod_id")
          and await r.scard(f"session_manager:scope:{MAIN}:sessions") == 3)
    # 表达式 or 支:group 不在任何 scope,但 user 在 e2e-main 白名单 → 命中 MAIN
    code, raw_vip, _ = await c.post("route", session_id="s-vip",
                                    group="e2e-no-such-group", user="e2e-vip")
    check("route 表达式 or 支（user 白名单跨 group 命中 e2e-main）",
          code == 200 and raw_vip.get("pod_id"),
          f"{code} {str(raw_vip)[:80]}")
    return state


async def stage3_mb_hot_update(c: Client, r) -> None:
    print("\n== 阶段 3：场景 M（B 类）—— pod_ttl 热更新立即生效 ==")
    snap_before = await r.get("session_manager:routing:snapshot")
    code, raw, _ = await c.post("config_sync", rawdata=full_sync_payload(
        {"tpl-e2e": {"pod_ttl": 120}}))
    check("M-B config_sync 全量更新成功", code == 200 and raw.get("ok") is True)
    await asyncio.sleep(1)
    cfg = await r.hgetall(f"resource_manager:resource:scope:{MAIN}:config")
    check("M-B RM 池参数缓存立即刷新 pod_ttl=120（update_pool_config 推送）",
          cfg.get("pod_ttl") == "120", str({k: v for k, v in cfg.items()
                                            if k in ("pod_ttl", "max_pods")}))
    snap_after = await r.get("session_manager:routing:snapshot")
    check("M-B 路由快照已覆盖（下一次 route 即见新值）",
          bool(snap_after) and snap_after != snap_before)


async def stage4_aging(c: Client, r, state: dict) -> None:
    print("\n== 阶段 4：场景 D —— session_ttl 真实到期 → 老化回收 → idle 暖池 ==")
    # 回拨 s1..s3 到期时间到过去（加速；不真睡 TTL）
    past = time.time() - 5
    for sid in ("s1", "s2", "s3"):
        await r.zadd("session_manager:session_expiry", {sid: past})
        await r.hset(f"session_manager:session:{sid}", "expiry", int(past))

    async def drained() -> bool:
        return await r.scard(f"session_manager:scope:{MAIN}:sessions") == 0
    ok = await wait_until(drained, 30, 2, "sessions drained")
    check("D-到期 pass：scope:sessions 清空（sweeper 每 1s）", ok)
    sessions_left = [s for s in ("s1", "s2", "s3")
                     if await r.exists(f"session_manager:session:{s}")]
    check("D-会话四处全清", not sessions_left, str(sessions_left))
    idle = await r.smembers(f"resource_manager:resource:scope:{MAIN}:idle")
    check("D-空 Pod pass → idle_consider → RM idle 暖池 2 个",
          len(idle) == 2, str(idle))
    reg = await r.smembers("session_manager:pods:registered")
    check("D-不变量 5：pods:registered 仍持有（待 RM 回收后清）",
          len(reg) == 2, str(reg))
    phases = [await r.hget(f"resource_manager:resource:pod:{p}:info", "phase")
              for p in idle]
    check("D-Pod phase=idle", set(phases) == {"idle"}, str(phases))


async def stage5_reclaim(c: Client, r, state: dict) -> None:
    print("\n== 阶段 5：场景 K —— idle 超 pod_ttl → reclaim（真删 K8s Pod）==")
    pods = await r.smembers(f"resource_manager:resource:scope:{MAIN}:idle")
    if not pods:
        check("K-前置：存在 idle Pod", False, "无 idle Pod")
        return
    past = int(time.time()) - 121   # pod_ttl=120（阶段 3 已热更）；int 秒级（to_int 契约）
    for p in pods:
        await r.set(f"resource_manager:resource:pod:{p}:idle_since", past)

    async def reclaimed() -> bool:
        return await r.scard(f"resource_manager:resource:scope:{MAIN}:idle") == 0
    ok = await wait_until(reclaimed, 20, 2)
    check("K-reclaim（每 1s tick）清空 idle 池", ok)
    for p in pods:
        k8s_gone = not await pod_exists(p)
        purged = not await r.sismember("resource_manager:resource:pods:all", p)
        check(f"K-Pod {p[:30]}… K8s 已删 + RM PURGE", k8s_gone and purged,
              f"k8s_gone={k8s_gone} purged={purged}")
    reg = await r.smembers("session_manager:pods:registered")
    check("K-notify_pod_dead 已清 SM 注册", len(reg) == 0, str(reg))


async def stage5b_natural_drain(c: Client, r) -> None:
    """自然老化全链路(零回拨,真等 TTL)——2026-08-26 缺陷①(idle_since 被周期
    重放刷新,reclaim 永不触发)的回归网:阶段 4/5 的回拨加速跳过了「计时自然
    累积」这条路径,本阶段用短 TTL 模板(tpl-nat: session_ttl=15/pod_ttl=20/
    min_idle=0)不回拨走完 route→到期→idle→reclaim 全程。"""
    print("\n== 阶段 5b：自然老化(零回拨,真等 TTL)==")
    code, raw, _ = await c.post("route", session_id="nat1", group="e2e-nat")
    check("5b-nat1 首会话 deploy", code == 200 and raw.get("pod_id"), str(raw)[:120])
    if code != 200:
        return
    pod = raw["pod_id"]

    async def session_gone() -> bool:
        return not await r.exists("session_manager:session:nat1")
    ok = await wait_until(session_gone, 40, 2, "session 自然到期")
    check("5b-D 会话自然到期被 sweeper 回收(真等 session_ttl=15,未回拨)", ok)
    check("5b-D scope 活跃会话清空",
          await r.scard("session_manager:scope:e2e-nat:sessions") == 0)

    async def in_idle() -> bool:
        return pod in await r.smembers("resource_manager:resource:scope:e2e-nat:idle")
    ok = await wait_until(in_idle, 20, 2, "空 Pod 转 idle")
    check("5b-空 Pod pass → 转 idle 暖池", ok)
    since = await r.get(f"resource_manager:resource:pod:{pod}:idle_since")
    check("5b-idle_since 计时起点存在", bool(since), str(since))

    # min_idle=0 → 无保护;真等 pod_ttl=20 后 reclaim 必须触发(缺陷①在场则永不)
    async def reclaimed() -> bool:
        return pod not in await r.smembers("resource_manager:resource:pods:all")
    ok = await wait_until(reclaimed, 45, 2, "自然回收")
    k8s_gone = not await pod_exists(pod)
    check("5b-K idle 计时自然累积满 pod_ttl → reclaim(真删 K8s + PURGE)",
          ok and k8s_gone, f"purged={ok} k8s_gone={k8s_gone}")


async def stage6_deploy_failure(c: Client, r) -> None:
    print("\n== 阶段 6：场景 I —— deploy 失败分支（镜像不可拉）==")
    t0 = time.monotonic()
    code, raw, body = await c.post("route", session_id="b1", group="e2e-bad")
    took = time.monotonic() - t0
    check("I-deploy 失败 → 503 NO_POD_AVAILABLE",
          code == 503 and body.get("error_code") == "NO_POD_AVAILABLE",
          f"{code} {body.get('error_code')} ({took:.0f}s, ready_timeout=25)")
    check("I-红线：错误路径 deploying 占位已清",
          await r.scard(f"resource_manager:resource:scope:{BAD}:deploying") == 0)


async def stage7_queue(c: Client, r) -> None:
    print("\n== 阶段 7：场景 F —— 容量满：等待队列 + 快失败/超时 ==")
    for sid in ("f1", "f2"):                       # 2 Pod 全满（cc=2, pc=1, max=2）
        code, raw, _ = await c.post("route", session_id=sid, group="e2e-f")
        check(f"F-部署并占满 {sid}", code == 200 and raw.get("pod_id"), str(raw)[:120])
    t0 = time.monotonic()
    results = await asyncio.gather(*[
        c.post("route", session_id=f"f-over-{i}", group="e2e-f") for i in range(5)])
    codes = [code for code, _, _ in results]
    queue_full = [b for code, _, b in results if code == 503
                  and b.get("error_code") == "SCOPE_QUEUE_FULL"]
    full_timeout = [b for code, _, b in results if code == 504
                    and b.get("error_code") == "SCOPE_FULL_TIMEOUT"]
    took = time.monotonic() - t0
    check("F-队列满（max_waiters=2×cc=4）→ 快失败 503 SCOPE_QUEUE_FULL",
          len(queue_full) >= 1, f"codes={codes} ({took:.0f}s)")
    check("F-队列内等待 → 超时 504 SCOPE_FULL_TIMEOUT",
          len(full_timeout) >= 2, str([b.get("error_code") for _, _, b in results]))
    await asyncio.sleep(1)
    check("F-等待者全部出队（finally 清理）",
          await r.scard(f"session_manager:scope:{FSCOPE}:waiters") == 0)


async def stage8_warm(c: Client, r) -> dict:
    print("\n== 阶段 8：场景 H —— min_idle_pods 热备（autoscale 预建）==")
    state = {}
    code, raw, _ = await c.post("route", session_id="w1", group="e2e-warm")
    check("H-w1 首会话 deploy", code == 200 and raw.get("pod_id"), str(raw)[:120])
    if code != 200:
        return state
    state["w1_pod"] = raw["pod_id"]

    async def warm_ready() -> bool:
        return await r.scard(f"resource_manager:resource:scope:{WARM}:idle") >= 1
    ok = await wait_until(warm_ready, 30, 2)
    idle = await r.smembers(f"resource_manager:resource:scope:{WARM}:idle")
    check("H-autoscale（1s tick）补位热备 idle=1", ok and len(idle) == 1, str(idle))
    if idle:
        warm_pod = next(iter(idle))
        state["warm_pod"] = warm_pod
        check("H-热备 Pod 在 K8s 真实存在", await pod_exists(warm_pod), warm_pod)
    return state


async def stage9_dead_pod(c: Client, r, state: dict) -> None:
    print("\n== 阶段 9：场景 G/J —— kubectl 删在用 Pod → watch 兜底 → 会话清洗 ==")
    pod = state.get("w1_pod")
    if not pod:
        check("G-前置：w1 Pod 存在", False)
        return
    await c.post("touch", session_id="w1", group="e2e-warm")   # 保活，确保会话还在
    out = await kubectl("delete", "pod", "-n", NS, pod, "--wait=false")
    check("G-手动删除在用 Pod（模拟宿主机宕机）", "deleted" in out, out.strip()[:80])

    async def purged() -> bool:
        return not await r.sismember("resource_manager:resource:pods:all", pod)
    ok = await wait_until(purged, 40, 3, "watch purge")
    check("J-watch（10s tick）发现 NotFound → PURGE", ok)
    code, raw, _ = await c.post("touch", session_id="w1", group="e2e-warm")
    check("G-notify_pod_dead 清洗会话：touch w1 → touched=false",
          raw.get("touched") is False, str(raw))
    reg = await r.smembers("session_manager:pods:registered")
    check("G-SM 注册三处已清",
          all(not m.startswith(f"{WARM}:{pod}") for m in reg), str(reg))


async def stage10_ma_sunset(c: Client, r) -> None:
    print("\n== 阶段 10：场景 M（A 类）—— deploy 字段变更 → 软摘除 + 版本过滤 ==")
    cfg_key = f"resource_manager:resource:scope:{WARM}:config"
    ver_before = await r.hget(cfg_key, "deploy_ver")
    # A 类字段（readiness_period ∈ DEPLOY_VER_FIELDS）
    code, raw, _ = await c.post("config_sync", rawdata=full_sync_payload(
        {"tpl-warm": {"readiness_period": 7}}))
    check("M-A config_sync 全量更新（A 类）成功", code == 200 and raw.get("ok") is True)
    await asyncio.sleep(1)
    ver_after = await r.hget(cfg_key, "deploy_ver")
    check("M-A RM scope:config deploy_ver 已变（新 Pod 用新 deploy 字段）",
          ver_before and ver_after and ver_before != ver_after,
          f"{(ver_before or '')[:8]}… → {(ver_after or '')[:8]}…")
    # warm scope 候选集被 ZREM 软摘除（老 Pod 不接新流量，等自然回收）
    warm_pod = await r.smembers(f"resource_manager:resource:scope:{WARM}:idle")
    scores = [await r.zscore(f"session_manager:scope:{WARM}:pods", p) for p in warm_pod]
    zrem = all(score is None for score in scores)
    check("M-A SM 候选集 ZREM 软摘除（老 Pod 不接新流量）",
          not warm_pod or zrem, f"idle={warm_pod or '∅'}")


async def stage11_half_dead(c: Client, r, state: dict) -> None:
    print("\n== 阶段 11：场景 N —— 半死探测【暂缓】==")
    skip("场景 N（连续 2 次 /health 失败判半死）",
         "待 AgentServer 原生支持 GET /health 后补验"
         "（单测已覆盖：tests/resource_manager/test_rm_business.py）")


async def stage11b_invariants(c: Client, r) -> None:
    """内部不变量巡检——2026-08-26 缺陷②④⑤的回归网(在 cleanup 清场前执行):
    ② PURGE/重放 release 的 TOCTOU 幽灵 → idle ⊆ pods:all 且成员必有 idle_since;
    ④ fingerprint 键序敏感 → 快照模板 deploy_ver 必须与 RM cfg 一致(暖复用前提);
    ⑤ 停机取消泄漏占位 → 静息态 deploying 必须全空。"""
    print("\n== 阶段 11b:内部不变量巡检 ==")
    all_pods = await r.smembers("resource_manager:resource:pods:all")
    ghosts, missing_since, scopes = [], [], set()
    async for key in r.scan_iter(match="resource_manager:resource:scope:*:idle",
                                 count=100):
        scope = key.split(":")[3]
        scopes.add(scope)
        for pod in await r.smembers(key):
            if pod not in all_pods:
                ghosts.append(f"{scope}:{pod}")
            if not await r.get(f"resource_manager:resource:pod:{pod}:idle_since"):
                missing_since.append(f"{scope}:{pod}")
    check("IV-idle ⊆ pods:all(无幽灵成员,缺陷②网)", not ghosts, str(ghosts))
    check("IV-idle 成员必有 idle_since 计时", not missing_since, str(missing_since))

    leaks = []
    async for key in r.scan_iter(match="resource_manager:resource:scope:*:deploying",
                                 count=100):
        if await r.scard(key):
            leaks.append(key)
    check("IV-静息时 deploying 占位全空(缺陷⑤网)", not leaks, str(leaks))

    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from agent_runtime.resource_manager.orchestrator import _deploy_ver
    from agent_runtime.session_manager.routing import snapshot_from_json

    mismatch = []
    snap = snapshot_from_json(await r.get("session_manager:routing:snapshot"))
    for scope in sorted(scopes):
        cfg = await r.hgetall(f"resource_manager:resource:scope:{scope}:config")
        try:
            spec = json.loads(cfg.get("pod_spec_json") or "{}")
        except ValueError:
            spec = {}
        if spec and _deploy_ver(spec) != cfg.get("deploy_ver"):
            mismatch.append(f"{scope}:cfg 不自洽")
            continue
        snap_scope = next((s for s in snap.scopes if s.scope_id == scope), None)
        if (spec and snap_scope is not None
                and snap.templates[snap_scope.template_id].deploy_ver()
                != cfg.get("deploy_ver")):
            mismatch.append(f"{scope}:快照≠RM")
    check("IV-快照模板 deploy_ver == RM cfg(暖复用前提,缺陷④网)",
          not mismatch, str(mismatch))


async def stage12_reconcile_cleanup(c: Client, r) -> None:
    print("\n== 阶段 12：场景 L —— Redis↔K8s 一致性 + cleanup 运维端点 ==")
    # 一致性：RM 登记的每个 Pod 在 K8s 都存在（反向孤儿由 cleanup 收口）
    all_pods = await r.smembers("resource_manager:resource:pods:all")
    drift = []
    for p in all_pods:
        if not await pod_exists(p):
            drift.append(p)
    check("L-无漂移：RM pods:all 全部真实存在于 K8s", not drift, str(drift))

    code, raw, _ = await c.post("cleanup", rawdata={"namespace": NS})
    cleaned = raw.get("cleaned", -1)
    out = await kubectl("get", "pods", "-n", NS, "-l",
                        "jiuwenclaw-component=agentserver")
    check("cleanup 运维批删（含 deploy 失败遗留的孤儿 Pod）",
          code == 200 and cleaned >= 0 and ("No resources found" in out),
          f"cleaned={cleaned}; kubectl: {out.strip().splitlines()[-1][:60]}")
    await asyncio.sleep(12)   # 等 watch/reconcile 兜底清 Redis
    left = await r.smembers("resource_manager:resource:pods:all")
    check("L-watch/reconcile 兜底清空 Redis 编排态", not left, str(left))
    sm_keys = await r.keys("session_manager:pod:*")
    check("L-SM Pod 注册态全清", not sm_keys, str(sm_keys[:5]))


async def stage13_error_contract(c: Client, r) -> None:
    print("\n== 阶段 13：边界错误契约（真服务 HTTP 映射）==")
    code, raw, body = await c.post("route", session_id="s-norule", group="e2e-no-such-group")
    check("无匹配 scope（未播通配兜底）→ 503 CONFIG_NOT_FOUND（不可重试，无 retry_after）",
          code == 503 and body.get("error_code") == "CONFIG_NOT_FOUND"
          and "retry_after" not in body, f"{code} {body.get('error_code')}")
    code, raw, body = await c.post("route", session_id=None, group="e2e-main")
    check("route 缺 session_id → 400 VALIDATION",
          code == 400 and body.get("error_code") == "VALIDATION",
          f"{code} {body.get('error_code')}")
    code, raw, body = await c.post("route", session_id="s-nouser", group="e2e-main",
                                   user=None)
    check("route 缺 user_id → 400 VALIDATION",
          code == 400 and body.get("error_code") == "VALIDATION",
          f"{code} {body.get('error_code')}")
    code, raw, body = await c.post("touch", session_id=None)
    check("touch 空 session → 400 VALIDATION",
          code == 400 and body.get("error_code") == "VALIDATION")
    code, raw, body = await c.post("config_sync",
                                   rawdata={"kind": "nope", "op": "create"})
    check("config_sync 旧 kind/op 协议 → 400 VALIDATION",
          code == 400 and body.get("error_code") == "VALIDATION")
    # 空目标 = 匹配不到任何 Pod 的 label selector（确定性为 0）。三个坑的结论：
    # 1) 不存在的 ns：in-cluster SA 的 namespaced RBAC 返回 403 而非空列表
    #    （宿主机 admin 凭据才是空列表）——跨凭据形态行为不一；
    # 2) 业务 ns：同 label 的真实 AgentServer 会被误删（handoff §十一.6 教训）；
    # 3) 刚清空的验收 ns 也不行：min_idle 模板的 autoscale 1s 内就重建热备。
    code, raw, body = await c.post("cleanup", rawdata={
        "namespace": NS,
        "label_selector": "jiuwenclaw-component=agentserver-no-such"})
    check("cleanup 空目标（无匹配 selector）→ 200 cleaned=0",
          code == 200 and raw.get("cleaned") == 0, str(raw))


# ---------------------------------------------------------------- 入口

def _parse_args() -> argparse.Namespace:
    env = os.getenv
    parser = argparse.ArgumentParser(
        description="agent-runtime 集成冒烟测试（HLD 场景 A–L 端到端）")
    parser.add_argument("--base-url", default=env("AGENT_RUNTIME_E2E_BASE_URL",
                                                  "http://127.0.0.1:8091/api/session"))
    parser.add_argument("--redis-url", default=env("AGENT_RUNTIME_E2E_REDIS_URL",
                                                   "redis://127.0.0.1:30001/1"))
    parser.add_argument("--namespace", default=env("AGENT_RUNTIME_E2E_NAMESPACE",
                                                   "agent-runtime-e2e"))
    parser.add_argument("--image", default=env("AGENT_RUNTIME_E2E_IMAGE", "influxdb:1.8"))
    parser.add_argument("--db-host", default=env("AGENT_RUNTIME_E2E_DB_HOST", "127.0.0.1"))
    parser.add_argument("--db-port", default=env("AGENT_RUNTIME_E2E_DB_PORT", "30000"))
    parser.add_argument("--db-user", default=env("AGENT_RUNTIME_E2E_DB_USER", "agent_runtime"))
    parser.add_argument("--db-password", default=env("AGENT_RUNTIME_E2E_DB_PASSWORD",
                                                     "agent_runtime_pw"))
    parser.add_argument("--db-name", default=env("AGENT_RUNTIME_E2E_DB_NAME", "agent_runtime"))
    parser.add_argument("--db-type", default=env("AGENT_RUNTIME_E2E_DB_TYPE", "mysql"),
                        help="落库校验的客户端类型:mysql|postgresql")
    parser.add_argument("--force-flush", action="store_true",
                        help="目标 Redis DB 含外来 key 时仍强制 FLUSHDB（默认中止）")
    return parser.parse_args()


async def main() -> None:
    global BASE, REDIS_URL, NS, IMAGE, DB_DSN, MAIN, FSCOPE, WARM, BAD
    args = _parse_args()
    BASE = args.base_url.rstrip("/")
    REDIS_URL = args.redis_url
    NS = args.namespace
    IMAGE = args.image
    DB_DSN = {"host": args.db_host, "port": args.db_port, "user": args.db_user,
              "password": args.db_password, "name": args.db_name,
              "type": args.db_type}
    MAIN, FSCOPE = "e2e-main", "e2e-f"     # scope_id 由 config_sync 下发(字面量)
    WARM, BAD = "e2e-warm", "e2e-bad"
    build_templates()

    print(f"agent-runtime 集成冒烟测试 @ {time.strftime('%F %T')}")
    print(f"service={BASE} redis={REDIS_URL} ns={NS} image={IMAGE}")

    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        if not await preflight(r, args.force_flush):
            print("\n===== 前置自检未通过，中止 =====")
            raise SystemExit(2)
        async with httpx.AsyncClient(timeout=90.0) as http:
            c = Client(http, BASE)
            await clean_previous(c, r)

            await stage1_seed(c, r)
            await stage1b_warm_up_without_request(c, r)
            state = await stage2_route_abc(c, r)
            await stage3_mb_hot_update(c, r)
            await stage4_aging(c, r, state)
            await stage5_reclaim(c, r, state)
            await stage5b_natural_drain(c, r)
            await stage6_deploy_failure(c, r)
            await stage7_queue(c, r)
            state.update(await stage8_warm(c, r))
            await stage9_dead_pod(c, r, state)
            await stage10_ma_sunset(c, r)
            await stage11_half_dead(c, r, state)
            await stage11b_invariants(c, r)
            await stage12_reconcile_cleanup(c, r)
            await stage13_error_contract(c, r)
    finally:
        await r.aclose()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n===== 冒烟结果：{passed}/{len(RESULTS)} PASS =====")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    raise SystemExit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    asyncio.run(main())
