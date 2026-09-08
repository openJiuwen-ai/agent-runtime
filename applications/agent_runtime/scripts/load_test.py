#!/usr/bin/env python
# coding: utf-8
"""agent-runtime 场景化压测 / 浸泡工具(asyncio + httpx,零额外依赖)。

目标:单入口(LB 或单实例)打 route/touch 混合负载 + 配置面扰动,输出延迟
分位数 / 吞吐 / 错误码分布;长时 --duration 即浸泡(周期增量报告)。

场景(--scenario):
- route          纯路由;
- route_touch    路由 + 保活;
- queued         小容量模板刻意打满(503 快失败路径);
- config_churn   热更新:流量进行中周期性 config_sync 翻转 B 类参数
                 (scope_concurrency/pod_ttl 两态),断言传播与流量无感;
- config_refresh 强制刷新:流量进行中周期性 config_refresh,断言代次单调、
                 存量会话亲和、新代暖 Pod 重建收敛、冷启动延迟;
- mixed          route_touch + churn + refresh 同场(共享 config 面串行锁)。

判定层(与场景正交,默认全开):
- 客户端:延迟分位数 / 错误码直方图 / 传输层错误率(原有);
- 可视化接口断言:/visualization/{config,scope,scopes,stats,recent_errors,
  evaluation}——热更新传播(snapshot ver + capacity 新值)、刷新代次、
  收尾巡检(deploying 收敛 / recent_errors 无白名单外码 / stats 交叉验证 /
  自评估无 critical findings)。注意 endpoints 与 recent_errors 是实例视角,
  LB 后靠多次采样按 instance_id 聚合近似;scopes/history 是 Redis 全局态;
- ERROR 日志感知(--log-source auto|file|kubectl|none):run 开始打快照
  (文件字节偏移 / kubectl --since-time),结束扫增量,按 " - ERROR - "
  计数;超 --log-error-max(默认 0)即 fail。业务失败(503/409)只打 INFO,
  ERROR 行 = 真异常,故默认阈值 0 是安全硬门。kubectl 源只读且 Pod 重启会
  丢日志(尽力源);精确判定面是宿主机 deploy_replicas.sh 的文件日志源。

安全边界(红线):
- 业务与配置操作全程只经 HTTP API,不碰 Redis/DB/K8s 写面;唯一例外是
  只读 `kubectl logs`(K8s 形态下感知 ERROR 日志);
- 不 FLUSHDB、不调 cleanup 端点(它会删目标 namespace 下全部 AgentServer
  Pod)——模板/规则/会话按 run-id 命名空间化,靠 TTL 老化;
- config_sync/config_refresh 服务端共用串行锁(并发即 409):工具内由
  config_plane_lock 单发射者串行,不自造 409;
- queued 场景 503 SCOPE_FULL 属预期(只报告不判败);churn/refresh 场景
  的预期错误码白名单见 EXPECTED_ERRORS,白名单外错误即 fail。

用法示例:
  uv run --no-sync python scripts/load_test.py \
      --base-url http://127.0.0.1:30091/api/session --duration 60 \
      --scenario route_touch --concurrency 8 --rps 50
  # 热更新/强制刷新/混合(K8s 联调,kubectl 感知 ERROR 日志):
  uv run --no-sync python scripts/load_test.py \
      --base-url http://127.0.0.1:30091/api/session --scenario mixed \
      --duration 300 --groups 2 --log-source kubectl --json
  # 浸泡:--duration 3600 --report-interval 300
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import shutil
import string
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import httpx

# ---------------------------------------------------------------- 参数

SCENARIOS = ["route", "route_touch", "queued",
             "config_churn", "config_refresh", "mixed"]
CONFIG_SCENARIOS = ("config_churn", "config_refresh", "mixed")

# churn 两态(B 类参数,同 id 仅参数变 → 热更新而非重建)
CHURN_STATES = (
    {"scope_concurrency": 40, "pod_ttl": 120},
    {"scope_concurrency": 50, "pod_ttl": 180},
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="agent-runtime 场景化压测/浸泡(6 场景 + 可视化/日志判定)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--base-url", default="http://127.0.0.1:8091/api/session",
                   help="单入口(LB 或实例),含 /api/session 前缀")
    p.add_argument("--scenario", choices=SCENARIOS, default="route",
                   help="route=纯路由;route_touch=路由+保活;queued=容量满"
                        "快失败;config_churn=热更新;config_refresh=强制刷新;"
                        "mixed=混合收官")
    p.add_argument("--concurrency", type=int, default=4, help="并发 worker 数")
    p.add_argument("--rps", type=float, default=0,
                   help="目标速率(开环令牌桶);0=闭环(各 worker 全速)")
    p.add_argument("--duration", type=float, default=30,
                   help="压测时长秒(浸泡即调大)")
    p.add_argument("--warmup", type=float, default=3,
                   help="预热秒数(不计入统计)")
    p.add_argument("--report-interval", type=float, default=10,
                   help="浸泡周期报告间隔秒")
    p.add_argument("--groups", type=int, default=4, help="并发 scope(group)数")
    p.add_argument("--sessions-per-group", type=int, default=8,
                   help="每 scope 的会话数(route_touch 循环 touch 这些会话)")
    p.add_argument("--scope-concurrency", type=int, default=2,
                   help="queued 场景模板 scope_concurrency(刻意小)")
    p.add_argument("--pod-concurrency", type=int, default=2,
                   help="queued 场景模板 pod_concurrency")
    p.add_argument("--no-seed", action="store_true",
                   help="不播种模板(要求目标已有可匹配的路由规则;"
                        "config 面场景不适用)")
    p.add_argument("--namespace", default="agent-runtime-e2e-wmq",
                   help="AgentServer Pod 拉起的 namespace(须已存在)")
    p.add_argument("--cleanup", choices=["none", "config"], default="config",
                   help="结束时删除本次 run 的模板/规则(默认);none=留待 TTL")
    p.add_argument("--timeout", type=float, default=90.0, help="单请求超时秒")
    p.add_argument("--max-error-rate", type=float, default=1.0,
                   help="传输层错误率超过此值退出码 1(默认 1.0=永不因业务"
                        "错误判败)")
    p.add_argument("--json", action="store_true", help="末尾输出机器可读 JSON 块")

    # ---- config_churn(热更新)
    p.add_argument("--churn-interval", type=float, default=10.0,
                   help="config_churn/mixed:两次 config_sync 间隔秒")
    p.add_argument("--churn-propagate-timeout", type=float, default=30.0,
                   help="sync 后等热更新传播(快照 ver+capacity 新值)的轮询预算秒")
    p.add_argument("--churn-jitter-budget-ms", type=float, default=0.0,
                   help="sync 窗口 route p99 相对基线增量预算 ms;0=仅报告")
    p.add_argument("--churn-jitter-fail", action="store_true",
                   help="把 sync 窗口 p99 抖动检查升级为 fail 级")

    # ---- config_refresh(强制刷新)
    p.add_argument("--refresh-interval", type=float, default=15.0,
                   help="config_refresh/mixed:两次 config_refresh 间隔秒")
    p.add_argument("--refresh-cold-probes", type=int, default=2,
                   help="每个刷新窗口注入的一次性新会话数(测冷启动+驱动新代部署)")
    p.add_argument("--refresh-rebuild-budget", type=float, default=120.0,
                   help="等新代暖 Pod 出现的轮询预算秒")
    p.add_argument("--refresh-cold-budget-ms", type=float, default=45000.0,
                   help="冷启动探测延迟预算 ms(warn 级)")

    # ---- ERROR 日志感知
    p.add_argument("--log-source", choices=["auto", "file", "kubectl", "none"],
                   default="auto",
                   help="auto=--log-file > .replicas.json 文件 > kubectl > 无")
    p.add_argument("--log-file", action="append", default=None,
                   help="日志文件路径(可重复;--log-source file 时必填)")
    p.add_argument("--log-namespace", default="agent-runtime-e2e",
                   help="kubectl 日志源 namespace(runtime 服务 Pod 所在 ns;"
                        "AgentServer Pod 在另一 ns,不在日志感知范围)")
    p.add_argument("--log-selector", default="app=agent-runtime",
                   help="kubectl 日志源 label selector")
    p.add_argument("--log-error-max", type=int, default=0,
                   help="计入的 ERROR 行数超过此值 → 退出码 1")
    p.add_argument("--log-error-allow", action="append", default=None,
                   help="ERROR 行白名单正则(可重复),命中不计入")
    p.add_argument("--log-scope", choices=["all", "run"], default="all",
                   help="all=窗口内全部 ERROR;run=仅行尾含本 run id 的请求行")
    p.add_argument("--log-sample", type=int, default=10, help="采样展示条数")
    p.add_argument("--no-log-watch", action="store_true",
                   help="等价 --log-source none")

    # ---- 判定层开关
    p.add_argument("--no-postcheck", action="store_true", help="关闭收尾巡检")
    p.add_argument("--allow-error", action="append", default=None,
                   help="追加预期错误码(格式 503/SCOPE_FULL,可重复)")
    p.add_argument("--settle", type=float, default=6.0,
                   help="流量停止后、巡检前的静置秒(容忍 stats 5s 批量 flush)")
    args = p.parse_args()

    if args.no_log_watch:
        args.log_source = "none"
    if args.log_source == "file" and not args.log_file:
        p.error("--log-source file 需要 --log-file")
    if args.scenario in CONFIG_SCENARIOS and args.no_seed:
        p.error(f"--scenario {args.scenario} 需要播种(--no-seed 不适用)")
    if args.scenario in CONFIG_SCENARIOS:
        interval = min(args.churn_interval, args.refresh_interval)
        if args.duration < 2 * interval:
            print(f"[load] 警告:duration={args.duration}s 短于 2×interval"
                  f"({interval}s),config 面场景可能没有扰动发生")
        if args.scenario in ("config_refresh", "mixed") \
                and args.duration < args.refresh_rebuild_budget:
            print(f"[load] 警告:duration={args.duration}s 短于重建预算"
                  f"{args.refresh_rebuild_budget}s,收敛断言可能超时")
    return args


# ---------------------------------------------------------------- 信封与载荷


def _envelope(msg_type, request_id, session_id, group_id):
    return {
        "type": msg_type,
        "metadata": {
            "request_id": request_id,
            "session_id": session_id,
            "user_id": f"loaduser-{request_id}",
            "bot_id": "loadbot",
            "extra": {"group_id": group_id},
        },
        "rawdata": {},
    }


def _main_container(container_id, agent_image):
    return {
        "container_id": container_id,
        "name": "agent",
        "image": agent_image,
        # influxdb:1.8 的 /health 在 8086(e2e 同款替代 AgentServer)
        "ports": [{"name": "sse", "containerPort": 8086}],
    }


def _scenario_template_params(scenario: str, args) -> dict:
    """按场景定模板策略字段。config 面场景 session_ttl 放大(防亲和误报)、
    refresh/mixed 用 min_idle_pods=1(否则新代暖 Pod 只被新会话触发,
    重建收敛断言不可靠)。"""
    if scenario == "queued":
        return {"scope_concurrency": args.scope_concurrency,
                "pod_concurrency": args.pod_concurrency,
                "session_ttl": 120, "pod_ttl": 120, "min_idle_pods": 0}
    if scenario in ("route", "route_touch"):
        return {"scope_concurrency": 50, "pod_concurrency": 10,
                "session_ttl": 120, "pod_ttl": 120, "min_idle_pods": 0}
    if scenario == "config_churn":
        return {"scope_concurrency": CHURN_STATES[0]["scope_concurrency"],
                "pod_concurrency": 10,
                "session_ttl": 600, "pod_ttl": CHURN_STATES[0]["pod_ttl"],
                "min_idle_pods": 0}
    # config_refresh / mixed。pod_concurrency=4 → max_pods=⌈40/4⌉=10:连续
    # 刷新时老代 Pod 未回收也计入 pods:all,容量闸门太紧会让新代冷启动
    # 直接 503(max_pods 语义,非缺陷)——放宽到 10 让冷探测多为真冷启动。
    return {"scope_concurrency": 40, "pod_concurrency": 4,
            "session_ttl": 600, "pod_ttl": 120, "min_idle_pods": 1}


def _ids(run: str, scenario: str) -> tuple[str, str]:
    suffix = "q" if scenario == "queued" else "a"
    return f"c-{run}-{suffix}", f"tpl-{run}-{suffix}"


def _seed_payload(run: str, args, params: dict) -> dict:
    """三段式快照载荷。churn 重放时同 id 仅 params 变(热更新而非删建)。"""
    cid, tpl_id = _ids(run, args.scenario)
    tpl = {"template_id": tpl_id, "main_container_id": cid,
           "namespace": args.namespace, "ready_timeout": 60, **params}
    scopes = [
        {"scope_id": f"scope-{run}-{gi}", "index": gi, "template_id": tpl_id,
         "routing_rules": f"group_id in ('grp-{run}-{gi}')"}
        for gi in range(args.groups)
    ]
    return {"containers": [_main_container(cid, "influxdb:1.8")],
            "templates": [tpl], "scopes": scopes}


async def _seed(client: httpx.AsyncClient, base: str, run: str, args) -> dict:
    """全量下发本次 run 的容器 + 模板 + scope(快照式替换,一次请求;
    容器 id 带 run 前缀,多轮压测不撞唯一约束)。"""
    params = _scenario_template_params(args.scenario, args)
    payload = _seed_payload(run, args, params)
    env = _envelope("config_sync", f"seed-{run}", None, f"grp-{run}-0")
    env["rawdata"] = payload
    r = await client.post(f"{base}/config_sync", json=env, timeout=args.timeout)
    if r.status_code != 200:
        print(f"[seed] config_sync failed: {r.status_code} {r.text[:200]}")
        sys.exit(2)
    return {"scopes": payload["scopes"], "params": params}


async def _cleanup_config(client: httpx.AsyncClient, base: str, run: str,
                          scopes: list[dict], tpl_ids: list[str]) -> None:
    """清掉本次 run 播种的配置。

    播种是快照式全量替换——播种时已清掉历史配置,此刻服务里的配置
    只属于本 run,因此清空全量(templates/scopes 皆空)等价于只删本 run。
    """
    env = _envelope("config_sync", f"clean-{run}", None, "x")
    env["rawdata"] = {"containers": [], "templates": [], "scopes": []}
    await client.post(f"{base}/config_sync", json=env, timeout=90.0)
    print(f"[cleanup] 已清空本次 run 的 {len(scopes)} 个 scope / {len(tpl_ids)} 个模板;"
          f"会话与 AgentServer Pod 留待 TTL 老化(不动 cleanup 端点)")


# ---------------------------------------------------------------- HTTP 基础


async def _post(client: httpx.AsyncClient, url: str, env: dict,
                timeout: float) -> tuple[int, dict, dict]:
    """POST 信封;返回 (status, rawdata, 全 body)。worker 与控制器共用。"""
    r = await client.post(url, json=env, timeout=timeout)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {}
    return r.status_code, body.get("rawdata") or {}, body


def _dig(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _as_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 统计


class Stats:
    def __init__(self) -> None:
        self.latencies: list[float] = []          # 毫秒
        self.ts: list[float] = []                 # time.monotonic(),与上同下标
        self.endpoints: list[str] = []            # route/touch
        self.errors: Counter[str] = Counter()     # "{status}/{error_code}"
        self.transport_errors: Counter[str] = Counter()
        self.total = 0
        self.transport_total = 0

    def record(self, latency_ms: float, status: int, error_code: str | None,
               endpoint: str = "route") -> None:
        self.total += 1
        self.latencies.append(latency_ms)
        self.ts.append(time.monotonic())
        self.endpoints.append(endpoint)
        if status != 200:
            self.errors[f"{status}/{error_code or '-'}"] += 1

    def record_transport_error(self, kind: str) -> None:
        self.transport_total += 1
        self.transport_errors[kind] += 1

    @staticmethod
    def _pct(lat: list[float], q: float) -> float:
        if not lat:
            return 0.0
        s = sorted(lat)
        return round(s[min(len(s) - 1, int(q * len(s)))], 1)

    @staticmethod
    def _snapshot_of(lat: list[float]) -> dict:
        return {"count": len(lat), "p50": Stats._pct(lat, 0.50),
                "p90": Stats._pct(lat, 0.90), "p99": Stats._pct(lat, 0.99),
                "max": round(max(lat), 1) if lat else 0.0}

    def snapshot(self) -> dict:
        snap = self._snapshot_of(self.latencies)
        snap["errors"] = dict(self.errors)
        snap["transport_errors"] = dict(self.transport_errors)
        return snap

    def snapshot_window(self, t0: float, t1: float) -> dict:
        """[t0, t1](monotonic)时间窗内的延迟分位数(config 事件抖动观察)。"""
        return self._snapshot_of(
            [l for l, t in zip(self.latencies, self.ts) if t0 <= t <= t1])

    def snapshot_complement(self, windows: list[tuple[float, float]]) -> dict:
        """排除全部窗口后的基线延迟分位数。"""
        return self._snapshot_of(
            [l for l, t in zip(self.latencies, self.ts)
             if not any(a <= t <= b for a, b in windows)])

    def unexpected_errors(self, whitelist: set[str]) -> dict:
        return {k: v for k, v in self.errors.items() if k not in whitelist}


class AffinityTracker:
    """会话→Pod 亲和被动记录:worker 持续 route 固定会话集,响应里的
    pod_id 喂 note();同 session 换 pod 即记一次违规事件(封顶防膨胀)。
    checkpoint 用事件下标,violations_since 取其后的增量。"""

    _CAP = 5000

    def __init__(self) -> None:
        self._pods: dict[str, str] = {}
        self._events: list[tuple[float, str, str, str]] = []

    def note(self, session_id, pod_id) -> None:
        if not session_id or not pod_id:
            return
        prev = self._pods.get(session_id)
        if prev is None:
            self._pods[session_id] = pod_id
        elif prev != pod_id:
            if len(self._events) < self._CAP:
                self._events.append(
                    (time.monotonic(), session_id, prev, pod_id))
            self._pods[session_id] = pod_id

    def checkpoint(self) -> int:
        return len(self._events)

    def violations_since(self, cp: int) -> list[tuple[float, str, str, str]]:
        return self._events[cp:]

    def all_violations(self) -> list[tuple[float, str, str, str]]:
        return list(self._events)


# ---------------------------------------------------------------- 限速


class _TokenBucket:
    """开环速率控制(rps>0 时):固定间隔发放令牌。"""

    def __init__(self, rps: float) -> None:
        self.interval = 1.0 / rps
        self._next_at = time.monotonic()

    async def take(self) -> None:
        now = time.monotonic()
        wait = self._next_at - now
        if wait > 0:
            await asyncio.sleep(wait)
            self._next_at += self.interval
        else:
            self._next_at = max(self._next_at + self.interval, now)


# ---------------------------------------------------------------- 可视化客户端


class VisClient:
    """GET /visualization/*(无 /api/session 前缀)。endpoints/recent_errors
    是实例视角,LB 后靠多次采样按 instance_id 聚合近似(Connection: close
    促新连接分布);scope/config/scopes/history 是 Redis/DB 全局态。"""

    def __init__(self, client: httpx.AsyncClient, base: str) -> None:
        self.client = client
        self.root = base.rsplit("/api/session", 1)[0]

    async def get(self, path: str, timeout: float = 30.0,
                  close: bool = False) -> tuple[int, dict]:
        try:
            r = await self.client.get(
                f"{self.root}{path}", timeout=timeout,
                headers={"connection": "close"} if close else None)
            try:
                return r.status_code, r.json()
            except Exception:  # noqa: BLE001
                return r.status_code, {}
        except Exception:  # noqa: BLE001
            return 0, {}

    async def poll(self, path: str, pred, timeout: float,
                   interval: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            status, body = await self.get(path)
            if status == 200:
                try:
                    if pred(body):
                        return True
                except Exception:  # noqa: BLE001
                    pass
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(interval)

    async def recent_errors_aggregated(self, samples: int = 6,
                                       limit: int = 200) -> list[dict]:
        """多采样按 instance_id 分桶、(ts,request_id,error_code,endpoint)
        去重后拼接——LB 后的单进程视角近似。"""
        per_instance: dict[str, dict] = {}
        for _ in range(samples):
            status, body = await self.get(
                f"/visualization/recent_errors?limit={limit}", close=True)
            if status != 200:
                continue
            bucket = per_instance.setdefault(
                str(body.get("instance_id", "?")), {})
            for e in body.get("errors") or []:
                key = (e.get("ts"), e.get("request_id"),
                       e.get("error_code"), e.get("endpoint"))
                bucket.setdefault(key, dict(e, instance_id=body.get("instance_id")))
        return [e for b in per_instance.values() for e in b.values()]


# ---------------------------------------------------------------- 判定框架


class CheckRecorder:
    """三级判定:fail(计退出码)/ warn(仅报告)/ skip(前置不满足)。
    仿 e2e_lib.check/summary_and_exit,自实现以保持单文件零依赖。"""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def record(self, name: str, ok, detail: str = "",
               severity: str = "fail") -> bool:
        ok = bool(ok)
        self.items.append({"name": name, "ok": ok, "severity": severity,
                           "detail": str(detail)[:400]})
        if severity == "skip" and not ok:
            tag = "SKIP"
        elif severity == "warn" and not ok:
            tag = "WARN"
        else:
            tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)
        return ok

    def failed(self) -> bool:
        return any(not i["ok"] and i["severity"] == "fail" for i in self.items)

    def counts(self) -> tuple[int, int, int]:
        fails = sum(1 for i in self.items
                    if not i["ok"] and i["severity"] == "fail")
        warns = sum(1 for i in self.items
                    if not i["ok"] and i["severity"] == "warn")
        return len(self.items) - fails - warns, fails, warns

    def summary(self) -> None:
        n_pass, n_fail, n_warn = self.counts()
        print(f"[final] checks: {n_pass} pass / {n_fail} fail / {n_warn} warn")
        for i in self.items:
            if not i["ok"] and i["severity"] == "fail":
                print(f"  [FAIL] {i['name']} — {i['detail']}")

    def to_json(self) -> list[dict]:
        return self.items


# 场景 → 客户端观测预期错误码白名单("--allow-error" 追加)。
# queued 容两种 503:SCOPE_FULL=会话闸门满;NO_POD_AVAILABLE=快失败改造后的
# 超限粗化码(max_pods=⌈sc/pc⌉ 打满部署不下去,见 2026-09-scope-full-fastfail)。
# mixed 容 503/NO_POD_AVAILABLE:连续 refresh 的多代 Pod 在 pod_ttl 回收前
# 堆积,叠加 churn 翻转 scope_concurrency(max_pods=⌈sc/pc⌉ 随之变化),
# 存量会话重放置会撞 max_pods 硬闸门——容量语义而非缺陷;出现会记 WARN
# 观测计数,其它错误码仍 fail。
EXPECTED_ERRORS: dict[str, set[str]] = {
    "queued": {"503/SCOPE_FULL", "503/NO_POD_AVAILABLE"},
    "mixed": {"503/NO_POD_AVAILABLE"},
}


def _print_report(title: str, snap: dict, window_sec: float) -> None:
    rps = round(snap["count"] / window_sec, 1) if window_sec > 0 else 0.0
    print(f"[{title}] n={snap['count']} rps={rps} "
          f"p50={snap['p50']}ms p90={snap['p90']}ms p99={snap['p99']}ms "
          f"max={snap['max']}ms")
    if snap.get("errors"):
        print(f"[{title}] errors: {snap['errors']}")
    if snap.get("transport_errors"):
        print(f"[{title}] transport errors: {snap['transport_errors']}")


def _window(stats: Stats, last_count: int) -> dict:
    """取自 last_count 之后的增量窗口统计。"""
    lat = stats.latencies[last_count:]
    snap = Stats._snapshot_of(lat)
    snap["errors"], snap["transport_errors"] = {}, {}
    return snap


# ---------------------------------------------------------------- ERROR 日志感知


class FileLogSource:
    """宿主机 deploy_replicas.sh 形态:logs/replica-<port>.log 字节偏移增量。
    文件变小(重启/轮转)则从头扫并标注——精确判定面。"""

    kind = "file"

    def __init__(self, paths: list[Path]) -> None:
        self.paths = paths
        self._offsets: dict[str, int] = {}
        self.notes: list[str] = []

    def describe(self) -> str:
        return ",".join(str(p) for p in self.paths)

    async def snapshot(self) -> None:
        for p in self.paths:
            try:
                self._offsets[str(p)] = p.stat().st_size
            except OSError:
                self._offsets[str(p)] = -1

    async def collect(self) -> list[str]:
        lines: list[str] = []
        for p in self.paths:
            off = self._offsets.get(str(p), -1)
            try:
                if not p.exists():
                    if off >= 0:
                        self.notes.append(f"{p.name}: 文件消失(副本停止?)")
                    continue
                size = p.stat().st_size
                if off < 0:
                    off = 0
                elif size < off:
                    self.notes.append(f"{p.name}: 文件变小({off}→{size}B),"
                                      f"已从头扫(疑似重启/轮转)")
                    off = 0
                with p.open("r", errors="replace") as f:
                    f.seek(off)
                    chunk = f.read()
                lines.extend(
                    l for l in chunk.splitlines() if " - ERROR - " in l)
            except OSError as exc:
                self.notes.append(f"{p.name}: 读取失败 {type(exc).__name__}")
        return lines


class KubectlLogSource:
    """K8s 形态:stdout-only → 只读 kubectl logs --since-time。
    Pod 重启后旧容器日志丢失(--since-time 只盖当前容器)——尽力源。"""

    kind = "kubectl"

    def __init__(self, namespace: str, selector: str) -> None:
        self.namespace = namespace
        self.selector = selector
        self.since = ""
        self.notes: list[str] = []

    def describe(self) -> str:
        return f"kubectl -n {self.namespace} -l {self.selector}"

    async def snapshot(self) -> None:
        self.since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    async def collect(self) -> list[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "kubectl", "logs", "-n", self.namespace, "-l", self.selector,
                "--since-time", self.since, "--timestamps",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            out, _ = await proc.communicate()
        except FileNotFoundError:
            self.notes.append("kubectl 不在 PATH")
            return []
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"kubectl 执行失败 {type(exc).__name__}: {exc}")
            return []
        text = out.decode(errors="replace") if out else ""
        if proc.returncode != 0:
            self.notes.append(f"kubectl 退出码 {proc.returncode}: "
                              f"{text.strip()[:200]}")
            return []
        return [l for l in text.splitlines() if " - ERROR - " in l]


def discover_log_sources(args) -> tuple[list, list[str]]:
    """按 --log-source 解析日志源;返回 (sources, notes)。"""
    notes: list[str] = []
    if args.log_source == "none":
        return [], ["日志感知关闭(--log-source none)"]
    if args.log_source == "file":
        paths = [Path(x) for x in args.log_file or []]
        return [FileLogSource(paths)], notes
    if args.log_source == "kubectl":
        if not shutil.which("kubectl"):
            return [], ["kubectl 不在 PATH,K8s 日志源不可用"]
        return [KubectlLogSource(args.log_namespace, args.log_selector)], notes
    # auto:--log-file > .replicas.json 文件 > kubectl
    app_dir = Path(__file__).resolve().parents[1]
    if args.log_file:
        return [FileLogSource([Path(x) for x in args.log_file])], notes
    manifest = app_dir / ".replicas.json"
    if manifest.exists():
        try:
            urls = json.loads(manifest.read_text()).get("base_urls") or []
            paths = [app_dir / "logs" / f"replica-{urlparse(u).port}.log"
                     for u in urls if urlparse(u).port]
            paths = [p for p in paths if p.exists()]
            if paths:
                return [FileLogSource(paths)], [
                    f"auto:发现 {manifest.name} → {len(paths)} 个副本日志文件"]
            notes.append(f"auto:{manifest.name} 存在但无对应日志文件")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"auto:解析 {manifest.name} 失败 {type(exc).__name__}")
    if shutil.which("kubectl"):
        return [KubectlLogSource(args.log_namespace, args.log_selector)], [
            "auto:无本地副本清单,回退 kubectl 日志源(尽力)"]
    return [], ["auto:既无 .replicas.json/日志文件也无 kubectl,日志感知降级为 none"]


def _filter_error_lines(lines: list[str], allow_patterns: list[re.Pattern],
                        scope: str, run_id: str) -> tuple[list[str], list[str]]:
    counted: list[str] = []
    allowed: list[str] = []
    for l in lines:
        if any(p.search(l) for p in allow_patterns):
            allowed.append(l)
            continue
        if scope == "run" and run_id not in l:
            continue
        counted.append(l)
    return counted, allowed


# ---------------------------------------------------------------- config 面控制器(单发射者)


async def churn_controller(client: httpx.AsyncClient, base: str, run: str,
                           args, vis: VisClient, checks: CheckRecorder,
                           events: list[dict], lock: asyncio.Lock,
                           seed_ctx: dict, ver_box: list, stop_at: float) -> None:
    """热更新控制器:每 --churn-interval 秒一次 config_sync,在 CHURN_STATES
    两态间翻转 B 类参数(同 id 仅参数变)。持锁含传播断言期——mixed 下防
    refresh 控制器插队自造 409。"""
    n = 0
    next_at = time.monotonic() + args.churn_interval
    while time.monotonic() < stop_at:
        wait = next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        if time.monotonic() >= stop_at:
            break
        n += 1
        params = dict(seed_ctx["params"])
        params.update(CHURN_STATES[n % 2])
        exp_sc, exp_ttl = params["scope_concurrency"], params["pod_ttl"]
        env = _envelope("config_sync", f"churn-{run}-{n}", None, f"grp-{run}-0")
        env["rawdata"] = _seed_payload(run, args, params)
        async with lock:
            t0 = time.monotonic()
            status, raw, body = await _post(
                client, f"{base}/config_sync", env, args.timeout)
            dt = (time.monotonic() - t0) * 1000
            ok = status == 200 and bool(raw.get("ok"))
            events.append({"kind": "sync", "seq": n, "t_start": t0,
                           "t_end": time.monotonic(), "duration_ms": round(dt, 1),
                           "ok": ok, "status": status})
            busy = status == 409 and body.get("error_code") == "CONFIG_SYNC_BUSY"
            checks.record(f"churn#{n} config_sync 200", ok,
                          f"status={status} {body.get('error_code') or ''} "
                          f"{dt:.0f}ms",
                          severity="warn" if busy else "fail")
            if not ok:
                next_at += args.churn_interval
                continue
            seeded_ids = {s["scope_id"] for s in seed_ctx["scopes"]}
            affected = set(raw.get("affected_scopes") or [])
            checks.record(f"churn#{n} affected_scopes 覆盖播种 scope",
                          bool(affected) and seeded_ids <= affected,
                          f"affected={sorted(affected)[:4]}")
            sid0 = seed_ctx["scopes"][0]["scope_id"]
            _, probe_body = await vis.get(
                f"/visualization/scope?scope_id={sid0}")
            has_capacity = "capacity" in (_dig(probe_body, "sm",
                                               default={}) or {})
            if has_capacity:
                # 首选 /visualization/scope 的 sm.capacity(快照模板派生闸门)
                prop_path = f"/visualization/scope?scope_id={sid0}"

                def prop_pred(c, exp_sc=exp_sc, exp_ttl=exp_ttl) -> bool:
                    return (_as_int(_dig(c, "sm", "capacity",
                                         "scope_concurrency")) == exp_sc
                            and _as_int(_dig(c, "sm", "capacity",
                                             "pod_ttl")) == exp_ttl)
                how = "capacity"
            else:
                # 目标 runtime 旧于 sm.capacity 字段(2026-09 观测补齐前):
                # 回退 /visualization/config 模板参数(DB 视角),一次性 warn
                if not getattr(churn_controller, "_warned_old_runtime", False):
                    checks.record(
                        "目标 runtime 无 sm.capacity 字段(版本过旧),"
                        "传播断言回退 /visualization/config 模板参数",
                        True, "", severity="warn")
                    churn_controller._warned_old_runtime = True
                prop_path = "/visualization/config"
                tpl_id0 = seed_ctx["scopes"][0]["template_id"]

                def prop_pred(c, exp_sc=exp_sc, exp_ttl=exp_ttl,
                              tpl_id0=tpl_id0) -> bool:
                    t = next((t for t in c.get("templates") or []
                              if t.get("template_id") == tpl_id0), None)
                    return (t is not None
                            and _as_int(t.get("scope_concurrency")) == exp_sc
                            and _as_int(t.get("pod_ttl")) == exp_ttl)
                how = "config 模板"
            prop = await vis.poll(prop_path, prop_pred,
                                  timeout=args.churn_propagate_timeout,
                                  interval=2)
            checks.record(f"churn#{n} 热更新传播({how} 新值可见)", prop,
                          f"{args.churn_propagate_timeout}s 内确认 "
                          f"scope_concurrency={exp_sc} pod_ttl={exp_ttl}")
            _, cfgb = await vis.get("/visualization/config")
            new_ver = _dig(cfgb, "routing_snapshot", "ver")
            checks.record(f"churn#{n} 路由快照 ver 递增",
                          _as_int(new_ver, -1) > _as_int(ver_box[0], -2),
                          f"ver {ver_box[0]}→{new_ver}")
            if _as_int(new_ver) is not None:
                ver_box[0] = new_ver
        next_at += args.churn_interval


async def refresh_controller(client: httpx.AsyncClient, base: str, run: str,
                             args, vis: VisClient, checks: CheckRecorder,
                             events: list[dict], lock: asyncio.Lock,
                             seed_ctx: dict, affinity: AffinityTracker,
                             cold_log: list[dict], stop_at: float) -> None:
    """强制刷新控制器:每 --refresh-interval 秒一次 config_refresh。
    断言:200 全 scope 覆盖 / generations 单调 +1(响应与 vis 双源)/
    存量会话亲和(被动)/ 新代暖 Pod 重建收敛 / 冷启动探测。"""
    scope_ids = [s["scope_id"] for s in seed_ctx["scopes"]]
    prev: dict[str, int] = {}
    for sid in scope_ids:
        _, b = await vis.get(f"/visualization/scope?scope_id={sid}")
        prev[sid] = _as_int(_dig(b, "rm", "scope_config", "generation"), 0)
    n = 0
    next_at = time.monotonic() + args.refresh_interval
    while time.monotonic() < stop_at:
        wait = next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        if time.monotonic() >= stop_at:
            break
        n += 1
        env = _envelope("config_refresh", f"refresh-{run}-{n}", None,
                        f"grp-{run}-0")
        async with lock:
            t0 = time.monotonic()
            status, raw, body = await _post(
                client, f"{base}/config_refresh", env, args.timeout)
            dt = (time.monotonic() - t0) * 1000
            gens = {k: _as_int(v, -1)
                    for k, v in (raw.get("generations") or {}).items()}
            ok = status == 200 and bool(raw.get("ok"))
            events.append({"kind": "refresh", "seq": n, "t_start": t0,
                           "t_end": time.monotonic(), "duration_ms": round(dt, 1),
                           "ok": ok, "status": status,
                           "generations": gens})
            checks.record(f"refresh#{n} config_refresh 200 且全 scope 覆盖",
                          ok and set(scope_ids) <= set(gens),
                          f"status={status} scopes_refreshed="
                          f"{raw.get('scopes_refreshed')} pods_sunset="
                          f"{raw.get('pods_sunset')}")
            if not ok:
                next_at += args.refresh_interval
                continue
            mono = all(gens.get(s, -1) == prev.get(s, 0) + 1 for s in scope_ids)
            checks.record(f"refresh#{n} generations 单调 +1", mono,
                          f"prev={ {s: prev.get(s, 0) for s in scope_ids} }"
                          f"→{gens}")
            prev = gens
            sid0 = scope_ids[0]
            want0 = gens.get(sid0, -1)
            xok = await vis.poll(
                f"/visualization/scope?scope_id={sid0}",
                lambda c: _as_int(
                    _dig(c, "rm", "scope_config", "generation")) == want0,
                timeout=15, interval=2)
            checks.record(f"refresh#{n} vis 代次与返回一致", xok,
                          f"期望 {sid0} generation={want0}")
            cp = affinity.checkpoint()
            # 冷启动探测:一次性新会话(候选集已清空 → 必走新代部署)。
            # 503 属已知容量语义(连续刷新时老代未回收顶满 max_pods →
            # NO_POD_AVAILABLE;或部署超时)——记 warn 观测,不判败;
            # 重建收敛后的暖探测才是硬断言。
            for i in range(max(args.refresh_cold_probes, 0)):
                sid = f"sess-{run}-cold-{n}-{i}"
                penv = _envelope("route", f"{run}-cold-{n}-{i}", sid,
                                 f"grp-{run}-0")
                pt0 = time.monotonic()
                pst, praw, _ = await _post(
                    client, f"{base}/route", penv, args.timeout)
                cold_log.append(
                    {"refresh_seq": n, "session_id": sid,
                     "latency_ms": round((time.monotonic() - pt0) * 1000, 1),
                     "status": pst, "pod_id": praw.get("pod_id"),
                     "error_code": praw.get("error_code")})
            # 新代暖 Pod 重建收敛(min_idle_pods=1 保证 autoscale 预热)
            rebuilt = True
            for sid in scope_ids:
                want = gens.get(sid, -1)
                rok = await vis.poll(
                    f"/visualization/scope?scope_id={sid}",
                    lambda c, want=want: any(
                        _as_int(p.get("generation")) == want
                        for p in (_dig(c, "rm", "pods", default=[]) or [])),
                    timeout=args.refresh_rebuild_budget, interval=3)
                rebuilt = rebuilt and rok
            checks.record(
                f"refresh#{n} 新代暖 Pod 重建收敛(≤{args.refresh_rebuild_budget}s)",
                rebuilt,
                f"目标代次={ {s: gens.get(s) for s in scope_ids} }")
            if rebuilt:
                # 暖探测:重建完成后新会话必须 200(命中 min_idle 暖 Pod)
                wenv = _envelope("route", f"{run}-warm-{n}", f"sess-{run}-warm-{n}",
                                 f"grp-{run}-0")
                wst, wraw, _ = await _post(client, f"{base}/route", wenv,
                                           args.timeout)
                checks.record(f"refresh#{n} 重建后新会话可达(暖探测 200)",
                              wst == 200,
                              f"status={wst} {wraw.get('error_code') or ''} "
                              f"pod={wraw.get('pod_id')}")
        violations = affinity.violations_since(cp)
        checks.record(f"refresh#{n} 存量会话亲和保持(同 session 回同 pod)",
                      not violations,
                      f"{len(violations)} 次换 pod"
                      + (f": {violations[:3]}" if violations else ""))
        next_at += args.refresh_interval


# ---------------------------------------------------------------- 流量 worker


def _pick_msg_type(scenario: str, si: int, seq: int, args) -> str:
    """route_touch/mixed:一半请求对已建立会话做保活(touch)。"""
    if scenario in ("route_touch", "mixed") and si % 2 == 0 \
            and seq > args.groups * args.sessions_per_group // max(args.concurrency, 1):
        return "touch"
    return "route"


async def _traffic_worker(wid: int, client: httpx.AsyncClient, base: str,
                          run: str, args, stats: Stats, affinity: AffinityTracker,
                          bucket: _TokenBucket | None, stop_at: float,
                          warmup_until: float) -> None:
    groups = [f"grp-{run}-{gi}" for gi in range(args.groups)]
    sessions = {(gi, si): f"sess-{run}-{gi}-{si}"
                for gi in range(args.groups)
                for si in range(args.sessions_per_group)}
    keys = list(sessions)
    rng = random.Random(1000 + wid)
    seq = 0

    while time.monotonic() < stop_at:
        if bucket is not None:
            await bucket.take()
        seq += 1
        key = rng.choice(keys)
        gi, si = key
        session_id, group_id = sessions[key], groups[gi]
        msg_type = _pick_msg_type(args.scenario, si, seq, args)
        env = _envelope(msg_type, f"{run}-w{wid}-{seq}", session_id, group_id)
        t0 = time.monotonic()
        try:
            status, rawdata, body = await _post(
                client, f"{base}/{msg_type}", env, args.timeout)
        except Exception as exc:  # noqa: BLE001 - 传输层错误计数不中断
            stats.record_transport_error(type(exc).__name__)
            continue
        dt = (time.monotonic() - t0) * 1000
        if time.monotonic() >= warmup_until:
            stats.record(dt, status, body.get("error_code"),
                         endpoint=msg_type)
            if msg_type == "route" and status == 200:
                affinity.note(session_id, rawdata.get("pod_id"))


# ---------------------------------------------------------------- 收尾巡检


async def post_run_sweep(vis: VisClient, checks: CheckRecorder, args,
                         whitelist: set[str], run_start_wall: float,
                         stats: Stats, run: str) -> None:
    """流量停止 + 静置后的服务侧巡检(全走可视化只读端点)。"""
    # 1) 静息 deploying 收敛(收敛式轮询,仿 e2e 阶段 11b;2026-09 快失败
    #    改造后无等待队列,waiters 字段已不存在)
    conv = await vis.poll(
        "/visualization/scopes?limit=500",
        lambda c: all((r.get("deploying") or 0) == 0
                      for r in c.get("scopes") or []),
        timeout=60, interval=3)
    checks.record("巡检:静息 deploying 收敛(60s)", conv,
                  "全部 scope 的 deploying==0")

    # 2) recent_errors 无白名单外错误码(多实例聚合采样)。本 run 的冷/暖
    #    探测请求(request_id 前缀)剔除——冷探测的 503 是刷新窗口已知容量
    #    语义,由专属 check 观测;流量面错误不放松。
    agg = await vis.recent_errors_aggregated(samples=6, limit=200)
    wl_codes = {k.split("/", 1)[1] for k in whitelist}
    probe_prefixes = (f"{run}-cold-", f"{run}-warm-")
    in_window = [e for e in agg
                 if _as_float(e.get("ts"), 0.0) >= run_start_wall - 5]
    unexpected = [e for e in in_window
                  if (e.get("error_code") or "-") not in wl_codes
                  and not str(e.get("request_id") or "").startswith(probe_prefixes)]
    probe_errs = [e for e in in_window
                  if str(e.get("request_id") or "").startswith(probe_prefixes)]
    instances = {e.get("instance_id") for e in agg}
    checks.record(
        "巡检:recent_errors 无白名单外错误码", not unexpected,
        f"采样实例={len(instances)} 窗口内={len(in_window)} "
        f"违规={len(unexpected)}(另有冷/暖探测 {len(probe_errs)} 条已剔除,"
        f"由专属 check 观测)"
        + (f": {[(e.get('endpoint'), e.get('error_code')) for e in unexpected[:3]]}"
           if unexpected else ""))

    # 3) stats 端点错误码交叉验证(实例视角,只 warn):客户端观测 ⊆ 服务端
    server_codes: set[str] = set()
    for _ in range(6):
        _, s = await vis.get("/visualization/stats", close=True)
        for st in (s.get("endpoints") or {}).values():
            server_codes.update((st.get("by_error_code") or {}).keys())
    client_codes = {k.split("/", 1)[1] if "/" in k else k for k in stats.errors}
    missing = {c for c in client_codes if c not in server_codes}
    checks.record("巡检:客户端错误码在服务端 stats 可见(交叉验证)",
                  not missing,
                  f"客户端={sorted(client_codes)} 服务端采样={sorted(server_codes)} "
                  f"缺失={sorted(missing)}", severity="warn")

    # 4) 自评估无 critical findings(latest=null 属正常 → skip)
    _, ev = await vis.get("/visualization/evaluation?limit=1")
    latest = ev.get("latest")
    if not latest:
        checks.record("巡检:自评估报告", True,
                      "latest=null(评估间隔未到/未产出)", severity="skip")
    else:
        crit = [f for f in latest.get("findings") or []
                if f.get("severity") == "critical"]
        checks.record("巡检:自评估无 critical findings", not crit,
                      f"{len(crit)} 条 critical: "
                      + "; ".join(str(f.get("rule") or f.get("title") or f)[:60]
                                  for f in crit[:3]),
                      severity="warn")


# ---------------------------------------------------------------- 主流程


async def main() -> int:
    args = _parse_args()
    base = args.base_url.rstrip("/")
    root = base.rsplit("/api/session", 1)[0]
    run = "load-" + time.strftime("%m%d%H%M%S") + "-" + "".join(
        random.choices(string.ascii_lowercase, k=4))

    print(f"[load] run={run} scenario={args.scenario} concurrency={args.concurrency} "
          f"rps={args.rps or 'closed-loop'} duration={args.duration}s "
          f"warmup={args.warmup}s target={base}")
    print("[load] 注意:queued 场景的 503 SCOPE_FULL 属预期容量满快失败路径;"
          "只报告不判败")

    checks = CheckRecorder()
    stats = Stats()
    affinity = AffinityTracker()
    cold_log: list[dict] = []
    events: list[dict] = []
    whitelist = EXPECTED_ERRORS.get(args.scenario, set()) | set(args.allow_error or [])

    log_sources, log_notes = discover_log_sources(args)
    for note in log_notes:
        print(f"[log] {note}")
    allow_patterns = [re.compile(p) for p in (args.log_error_allow or [])]
    run_start_wall = time.time()

    log_report: dict = {"sources": [], "total_counted": 0, "allowlisted": 0,
                        "samples": []}

    async with httpx.AsyncClient() as client:
        # 上线检查
        try:
            probe = await client.get(f"{root}/healthz", timeout=10)
            probe.raise_for_status()
            print(f"[load] target online: {probe.json()}")
        except Exception as exc:  # noqa: BLE001
            print(f"[load] target offline: {type(exc).__name__}: {exc}")
            return 2

        # 日志起始快照(seed 之前——播种失败也在感知窗口内)
        for src in log_sources:
            await src.snapshot()

        vis = VisClient(client, base)
        seed_ctx: dict | None = None
        if not args.no_seed:
            seed_ctx = await _seed(client, base, run, args)
            tpl_ids = sorted({s["template_id"] for s in seed_ctx["scopes"]})
            print(f"[seed] {len(seed_ctx['scopes'])} scopes → templates {tpl_ids}")
            if args.scenario in CONFIG_SCENARIOS:
                print(f"[seed] 模板参数: {seed_ctx['params']}")

        _, cfgb = await vis.get("/visualization/config")
        ver_box = [_dig(cfgb, "routing_snapshot", "ver")]
        print(f"[vis] 路由快照基线 ver={ver_box[0]}")

        bucket = _TokenBucket(args.rps) if args.rps > 0 else None
        t_start = time.monotonic()
        warmup_until = t_start + args.warmup
        stop_at = t_start + args.duration
        tasks = [asyncio.create_task(
            _traffic_worker(i, client, base, run, args, stats, affinity,
                            bucket, stop_at, warmup_until))
            for i in range(args.concurrency)]
        lock = asyncio.Lock()
        if args.scenario in ("config_churn", "mixed"):
            tasks.append(asyncio.create_task(
                churn_controller(client, base, run, args, vis, checks, events,
                                 lock, seed_ctx, ver_box, stop_at)))
        if args.scenario in ("config_refresh", "mixed"):
            tasks.append(asyncio.create_task(
                refresh_controller(client, base, run, args, vis, checks,
                                   events, lock, seed_ctx, affinity,
                                   cold_log, stop_at)))

        # 浸泡周期报告(增量窗口)
        last_count, last_t = 0, time.monotonic()
        try:
            while any(not t.done() for t in tasks):
                await asyncio.sleep(min(args.report_interval, 1.0))
                now = time.monotonic()
                if args.report_interval > 0 and now - last_t >= args.report_interval \
                        and now > warmup_until:
                    snap = _window(stats, last_count)
                    _print_report("soak", snap, now - last_t)
                    last_count, last_t = len(stats.latencies), now
        except KeyboardInterrupt:
            print("\n[load] Ctrl-C:等待 task 收尾后输出部分报告…")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = max(time.monotonic() - warmup_until, 1e-6)

        if args.cleanup == "config" and seed_ctx:
            tpl_ids = sorted({s["template_id"] for s in seed_ctx["scopes"]})
            await _cleanup_config(client, base, run, seed_ctx["scopes"], tpl_ids)

        # 场景收尾判定(客户端侧)
        unexpected = stats.unexpected_errors(whitelist)
        checks.record("无非预期错误码(客户端直方图)", not unexpected,
                      f"白名单={sorted(whitelist)} 超出={unexpected}")
        for code, cnt in stats.errors.items():
            if code in whitelist:
                checks.record(f"预期内错误码 {code} 出现(容量语义观测)",
                              False,
                              f"{cnt} 次 / {stats.total} 请求"
                              f"({cnt / max(stats.total, 1):.2%})",
                              severity="warn")

        if cold_log:
            lats = sorted(c["latency_ms"] for c in cold_log)
            cold_errs = [c for c in cold_log if c["status"] != 200]
            # 冷探测非 200 属刷新窗口已知容量语义(max_pods 顶满/部署超时),
            # warn 观测;重建后暖探测(fail 级)兜底"恢复服务"断言
            checks.record("refresh 冷启动探测无意外错误码(503=容量语义,见 warn)",
                          all(c["status"] == 503 for c in cold_errs),
                          f"非 200 且非 503: "
                          f"{[(c['session_id'], c['status']) for c in cold_errs if c['status'] != 503][:3]}",
                          severity="warn" if cold_errs else "fail")
            ok_lats = [c["latency_ms"] for c in cold_log if c["status"] == 200]
            if ok_lats:
                detail = (f"成功 n={len(ok_lats)} p50={sorted(ok_lats)[len(ok_lats) // 2]:.0f}ms "
                          f"max={max(ok_lats):.0f}ms;503×{len(cold_errs)} "
                          f"预算={args.refresh_cold_budget_ms:.0f}ms")
                if args.refresh_cold_budget_ms > 0:
                    checks.record("refresh 冷启动延迟 ≤ 预算",
                                  max(ok_lats) <= args.refresh_cold_budget_ms,
                                  detail, severity="warn")
                else:
                    print(f"[cold-start] {detail}")

        sync_events = [e for e in events if e["kind"] == "sync" and e.get("ok")]
        if sync_events:
            # 窗口只罩 sync 请求本身(锁内写库+推 RM 都在响应前完成);
            # 尾巴过长会把整个 run 摊进窗口,基线恒空
            windows = [(e["t_start"] - 1, e["t_end"] + 2) for e in sync_events]
            win_p99s = [p for p in (stats.snapshot_window(a, b)["p99"]
                                    for a, b in windows) if p > 0]
            base_snap = stats.snapshot_complement(windows)
            if win_p99s and base_snap["count"]:
                delta = max(win_p99s) - base_snap["p99"]
                detail = (f"窗口 p99={max(win_p99s)}ms 基线 p99={base_snap['p99']}ms "
                          f"Δ={delta:.1f}ms (n={len(win_p99s)})")
                if args.churn_jitter_budget_ms > 0:
                    checks.record(
                        "churn sync 窗口 route p99 抖动 ≤ 预算",
                        delta <= args.churn_jitter_budget_ms, detail,
                        severity="fail" if args.churn_jitter_fail else "warn")
                else:
                    print(f"[churn-jitter] {detail}")
            elif args.scenario in ("config_churn", "mixed"):
                print(f"[churn-jitter] 窗口/基线样本不足"
                      f"(窗口命中={len(win_p99s)} 基线 n={base_snap['count']})")

        # 静置 + 收尾巡检
        if args.settle > 0:
            print(f"[load] 静置 {args.settle}s(容忍 stats 5s 批量 flush)…")
            await asyncio.sleep(args.settle)
        if not args.no_postcheck:
            await post_run_sweep(vis, checks, args, whitelist,
                                 run_start_wall, stats, run)

        # ERROR 日志增量判定(巡检之后——巡检期错误也入窗)
        for src in log_sources:
            lines = await src.collect()
            counted, allowed = _filter_error_lines(
                lines, allow_patterns, args.log_scope, run)
            log_report["sources"].append(
                {"kind": src.kind, "detail": src.describe(),
                 "lines": len(lines), "counted": len(counted),
                 "allowed": len(allowed), "notes": src.notes})
            log_report["total_counted"] += len(counted)
            log_report["allowlisted"] += len(allowed)
            log_report["samples"].extend(
                f"[{src.kind}] {l[:400]}" for l in counted[:args.log_sample])
            print(f"[error-log] {src.kind}:{src.describe()} "
                  f"ERROR 行={len(lines)} 计入={len(counted)} "
                  f"白名单={len(allowed)}")
            for note in src.notes:
                print(f"[error-log]   注: {note}")
        checks.record(f"ERROR 日志增量 ≤ {args.log_error_max}",
                      log_report["total_counted"] <= args.log_error_max,
                      f"counted={log_report['total_counted']} "
                      f"allowlisted={log_report['allowlisted']} "
                      f"源={len(log_sources)}")
        for s in log_report["samples"][:args.log_sample]:
            print(f"  {s}")

    # ---------------- 报告与退出码
    snap = stats.snapshot()
    _print_report("final", snap, elapsed)
    n_sync = sum(1 for e in events if e["kind"] == "sync")
    n_refresh = sum(1 for e in events if e["kind"] == "refresh")
    if events:
        recent = ", ".join(
            f"{e['kind']}#{e['seq']} {e['duration_ms']:.0f}ms"
            f"{'ok' if e['ok'] else 'FAIL'}" for e in events[-3:])
        print(f"[config] sync×{n_sync} refresh×{n_refresh} (最近: {recent})")
    checks.summary()

    transport_rate = (stats.transport_total /
                      max(stats.transport_total + stats.total, 1))
    transport_fail = transport_rate > args.max_error_rate
    if transport_fail:
        print(f"[load] FAIL: transport error rate {transport_rate:.1%} > "
              f"{args.max_error_rate:.0%}")

    if args.json:
        print(json.dumps(
            {"run": run, "scenario": args.scenario,
             "elapsed_sec": round(elapsed, 1), **snap,
             "checks": checks.to_json(),
             "error_logs": log_report,
             "config_events": [{k: v for k, v in e.items()
                                if k not in ("t_start", "t_end")}
                               for e in events],
             "affinity_violations": [list(v) for v in affinity.all_violations()],
             "cold_start": cold_log},
            ensure_ascii=False))
    print("[load] done")
    if checks.failed() or transport_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
