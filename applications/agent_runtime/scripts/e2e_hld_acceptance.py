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
- mysql 客户端 + 只读权限（可选，仅校验配置落库；缺失自动 SKIP）；
- --with-mounts（全量真实规格阶段，默认关）：kubectl 凭据需可创建 namespace
  级 ConfigMap/PVC 与 cluster 级 PV（hostPath 静态供给），节点需接受 apparmor
  annotation（运行时未启用 AppArmor 时 Pod 创建即失败——如实暴露）。

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
BOX = ""      # （box：sidecar 多容器；--with-sidecar 时启用）
WITH_SIDECAR = False
SIDECAR_IMAGE = ""   # sidecar 替身镜像（默认与主镜像同款 influxdb:1.8 改端口）
MNT = ""      # （mnt：主+sidecar 三种挂载全量真实规格；--with-mounts 时启用）
WITH_MOUNTS = False
# 契约参数（真镜像门禁三件套：health_path / sse_path / agent_env）
HEALTH_PATH = "/health"
SSE_PATH = "/sse"
AGENT_ENV = None


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
        "sse_path": SSE_PATH,
        # 探测/readiness 契约参数(与 RM probe 同源;真 AgentServer 为
        # /api/v1/health——替身 influxdb 沿用默认 /health,发布门禁跑真镜像时
        # 必须显式传 --health-path/--sse-path/--agent-env,否则 readiness 永不
        # 通过,阶段 2 起全红)
        "health_path": HEALTH_PATH,
        "image_pull_policy": "IfNotPresent",
        "scope_concurrency": 3,
        "pod_concurrency": 2,
        "session_ttl": 30,
        "pod_ttl": 60,
        "min_idle_pods": 0,
        "ready_timeout": 240,
    }
    if AGENT_ENV:
        base["agent_env"] = AGENT_ENV
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

# sidecar 替身（--with-sidecar）：influxdb:1.8 改绑 8096——主容器已占 8086，
# 同 Pod 共享网络命名空间必须错开端口；**RPC 端口 8088 也必须错开**（influxdb
# 双实例同 Pod 会抢 127.0.0.1:8088 → sidecar CrashLoop，2026-08-27 真环境实测）；
# TCP readiness 真实可过（8096 监听即 Ready）。非特权无挂载：验证多容器渲染 +
# readiness 门控。
# --sidecar-image 指定真 jiuwenbox 镜像时切完整 jiuwenbox 规格（特权四件套 +
# cgroup hostPath + 8321）。注意 JIUWENBOX_LISTEN 必须 http:// scheme——0.0.6s
# 实测 tcp:// 被拒（"expected 'http' or 'unix'"，EE 旧默认 tcp:// 系旧版行为）。
SIDECAR_STANDIN_PORT = 8096


def _sidecar_standin() -> dict:
    common = {
        "name": "box-standin",
        # ConfigMap subPath 单 key 挂载(老 SDK config.yaml 同款形态):
        # 阶段 2b 先 kubectl create configmap,exec 验证容器内文件内容
        "configmap_mounts": [{
            "config_map_name": "e2e-box-cm",
            "mount_path": "/etc/box/policy.yaml",
            "sub_path": "policy.yaml",
        }],
        "readiness_probe_type": "tcp",
        "readiness_initial_delay": 5,
        "readiness_period": 5,
    }
    if SIDECAR_IMAGE != IMAGE:   # 真 jiuwenbox 镜像:完整规格
        return {**common,
                "image": SIDECAR_IMAGE,
                "port": 8321,
                "env": {"JIUWENBOX_LISTEN": "http://0.0.0.0:8321"},
                "privileged": True,
                "capabilities_add": ["SYS_ADMIN", "NET_ADMIN"],
                "seccomp_unconfined": True,
                "apparmor_unconfined": True,
                "host_path_mounts": [{
                    "host_path": "/sys/fs/cgroup",
                    "mount_path": "/sys/fs/cgroup",
                    "host_path_type": "Directory",
                }],
                }
    return {**common,           # influxdb 替身:错开 8086 与 RPC 8088
            "image": SIDECAR_IMAGE,
            "port": SIDECAR_STANDIN_PORT,
            "env": {
                "INFLUXDB_HTTP_BIND_ADDRESS": f":{SIDECAR_STANDIN_PORT}",
                "INFLUXDB_BIND_ADDRESS": ":8098",
            }}


# ---- 全量真实规格(--with-mounts)资源名/路径约定:对齐真实 config_sync 请求的
# 引用名(agent-config-cm/box-policy-cm/agent-data-pvc/box-data-pvc)。CM 与静态
# PV/PVC 由 stage0 预置;宿主 /mnt/host-test 由 kubelet DirectoryOrCreate 自建。
MNT_AGENT_CM = "agent-config-cm"
MNT_BOX_CM = "box-policy-cm"
MNT_AGENT_PVC = "agent-data-pvc"
MNT_BOX_PVC = "box-data-pvc"
MNT_AGENT_PV = "e2e-agent-data-pv"
MNT_BOX_PV = "e2e-box-data-pv"
MNT_HOST_PATH = "/mnt/host-test"


def _jiuwenbox_spec() -> dict:
    """tpl-mnt 的 sidecar:完整 jiuwenbox 规格(三种挂载 + 特权四件套)。

    与 _sidecar_standin 的关键差异:不引用 --with-sidecar 门控的 e2e-box-cm
    (该 CM 只在 stage2b 创建,--with-mounts 单独开时缺失 →
    CreateContainerConfigError 永不 Ready),改用 stage0 无条件预置的
    box-policy-cm。特权四件套替身模式也带——挂载与 securityContext 渲染
    不依赖镜像,默认替身跑即可断言;真镜像门禁验证真契约。
    """
    real = SIDECAR_IMAGE != IMAGE
    return {
        "name": "jiuwenbox",          # 与真实请求一致;≠ agent,过容器名冲突校验
        "image": SIDECAR_IMAGE,
        "port": 8321 if real else SIDECAR_STANDIN_PORT,
        "env": ({"JIUWENBOX_LISTEN": "http://0.0.0.0:8321"} if real
                else {"INFLUXDB_HTTP_BIND_ADDRESS": f":{SIDECAR_STANDIN_PORT}",
                      "INFLUXDB_BIND_ADDRESS": ":8098"}),
        "privileged": True,
        "capabilities_add": ["SYS_ADMIN", "NET_ADMIN"],
        "seccomp_unconfined": True,
        "apparmor_unconfined": True,
        "configmap_mounts": [{"config_map_name": MNT_BOX_CM,
                              "mount_path": "/etc/box/policy.yaml",
                              "sub_path": "policy.yaml"}],
        "host_path_mounts": [{"host_path": "/sys/fs/cgroup",
                              "mount_path": "/sys/fs/cgroup",
                              "host_path_type": "Directory"}],
        "pvc_mounts": [{"claim_name": MNT_BOX_PVC,
                        "mount_path": "/var/lib/jiuwenbox"}],
        "readiness_probe_type": "tcp",
        "readiness_initial_delay": 5,
        "readiness_period": 5,
    }


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
    if WITH_SIDECAR:
        # pod_ttl=3600:box Pod 全程长存——否则可能在阶段 5~12 之间被自然回收,
        # 使 D-不变量5 / K-notify 的注册表计数随时序漂移(2026-08-27 实测)
        TPL["tpl-box"] = template(scope_concurrency=2, pod_concurrency=1,
                                  pod_ttl=3600,
                                  sidecars=[_sidecar_standin()])
        # 幂等:build_templates 可能被热更新阶段再次调用
        if not any(sid == "e2e-box" for sid, _, _ in SCOPES_DEF):
            SCOPES_DEF.append(("e2e-box", "tpl-box", "group_id in ('e2e-box')"))
    if WITH_MOUNTS:
        # 全量真实规格:对齐真实 config_sync 请求(主容器 cm/hp/pvc 三挂载 +
        # 显式 container_port/readiness 参数 + sidecar jiuwenbox 完整规格)。
        # 全部走 overrides,**绝不进 template() base**——否则全部模板
        # deploy_ver 变化 → 全量 A 类日落,stage3 直接 409。
        # min_idle=1:stage1 下发后 autoscale(1s tick)即预热(暖 Pod 不写 SM
        # pods:registered,2c route 前对其余阶段不可见);pod_ttl=3600 全程
        # 长存,与 tpl-box 同款(跨 scope 计数断言 +1 处理)。
        TPL["tpl-mnt"] = template(
            template_name="主+sidecar 双镜像·三种挂载全量样例",
            pod_name="agentserver-mnt", container_name="agent",
            container_port=8086,             # 显式下发(=sse_port;此前从未覆盖)
            scope_concurrency=3, pod_concurrency=2,
            session_ttl=60, pod_ttl=3600, min_idle_pods=1, ready_timeout=240,
            readiness_initial_delay=5, readiness_period=5,
            agent_configmap_mounts=[{
                "config_map_name": MNT_AGENT_CM,
                "mount_path": "/etc/agent/config.yaml",
                "sub_path": "config.yaml"}],
            agent_host_path_mounts=[{
                "host_path": MNT_HOST_PATH, "mount_path": MNT_HOST_PATH,
                "host_path_type": "DirectoryOrCreate", "read_only": True}],
            agent_pvc_mounts=[{
                "claim_name": MNT_AGENT_PVC, "mount_path": "/var/lib/agent"}],
            sidecars=[_jiuwenbox_spec()])
        # 幂等 + 表达式非通配:SCOPES_DEF 故意不播通配,保住 stage13 的
        # CONFIG_NOT_FOUND 验收(真实请求的空串通配形态不可照搬)
        if not any(sid == MNT or sid == "e2e-mnt" for sid, _, _ in SCOPES_DEF):
            SCOPES_DEF.append(("e2e-mnt", "tpl-mnt", "group_id in ('e2e-mnt')"))


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
    n_tpl, n_scope = len(TPL), len(SCOPES_DEF)
    check(f"config_sync 全量下发（{n_tpl} 模板 + {n_scope} scope）",
          code == 200 and raw.get("ok") is True
          and raw.get("templates_synced") == n_tpl
          and raw.get("scopes_synced") == n_scope,
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
    check("DB(service_config_template/routing_scope) 落库",
          counts == [len(TPL), len(SCOPES_DEF)], str(counts))


async def stage1b_warm_up_without_request(c: Client, r) -> None:
    print("\n== 阶段 1b：无请求预热（config_sync → autoscale 预备 min_idle 热备）==")
    # 此刻除播种外无任何 route；tpl-warm 的 scope（min_idle=1）应被 autoscale 预热
    async def warm_ready() -> bool:
        return await r.scard(f"resource_manager:resource:scope:{WARM}:idle") >= 1
    # --with-mounts:同窗并发预热两个 min_idle scope(warm+mnt),首拉镜像时
    # 60s 偏紧,提到 90s
    ok = await wait_until(warm_ready, 90, 2)
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
    # 表达式 or 支（user 白名单跨 group 命中 e2e-main）在阶段 12b 验证：
    # 此处 e2e-main 已被 s1–s3 占满（cc=3），or 支 route 只会排队 504——
    # 原位置仅在「部署慢、s1 先过期」的时序下碰巧 200（2026-08-27 快跑实测 504）。
    return state


async def stage2b_sidecar(c: Client, r) -> None:
    """sidecar 多容器(--with-sidecar 专用):双容器 Pod + readiness 门控 + ConfigMap 挂载。"""
    if not WITH_SIDECAR:
        return
    print("\n== 阶段 2b：sidecar 多容器 —— 双容器 Pod / readiness 门控 / ConfigMap ==")
    # ConfigMap 资源(幂等:重跑已存在即视为就绪)
    cm_out = await kubectl("create", "configmap", "e2e-box-cm", "-n", NS,
                           "--from-literal=policy.yaml=e2e-box-policy-standin")
    check("BOX-ConfigMap e2e-box-cm 就绪（create 幂等）",
          "AlreadyExists" in cm_out or "created" in cm_out or "configmap" in cm_out.lower(),
          cm_out.strip().splitlines()[-1][:60] if cm_out.strip() else "")
    t0 = time.monotonic()
    code, raw, _ = await c.post("route", session_id="s-box", group="e2e-box")
    ok = code == 200 and raw.get("pod_id", "").startswith("agentserver-")
    check("BOX-route 部署双容器 Pod（等全容器 Ready）", ok,
          f"{code} {raw} ({time.monotonic()-t0:.0f}s)")
    if not ok:
        return
    pod_id = raw["pod_id"]
    out = await kubectl("get", "pod", "-n", NS, pod_id, "-o",
                        "jsonpath={.spec.containers[*].name}")
    names = out.split()
    check("BOX-Pod 内含 sidecar 容器（agent + box-standin）",
          "agent" in names and "box-standin" in names, out.strip())
    # sidecar readiness 参与 Pod Ready:route 返回即 TCP 探针已过(8096 在监听)
    check("BOX-sidecar readiness 门控（Ready 后才返回 sse_url）",
          raw.get("pod_sse_url", "").startswith("http://"),
          raw.get("pod_sse_url", ""))
    # ConfigMap subPath 真挂载:容器内读出 CM 内容
    out = await kubectl("exec", "-n", NS, pod_id, "-c", "box-standin",
                        "--", "cat", "/etc/box/policy.yaml")
    check("BOX-ConfigMap subPath 挂载内容可见（/etc/box/policy.yaml）",
          "e2e-box-policy-standin" in out, out.strip()[:60])


# ------------------------------------------------- 全量真实规格(--with-mounts)

def _pv_pvc_yaml(pv: str, pvc: str, node: str, host_dir: str) -> str:
    """静态供给清单:hostPath PV(钉节点) + 空 storageClassName PVC(volumeName 预绑)。

    nfs-provisioner 不供给(2026-08 实测),动态 StorageClass 不可用——与手工
    补验同款绕过;host_dir 由 hostPath type=DirectoryOrCreate 自建。PVC 不写
    namespace(由 apply -n 注入;PV 是 cluster 级,忽略 -n)。
    """
    return f"""apiVersion: v1
kind: PersistentVolume
metadata:
  name: {pv}
  labels: {{app: agent-runtime-e2e-mounts}}
spec:
  capacity: {{storage: 1Gi}}
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  hostPath: {{path: {host_dir}, type: DirectoryOrCreate}}
  nodeAffinity:
    required:
      nodeSelectorTerms:
      - matchExpressions:
        - key: kubernetes.io/hostname
          operator: In
          values: [{node}]
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {pvc}
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: ""
  volumeName: {pv}
  resources: {{requests: {{storage: 1Gi}}}}
"""


async def _schedulable_node() -> str:
    """首个可调度节点(Ready 且无 NoSchedule/NoExecute 污点)——静态供给 PV 的
    nodeAffinity 钉这里。误指 master(带污点)会让 Pod 永久 Pending
    「volume node affinity conflict」,而 PV 亲和不可变只能删了重建
    (2026-08-28 手工补验教训,见 feature 记录)。"""
    out = await kubectl("get", "nodes", "-o", "json")
    try:
        nodes = json.loads(out).get("items", [])
    except ValueError:
        return ""
    for n in nodes:
        conditions = {c.get("type"): c.get("status")
                      for c in n.get("status", {}).get("conditions", [])}
        if conditions.get("Ready") != "True":
            continue
        taints = n.get("spec", {}).get("taints") or []
        if any(t.get("effect") in ("NoSchedule", "NoExecute") for t in taints):
            continue
        return n.get("metadata", {}).get("name", "")
    return ""


async def _ensure_pvc(pvc: str, pv: str, node: str, host_dir: str) -> bool:
    """单对 PVC 就绪——「已 Bound 即复用,缺失才静态供给」。

    2026-08-28 真环境实测:ns 里可能有真实实验/运维按自己方式预置的同名 PVC
    (当时撞上 agent-data-pvc→pv-agent-data 手工对),而 volumeName 不可变,
    盲目 apply 自己的清单必 Invalid。复用既绑 PVC 也更贴近生产——PVC 由谁
    供给本就不归模板管。存在但 Pending(无主)→ 删后按我们的清单重建。
    """
    out = await kubectl("get", "pvc", "-n", NS, pvc,
                        "-o", "jsonpath={.status.phase}")
    if "Bound" in out:
        # 复用既绑:顺手清掉我们自己的孤儿 PV(Available 无 claimRef,安全)
        pv_out = await kubectl("get", "pv", pv, "-o", "jsonpath={.status.phase}")
        if pv_out.strip() == "Available":
            await kubectl("delete", "pv", pv, "--wait=false")
        return True
    if "NotFound" not in out:
        await kubectl("delete", "pvc", "-n", NS, pvc)   # Pending 无负载,安全
    apply_out = await kubectl("apply", "-n", NS, "-f", "-",
                              stdin=_pv_pvc_yaml(pv, pvc, node, host_dir))
    return ("created" in apply_out or "configured" in apply_out
            or "unchanged" in apply_out) and "error" not in apply_out.lower()


async def stage0_provision_mounts() -> bool:
    """全量规格前置资源:2 ConfigMap + 2 PVC 就绪(Bound)。

    返回 False = 预置失败(main 在 clean_previous 的 FLUSHDB/删 Pod 等
    破坏性动作**之前**中止)。CM/PVC 跨轮复用不清理(create/复用幂等)。
    """
    print("\n== 阶段 0：全量规格资源预置（ConfigMap / PVC）==")
    # ConfigMap(subPath key 名必须逐字等于 config.yaml/policy.yaml;
    # 已存在则复用——内容由阶段 2c 按 CM 当前值比对断言,不假设播种值)
    cm_out = await kubectl("create", "configmap", MNT_AGENT_CM, "-n", NS,
                           "--from-literal=config.yaml=e2e-agent-config-v1")
    ok_cm1 = check("MNT-ConfigMap agent-config-cm 就绪（create 幂等）",
                   "AlreadyExists" in cm_out or "created" in cm_out,
                   cm_out.strip()[:60])
    cm_out = await kubectl("create", "configmap", MNT_BOX_CM, "-n", NS,
                           "--from-literal=policy.yaml=e2e-box-policy-v1")
    ok_cm2 = check("MNT-ConfigMap box-policy-cm 就绪（create 幂等）",
                   "AlreadyExists" in cm_out or "created" in cm_out,
                   cm_out.strip()[:60])

    node = await _schedulable_node()
    ok_node = check("MNT-获取可调度节点（静态供给时 PV 钉节点,避污点 master）",
                    bool(node), node[:60])
    if not (ok_cm1 and ok_cm2 and ok_node):
        return False
    ok_apply = True
    for pvc, pv, host_dir in (
            (MNT_AGENT_PVC, MNT_AGENT_PV, "/mnt/agent-runtime-e2e/agent-data"),
            (MNT_BOX_PVC, MNT_BOX_PV, "/mnt/agent-runtime-e2e/box-data")):
        ok_apply &= await _ensure_pvc(pvc, pv, node, host_dir)
    if not check("MNT-双 PVC 预置（已 Bound 复用 / 缺失静态供给）", ok_apply):
        return False

    async def both_bound() -> bool:
        out = await kubectl("get", "pvc", "-n", NS, MNT_AGENT_PVC, MNT_BOX_PVC,
                            "-o", "jsonpath={.items[*].status.phase}")
        return out.split() == ["Bound", "Bound"]
    bound = await wait_until(both_bound, 30, 2)
    if not bound:
        # Released 陷阱:PVC 曾被删而 PV claimRef 钉死旧引用 → 永不再 Bind;
        # 清 claimRef 后重等(静态供给标准解法)
        for pv in (MNT_AGENT_PV, MNT_BOX_PV):
            await kubectl("patch", "pv", pv, "--type", "merge",
                          "-p", '{"spec":{"claimRef":null}}')
        bound = await wait_until(both_bound, 30, 2)
    return check("MNT-双 PVC 均 Bound", bound)


async def stage2c_mounts(c: Client, r) -> None:
    """全量真实规格(--with-mounts 专用):真实 config_sync 形状的 e2e 化——
    主容器三种挂载 + 显式 container_port/readiness 参数 + sidecar jiuwenbox
    完整规格,逐字段断言 + 容器内 exec 实证(2026-08-28 双真镜像手工全量
    验证记录在 docs/feature/2026-08-sidecar-containers.md)。"""
    if not WITH_MOUNTS:
        return
    print("\n== 阶段 2c：全量真实规格 —— 三种挂载 / 特权四件套 / 逐字段断言 ==")

    # 90s HTTP 超时 < 冷部署:先等 autoscale 暖池(stage1 下发即预热,min_idle=1)。
    # 全量规格 Pod 能否 Ready 本身就是被验命题——PVC 未 Bound / apparmor 不被
    # 节点接受 / CM 缺失都会卡在这里(而不是 route 超时后一片红)
    async def warm_ready() -> bool:
        return await r.scard(f"resource_manager:resource:scope:{MNT}:idle") >= 1
    ok = await wait_until(warm_ready, 120, 2)
    warm = await r.smembers(f"resource_manager:resource:scope:{MNT}:idle")
    check("MNT-全量规格暖 Pod 无请求预热 Ready（PVC/特权/挂载齐备）",
          ok and len(warm) >= 1, str(sorted(warm))[:120])
    if not ok:
        return

    t0 = time.monotonic()
    code, raw, _ = await c.post("route", session_id="s-mnt", group=MNT)
    pod_id = str(raw.get("pod_id", ""))
    check("MNT-route s-mnt 复用暖 Pod（全量规格零冷启动）",
          code == 200 and pod_id in warm,
          f"{code} {pod_id} ({time.monotonic()-t0:.0f}s)")
    if code != 200 or not pod_id:
        return

    # ---- Pod spec 逐字段断言(K8s API 形状为 camelCase;探针 port 可能序列化
    # 成数字或字符串,int() 归一;特权断言只打在 sidecar——主容器无
    # securityContext 渲染,只读性体现在 volumeMounts.readOnly)
    out = await kubectl("get", "pod", "-n", NS, pod_id, "-o", "json")
    try:
        spec = json.loads(out)
    except ValueError:
        check("MNT-Pod JSON 可解析", False, out.strip()[:120])
        return
    containers = {ct.get("name"): ct
                  for ct in spec.get("spec", {}).get("containers", [])}
    check("MNT-双容器（agent + jiuwenbox）",
          set(containers) == {"agent", "jiuwenbox"}, str(sorted(containers)))
    agent_ct = containers.get("agent") or {}
    box_ct = containers.get("jiuwenbox") or {}

    ports = [int(p.get("containerPort", 0)) for p in agent_ct.get("ports") or []]
    check("MNT-主容器显式 container_port=8086（=sse_port → 单端口声明）",
          8086 in ports, str(agent_ct.get("ports")))

    mounts = {m.get("mountPath"): m
              for m in agent_ct.get("volumeMounts") or []}
    check("MNT-主容器 ConfigMap subPath 挂载（/etc/agent/config.yaml）",
          (mounts.get("/etc/agent/config.yaml") or {}).get("subPath")
          == "config.yaml",
          str(mounts.get("/etc/agent/config.yaml")))
    check("MNT-主容器 hostPath 挂载 readOnly（/mnt/host-test）",
          (mounts.get(MNT_HOST_PATH) or {}).get("readOnly") is True,
          str(mounts.get(MNT_HOST_PATH)))
    check("MNT-主容器 PVC 挂载（/var/lib/agent）",
          "/var/lib/agent" in mounts, str(sorted(mounts)))

    probe = agent_ct.get("readinessProbe") or {}
    http_get = probe.get("httpGet") or {}
    try:
        probe_port = int(http_get.get("port", 0))
    except (TypeError, ValueError):
        probe_port = -1
    check("MNT-主容器 readiness 探针（health_path + 8086 + 5s/5s）",
          http_get.get("path") == HEALTH_PATH and probe_port == 8086
          and probe.get("initialDelaySeconds") == 5
          and probe.get("periodSeconds") == 5, str(probe))
    if AGENT_ENV:
        env = {e.get("name"): e.get("value")
               for e in agent_ct.get("env") or []}
        check("MNT-主容器 agent_env 注入逐项可见",
              all(env.get(k) == str(v) for k, v in AGENT_ENV.items()), str(env))

    sc = box_ct.get("securityContext") or {}
    caps = (sc.get("capabilities") or {}).get("add") or []
    check("MNT-sidecar 特权三件套（privileged + SYS_ADMIN/NET_ADMIN + Unconfined seccomp）",
          sc.get("privileged") is True and set(caps) == {"SYS_ADMIN", "NET_ADMIN"}
          and (sc.get("seccompProfile") or {}).get("type") == "Unconfined",
          str(sc))
    anno = (spec.get("metadata") or {}).get("annotations") or {}
    check("MNT-sidecar apparmor annotation（Pod 级 unconfined）",
          anno.get("container.apparmor.security.beta.kubernetes.io/jiuwenbox")
          == "unconfined", str(anno))

    # 卷全景按内容断言(卷名是内部实现细节,引用的 CM/hostPath/PVC 才是契约)
    vols = spec.get("spec", {}).get("volumes") or []
    cm_names = {v["configMap"]["name"] for v in vols if v.get("configMap")}
    hp_paths = {v["hostPath"]["path"] for v in vols if v.get("hostPath")}
    pvc_names = {v["persistentVolumeClaim"]["claimName"]
                 for v in vols if v.get("persistentVolumeClaim")}
    check("MNT-卷全景（2 ConfigMap + 2 hostPath + 2 PVC 各就位）",
          {MNT_AGENT_CM, MNT_BOX_CM} <= cm_names
          and {MNT_HOST_PATH, "/sys/fs/cgroup"} <= hp_paths
          and {MNT_AGENT_PVC, MNT_BOX_PVC} <= pvc_names,
          f"cm={sorted(cm_names)} hp={sorted(hp_paths)} pvc={sorted(pvc_names)}")

    box_probe = box_ct.get("readinessProbe") or {}
    box_tcp = box_probe.get("tcpSocket") or {}
    try:
        box_port = int(box_tcp.get("port", 0))
    except (TypeError, ValueError):
        box_port = -1
    expect_box_port = 8321 if SIDECAR_IMAGE != IMAGE else SIDECAR_STANDIN_PORT
    check("MNT-sidecar TCP readiness 探针（5s/5s）",
          box_port == expect_box_port
          and box_probe.get("initialDelaySeconds") == 5
          and box_probe.get("periodSeconds") == 5, str(box_probe))

    # ---- exec 实证:挂载不止出现在 spec 里,容器内要真看得见/写得进
    # (真镜像无 shell 时 exec 失败 = 如实暴露的真实场景信息,不吞)
    # CM 内容与「CM 当前值」比对而非播种值——预置的 CM 可能来自真实实验
    # (2026-08-28 实测 ns 里已有 agent-config-cm),挂载正确性=内容一致
    cm_val = (await kubectl("get", "configmap", MNT_AGENT_CM, "-n", NS,
                            "-o", "jsonpath={.data.config\\.yaml}")).strip()
    out = await kubectl("exec", "-n", NS, pod_id, "-c", "agent", "--",
                        "cat", "/etc/agent/config.yaml")
    check("MNT-主容器 ConfigMap 内容可见（cat /etc/agent/config.yaml）",
          bool(cm_val) and out.strip() == cm_val,
          f"cm={cm_val[:40]!r} pod={out.strip()[:40]!r}")
    out = await kubectl("exec", "-n", NS, pod_id, "-c", "agent", "--",
                        "ls", "-d", MNT_HOST_PATH)
    check("MNT-主容器 hostPath 目录存在（DirectoryOrCreate 由 kubelet 建）",
          MNT_HOST_PATH in out, out.strip()[:60])
    out = await kubectl("exec", "-n", NS, pod_id, "-c", "agent", "--",
                        "sh", "-c", f"touch {MNT_HOST_PATH}/ro 2>&1")
    check("MNT-主容器 hostPath read_only=true → 写入被拒",
          "Read-only file system" in out, out.strip()[:80])
    out = await kubectl("exec", "-n", NS, pod_id, "-c", "agent", "--",
                        "sh", "-c",
                        "echo mnt-probe > /var/lib/agent/probe"
                        " && cat /var/lib/agent/probe")
    check("MNT-主容器 PVC 可写回读（agent-data-pvc）",
          "mnt-probe" in out, out.strip()[:60])
    cm_val = (await kubectl("get", "configmap", MNT_BOX_CM, "-n", NS,
                            "-o", "jsonpath={.data.policy\\.yaml}")).strip()
    out = await kubectl("exec", "-n", NS, pod_id, "-c", "jiuwenbox", "--",
                        "cat", "/etc/box/policy.yaml")
    check("MNT-sidecar ConfigMap 内容可见（cat /etc/box/policy.yaml）",
          bool(cm_val) and out.strip() == cm_val,
          f"cm={cm_val[:40]!r} pod={out.strip()[:40]!r}")
    out = await kubectl("exec", "-n", NS, pod_id, "-c", "jiuwenbox", "--",
                        "ls", "/sys/fs/cgroup")
    check("MNT-sidecar 宿主 cgroup 可见（ls /sys/fs/cgroup）",
          bool(out.strip()), out.strip()[:60])
    out = await kubectl("exec", "-n", NS, pod_id, "-c", "jiuwenbox", "--",
                        "sh", "-c",
                        "echo box-probe > /var/lib/jiuwenbox/probe"
                        " && cat /var/lib/jiuwenbox/probe")
    check("MNT-sidecar PVC 可写回读（box-data-pvc）",
          "box-probe" in out, out.strip()[:60])


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
    # --with-sidecar: box Pod(pod_ttl=3600 长存)也在注册表,期望 +1;
    # --with-mounts: mnt Pod(2c 已 route,pod_ttl=3600 长存)同理再 +1
    check("D-不变量 5：pods:registered 仍持有（待 RM 回收后清）",
          len(reg) == 2 + (1 if WITH_SIDECAR else 0) + (1 if WITH_MOUNTS else 0),
          str(reg))
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
    # --with-sidecar: box Pod 未参与本阶段回收,仍注册(阶段 12 cleanup 统一清);
    # --with-mounts: mnt Pod 同理
    check("K-notify_pod_dead 已清 SM 注册",
          len(reg) == (1 if WITH_SIDECAR else 0) + (1 if WITH_MOUNTS else 0),
          str(reg))


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
          await r.zcard(f"resource_manager:resource:scope:{BAD}:deploying") == 0)


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
          await r.zcard(f"session_manager:scope:{FSCOPE}:waiters") == 0)


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

    # deploying 已迁 ZSET(占位 deadline 化):「静息全空」是**收敛断言**而非
    # 单次快照——min_idle≥1 的 scope 在 autoscale 驱动下随时有 ~10-12s 的
    # 在途预热占位,快照会误报(2026-08-28 实测)。真泄漏(进程崩后未清的
    # 占位)永不收敛,有界等待后仍非空 → FAIL,牙齿不变。
    async def _deploying_remaining() -> list[str]:
        remaining = []
        async for key in r.scan_iter(
                match="resource_manager:resource:scope:*:deploying", count=100):
            try:
                count = await r.zcard(key)
            except Exception:               # 老库残留 SET 型键(升级未清库)
                count = await r.scard(key)
            if count:
                remaining.append(key)
        return remaining

    async def _drained() -> bool:           # 真 async 闭包(lambda 里协程==0 恒 False)
        return not await _deploying_remaining()

    drained = await wait_until(_drained, timeout=40, interval=2,
                               desc="deploying drained")
    check("IV-静息时 deploying 占位全空(缺陷⑤网)", drained,
          str(await _deploying_remaining()))

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
    # 牙齿=「被删的 Pod 从 K8s 消失」,不断言 ns 恒零——min_idle scope
    # (e2e-warm 常开,--with-mounts 再加 e2e-mnt)的 autoscale 重建热备会与
    # 本即时采样竞速,「No resources found」与 H0 配置驱动预热自相矛盾
    check("cleanup 运维批删（含 deploy 失败遗留的孤儿 Pod）",
          code == 200 and cleaned >= 0
          and not any(p in out for p in all_pods),
          f"cleaned={cleaned}; kubectl: {out.strip().splitlines()[-1][:60]}")
    await asyncio.sleep(12)   # 等 watch/reconcile 兜底清 Redis
    left = await r.smembers("resource_manager:resource:pods:all")
    # cleanup 只删物理 Pod;watch(≤10s)PURGE 后 min_idle scope 的 autoscale
    # 会重建热备(新 pod_id)。牙齿=「cleanup 前的存量 Pod 全部经 NotFound
    # 路径收敛」,重建者应全是 min_idle 暖 Pod——旧「恒零」断言在同 watch
    # tick 双 scope 一起重建的时序下会闪红(2026-08-28 分析)
    rebuilt = sorted(set(left) - set(all_pods))
    check("L-watch/reconcile 兜底清空 Redis 编排态（存量 Pod 全收敛）",
          not (set(all_pods) & set(left)), f"rebuilt_by_autoscale={rebuilt}")
    sm_keys = await r.keys("session_manager:pod:*")
    check("L-SM Pod 注册态全清", not sm_keys, str(sm_keys[:5]))


async def stage12b_or_branch(c: Client, r) -> None:
    """表达式 or 支(阶段 12 清场后):e2e-main 此时空闲,or 支命中可确定性 200。

    原位置(阶段 2 尾)被 s1–s3 占满 cc=3,or 支 route 只能排队 504——只有
    「部署慢、会话先过期」的时序下碰巧 200(2026-08-27 快跑实测暴露)。
    """
    print("\n== 阶段 12b：表达式 or 支（清场后确定性验证）==")
    code, raw_vip, _ = await c.post("route", session_id="s-vip",
                                    group="e2e-no-such-group", user="e2e-vip")
    check("route 表达式 or 支（user 白名单跨 group 命中 e2e-main）",
          code == 200 and raw_vip.get("pod_id", "").startswith("agentserver-"),
          f"{code} {str(raw_vip)[:80]}")


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
    parser.add_argument("--health-path", default=env("AGENT_RUNTIME_E2E_HEALTH_PATH", "/health"),
                        help="模板 health_path(readiness 与场景 N 探测同源;"
                             "真 AgentServer 为 /api/v1/health)")
    parser.add_argument("--sse-path", default=env("AGENT_RUNTIME_E2E_SSE_PATH", "/sse"),
                        help="模板 sse_path(真 AgentServer 为 /api/v1/events/stream)")
    parser.add_argument("--agent-env", default=env("AGENT_RUNTIME_E2E_AGENT_ENV", None),
                        help="模板 agent_env 的 JSON 对象(真 AgentServer 需 "
                             "AGENT_HTTP_ENABLED/HOST/PORT 三件套开 HTTP 入口)")
    parser.add_argument("--with-sidecar", action="store_true",
                        default=env("AGENT_RUNTIME_E2E_WITH_SIDECAR", "") == "1",
                        help="追加 sidecar 多容器阶段（替身=influxdb 改端口 8096，"
                             "tcp 探针；真 jiuwenbox 镜像用 --sidecar-image）")
    parser.add_argument("--sidecar-image", default=None,
                        help="sidecar 镜像（默认复用 --image 作替身）")
    parser.add_argument("--with-mounts", action="store_true",
                        default=env("AGENT_RUNTIME_E2E_WITH_MOUNTS", "") == "1",
                        help="追加全量真实规格阶段（主容器 cm/hp/pvc 三挂载 + "
                             "sidecar jiuwenbox 完整规格 + 显式 container_port；"
                             "自动预置 ConfigMap 与静态 hostPath PV/PVC，"
                             "需 PV 创建权限）")
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
    global BOX, WITH_SIDECAR, SIDECAR_IMAGE, HEALTH_PATH, SSE_PATH, AGENT_ENV
    global MNT, WITH_MOUNTS
    args = _parse_args()
    BASE = args.base_url.rstrip("/")
    REDIS_URL = args.redis_url
    NS = args.namespace
    IMAGE = args.image
    HEALTH_PATH = args.health_path
    SSE_PATH = args.sse_path
    if args.agent_env:
        AGENT_ENV = json.loads(args.agent_env)
        if not isinstance(AGENT_ENV, dict):
            raise SystemExit("--agent-env 必须是 JSON 对象")
    else:
        AGENT_ENV = None
    DB_DSN = {"host": args.db_host, "port": args.db_port, "user": args.db_user,
              "password": args.db_password, "name": args.db_name,
              "type": args.db_type}
    MAIN, FSCOPE = "e2e-main", "e2e-f"     # scope_id 由 config_sync 下发(字面量)
    WARM, BAD = "e2e-warm", "e2e-bad"
    BOX, WITH_SIDECAR = "e2e-box", bool(args.with_sidecar)
    MNT, WITH_MOUNTS = "e2e-mnt", bool(args.with_mounts)
    SIDECAR_IMAGE = args.sidecar_image or IMAGE
    build_templates()

    print(f"agent-runtime 集成冒烟测试 @ {time.strftime('%F %T')}")
    print(f"service={BASE} redis={REDIS_URL} ns={NS} image={IMAGE}"
          + (f" sidecar={SIDECAR_IMAGE}" if WITH_SIDECAR else "")
          + (" with-mounts" if WITH_MOUNTS else ""))

    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        if not await preflight(r, args.force_flush):
            print("\n===== 前置自检未通过，中止 =====")
            raise SystemExit(2)
        if WITH_MOUNTS and not await stage0_provision_mounts():
            print("\n===== 全量规格资源预置未通过，中止（未执行清场）=====")
            raise SystemExit(2)
        async with httpx.AsyncClient(timeout=90.0) as http:
            c = Client(http, BASE)
            await clean_previous(c, r)

            await stage1_seed(c, r)
            await stage1b_warm_up_without_request(c, r)
            state = await stage2_route_abc(c, r)
            await stage2b_sidecar(c, r)
            await stage2c_mounts(c, r)
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
            await stage12b_or_branch(c, r)
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
