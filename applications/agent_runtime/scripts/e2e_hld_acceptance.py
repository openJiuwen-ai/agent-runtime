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
    scope_id,
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
RULES = [
    ("rule-main", "e2e-main", "tpl-e2e"),
    ("rule-f", "e2e-f", "tpl-f"),
    ("rule-warm", "e2e-warm", "tpl-warm"),
    ("rule-bad", "e2e-bad", "tpl-bad"),
]


def build_templates() -> None:
    TPL.clear()
    TPL.update({
        "tpl-e2e": template(),
        "tpl-f": template(scope_concurrency=2, pod_concurrency=1),
        "tpl-warm": template(scope_concurrency=2, pod_concurrency=1,
                             min_idle_pods=1, session_ttl=90),
        "tpl-bad": template(agent_image="agent-runtime-e2e-missing:1",
                            image_pull_policy="Always", ready_timeout=25),
    })


async def clean_previous(c: Client, r: aioredis.Redis) -> None:
    """清掉上一轮残留：Redis 编排态 + DB 配置表 + 验收命名空间的 Pod。"""
    await r.flushdb()
    if DB_DSN.get("type") == "postgresql" and shutil.which("psql") is not None:
        # PG：create 是裸 INSERT（唯一约束防重），重跑必须先清种子行
        await asyncio.create_subprocess_exec(
            "psql", f"-h{DB_DSN['host']}", f"-p{DB_DSN['port']}",
            f"-U{DB_DSN['user']}", "-d", DB_DSN["name"], "-c",
            "TRUNCATE service_config_template, routing_rule;",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "PGPASSWORD": DB_DSN["password"]})
    elif shutil.which("mysql") is not None:
        await asyncio.create_subprocess_exec(
            "mysql", f"-h{DB_DSN['host']}", f"-P{DB_DSN['port']}",
            f"-u{DB_DSN['user']}", f"-p{DB_DSN['password']}", "-e",
            f"USE {DB_DSN['name']}; "
            "SET FOREIGN_KEY_CHECKS=0; TRUNCATE service_config_template; "
            "TRUNCATE routing_rule; SET FOREIGN_KEY_CHECKS=1;",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    out = await kubectl("delete", "pod", "-n", NS, "-l",
                        "jiuwenclaw-component=agentserver", "--wait=false")
    print(f"-- 清理上一轮残留 Pod：{out.strip().splitlines()[-1][:80] if out.strip() else '无'}")


# ---------------------------------------------------------------- 阶段

async def stage1_seed(c: Client, r) -> None:
    print("\n== 阶段 1：config_sync 下发模板 + 路由规则（含 DB 落库）==")
    for tpl_id, tpl in TPL.items():
        code, raw, body = await c.post("config_sync", rawdata={
            "kind": "template", "op": "create",
            "template_id": tpl_id, "template": tpl})
        check(f"config_sync create {tpl_id}", code == 200 and raw.get("ok") is True,
              json.dumps(body, ensure_ascii=False)[:200])
    for rule_id, group, tpl_id in RULES:
        code, raw, body = await c.post("config_sync", rawdata={
            "kind": "routing_rule", "op": "create", "rule_id": rule_id,
            "group_id": group, "bot_id": "b", "template_id": tpl_id})
        check(f"config_sync rule {rule_id}", code == 200 and raw.get("ok") is True)
    if DB_DSN.get("type") == "postgresql":
        if shutil.which("psql") is None:
            skip("DB(service_config_template/routing_rule) 落库", "psql 客户端不可用")
            return
        env = {**os.environ, "PGPASSWORD": DB_DSN["password"]}
        proc = await asyncio.create_subprocess_exec(
            "psql", f"-h{DB_DSN['host']}", f"-p{DB_DSN['port']}",
            f"-U{DB_DSN['user']}", "-d", DB_DSN["name"], "-t", "-A", "-c",
            "SELECT (SELECT COUNT(*) FROM service_config_template), "
            "(SELECT COUNT(*) FROM routing_rule);",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=env)
        out, _ = await proc.communicate()
        counts = [int(x) for x in out.decode().strip().split("|")]
    else:
        if shutil.which("mysql") is None:
            skip("DB(service_config_template/routing_rule) 落库", "mysql 客户端不可用")
            return
        proc = await asyncio.create_subprocess_exec(
            "mysql", f"-h{DB_DSN['host']}", f"-P{DB_DSN['port']}",
            f"-u{DB_DSN['user']}", f"-p{DB_DSN['password']}", "-N", "-e",
            f"USE {DB_DSN['name']}; SELECT COUNT(*) FROM service_config_template; "
            "SELECT COUNT(*) FROM routing_rule;",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        counts = [int(x) for x in out.decode().split()]
    check("DB(service_config_template/routing_rule) 落库", counts == [4, 4], str(counts))


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
    return state


async def stage3_mb_hot_update(c: Client, r) -> None:
    print("\n== 阶段 3：场景 M（B 类）—— pod_ttl 热更新立即生效 ==")
    code, raw, _ = await c.post("config_sync", rawdata={
        "kind": "template", "op": "update", "template_id": "tpl-e2e",
        "updates": {"pod_ttl": 120}})
    check("M-B config_sync update 成功", code == 200 and raw.get("ok") is True)
    await asyncio.sleep(1)
    cfg = await r.hgetall(f"resource_manager:resource:scope:{MAIN}:config")
    check("M-B RM 池参数缓存立即刷新 pod_ttl=120（update_pool_config 推送）",
          cfg.get("pod_ttl") == "120", str({k: v for k, v in cfg.items()
                                            if k in ("pod_ttl", "max_pods")}))
    sm_cfg_exists = await r.exists(f"session_manager:scope:{MAIN}:config")
    check("M-B SM resolve 缓存已失效（DEL，下一次 route 重新 resolve）",
          not sm_cfg_exists, f"exists={sm_cfg_exists}")


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
    code, raw, _ = await c.post("config_sync", rawdata={
        "kind": "template", "op": "update", "template_id": "tpl-warm",
        "updates": {"readiness_period": 7}})
    check("M-A config_sync update（A 类）成功", code == 200 and raw.get("ok") is True)
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
    check("无匹配路由规则 → 503 CONFIG_NOT_FOUND（不可重试，无 retry_after）",
          code == 503 and body.get("error_code") == "CONFIG_NOT_FOUND"
          and "retry_after" not in body, f"{code} {body.get('error_code')}")
    code, raw, body = await c.post("route", session_id=None, group="e2e-main")
    check("route 缺 session_id → 400 VALIDATION",
          code == 400 and body.get("error_code") == "VALIDATION",
          f"{code} {body.get('error_code')}")
    code, raw, body = await c.post("touch", session_id=None)
    check("touch 空 session → 400 VALIDATION",
          code == 400 and body.get("error_code") == "VALIDATION")
    code, raw, body = await c.post("config_sync",
                                   rawdata={"kind": "nope", "op": "create"})
    check("config_sync 未知 kind → 400 VALIDATION",
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
    MAIN, FSCOPE = scope_id("e2e-main", "b"), scope_id("e2e-f", "b")
    WARM, BAD = scope_id("e2e-warm", "b"), scope_id("e2e-bad", "b")
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
            state = await stage2_route_abc(c, r)
            await stage3_mb_hot_update(c, r)
            await stage4_aging(c, r, state)
            await stage5_reclaim(c, r, state)
            await stage6_deploy_failure(c, r)
            await stage7_queue(c, r)
            state.update(await stage8_warm(c, r))
            await stage9_dead_pod(c, r, state)
            await stage10_ma_sunset(c, r)
            await stage11_half_dead(c, r, state)
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
