#!/usr/bin/env python
# coding: utf-8
"""agent-runtime 场景化压测 / 浸泡工具（asyncio + httpx，零额外依赖）。

目标：单入口（LB 或单实例）打 route/touch 混合负载，输出延迟分位数 /
吞吐 / 错误码分布；长时 --duration 即浸泡（周期增量报告）。

安全边界（红线）：
- 全程只经 HTTP API，不碰 Redis/DB/K8s；
- 不 FLUSHDB、默认不调 cleanup 端点（它会删目标 namespace 下全部
  AgentServer Pod）——模板/规则/会话按 run-id 命名空间化，靠 TTL 老化；
- queued 场景错误直方图中出现 503 SCOPE_FULL 属预期（容量满快失败路径被
  刻意打到），只报告不判败。

用法示例：
  uv run --no-sync python scripts/load_test.py \
      --base-url http://127.0.0.1:30091/api/session --duration 60 \
      --scenario route_touch --concurrency 8 --rps 50
  # 浸泡：--duration 3600 --report-interval 300
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import string
import sys
import time
from collections import Counter, deque

import httpx

# ---------------------------------------------------------------- 参数


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="agent-runtime 场景化压测/浸泡")
    p.add_argument("--base-url", default="http://127.0.0.1:8091/api/session",
                   help="单入口（LB 或实例），含 /api/session 前缀")
    p.add_argument("--scenario", choices=["route", "route_touch", "queued"],
                   default="route",
                   help="route=纯路由；route_touch=路由+保活；queued=小容量模板刻意打满（503 快失败路径）")
    p.add_argument("--concurrency", type=int, default=4, help="并发 worker 数")
    p.add_argument("--rps", type=float, default=0,
                   help="目标速率（开环令牌桶）；0=闭环（各 worker 全速）")
    p.add_argument("--duration", type=float, default=30,
                   help="压测时长秒（浸泡即调大）")
    p.add_argument("--warmup", type=float, default=3,
                   help="预热秒数（不计入统计）")
    p.add_argument("--report-interval", type=float, default=10,
                   help="浸泡周期报告间隔秒")
    p.add_argument("--groups", type=int, default=4, help="并发 scope（group）数")
    p.add_argument("--sessions-per-group", type=int, default=8,
                   help="每 scope 的会话数（route_touch 循环 touch 这些会话）")
    p.add_argument("--scope-concurrency", type=int, default=2,
                   help="queued 场景模板 scope_concurrency（刻意小）")
    p.add_argument("--pod-concurrency", type=int, default=2,
                   help="queued 场景模板 pod_concurrency")
    p.add_argument("--no-seed", action="store_true",
                   help="不播种模板（要求目标已有可匹配的路由规则）")
    p.add_argument("--namespace", default="agent-runtime-e2e",
                   help="AgentServer Pod 拉起的 namespace（须已存在）")
    p.add_argument("--cleanup", choices=["none", "config"], default="config",
                   help="结束时删除本次 run 的模板/规则（默认）；none=留待 TTL")
    p.add_argument("--timeout", type=float, default=90.0, help="单请求超时秒")
    p.add_argument("--max-error-rate", type=float, default=1.0,
                   help="传输层错误率超过此值退出码 1（默认 1.0=永不因业务错误判败）")
    p.add_argument("--json", action="store_true", help="末尾输出机器可读 JSON 块")
    return p.parse_args()


# ---------------------------------------------------------------- 信封与播种


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
        # influxdb:1.8 的 /health 在 8086（e2e 同款替代 AgentServer）
        "ports": [{"name": "sse", "containerPort": 8086}],
    }


def _template(main_cid, scope_cc, pod_cc, namespace):
    return {
        "main_container_id": main_cid,
        "namespace": namespace,
        "scope_concurrency": scope_cc,
        "pod_concurrency": pod_cc,
        "session_ttl": 120,
        "pod_ttl": 120,
        "min_idle_pods": 0,
        "ready_timeout": 60,
    }


# ---------------------------------------------------------------- 统计


class Stats:
    def __init__(self) -> None:
        self.latencies: list[float] = []          # 毫秒
        self.errors: Counter[str] = Counter()     # "{status}/{error_code}"
        self.transport_errors: Counter[str] = Counter()
        self.total = 0
        self.transport_total = 0

    def record(self, latency_ms: float, status: int, error_code: str | None) -> None:
        self.total += 1
        self.latencies.append(latency_ms)
        if status != 200:
            self.errors[f"{status}/{error_code or '-'}"] += 1

    def record_transport_error(self, kind: str) -> None:
        self.transport_total += 1
        self.transport_errors[kind] += 1

    def snapshot(self) -> dict:
        lat = sorted(self.latencies)
        n = len(lat)

        def pct(q: float) -> float:
            return round(lat[min(n - 1, int(q * n))], 1) if n else 0.0

        return {
            "count": n,
            "p50": pct(0.50), "p90": pct(0.90), "p99": pct(0.99),
            "max": round(lat[-1], 1) if lat else 0.0,
            "errors": dict(self.errors),
            "transport_errors": dict(self.transport_errors),
        }


def _print_report(title: str, snap: dict, window_sec: float) -> None:
    rps = round(snap["count"] / window_sec, 1) if window_sec > 0 else 0.0
    print(f"[{title}] n={snap['count']} rps={rps} "
          f"p50={snap['p50']}ms p90={snap['p90']}ms p99={snap['p99']}ms "
          f"max={snap['max']}ms")
    if snap["errors"]:
        print(f"[{title}] errors: {snap['errors']}")
    if snap["transport_errors"]:
        print(f"[{title}] transport errors: {snap['transport_errors']}")


# ---------------------------------------------------------------- 主流程


async def _seed(client: httpx.AsyncClient, base: str, run: str, args) -> list[dict]:
    """全量下发本次 run 的容器 + 模板 + scope（快照式替换,一次请求;
    容器 id 带 run 前缀,多轮压测不撞唯一约束）。"""
    if args.scenario == "queued":
        tpl_id = f"tpl-{run}-q"
        cid = f"c-{run}-q"
        tpl = _template(cid, args.scope_concurrency, args.pod_concurrency,
                        args.namespace)
    else:
        tpl_id = f"tpl-{run}-a"
        cid = f"c-{run}-a"
        tpl = _template(cid, 50, 10, args.namespace)
    scopes = [
        {"scope_id": f"scope-{run}-{gi}", "index": gi, "template_id": tpl_id,
         "routing_rules": f"group_id in ('grp-{run}-{gi}')"}
        for gi in range(args.groups)
    ]
    r = await client.post(f"{base}/config_sync", json={
        "type": "config_sync",
        "metadata": {"request_id": f"seed-{run}", "session_id": None,
                     "bot_id": "loadbot",
                     "extra": {"group_id": f"grp-{run}-0"}},
        "rawdata": {"containers": [_main_container(cid, "influxdb:1.8")],
                    "templates": [{"template_id": tpl_id, **tpl}],
                    "scopes": scopes},
    }, timeout=args.timeout)
    if r.status_code != 200:
        print(f"[seed] config_sync failed: {r.status_code} {r.text[:200]}")
        sys.exit(2)
    return scopes


async def _cleanup_config(client: httpx.AsyncClient, base: str, run: str,
                          scopes: list[dict], tpl_ids: list[str]) -> None:
    """清掉本次 run 播种的配置。

    播种是快照式全量替换——播种时已清掉历史配置,此刻服务里的配置
    只属于本 run,因此清空全量(templates/scopes 皆空)等价于只删本 run。
    """
    await client.post(f"{base}/config_sync", json={
        "type": "config_sync",
        "metadata": {"request_id": f"clean-{run}", "session_id": None,
                     "bot_id": "loadbot", "extra": {"group_id": "x"}},
        "rawdata": {"containers": [], "templates": [], "scopes": []},
    })
    print(f"[cleanup] 已清空本次 run 的 {len(scopes)} 个 scope / {len(tpl_ids)} 个模板；"
          f"会话与 AgentServer Pod 留待 TTL 老化（不动 cleanup 端点）")


class _TokenBucket:
    """开环速率控制（rps>0 时）：固定间隔发放令牌。"""

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


async def _worker(wid: int, client: httpx.AsyncClient, base: str, run: str,
                  args, stats: Stats, bucket: _TokenBucket | None,
                  stop_at: float, warmup_until: float) -> None:
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
        # route_touch：一半请求对已建立会话做保活
        msg_type = ("touch" if args.scenario == "route_touch" and si % 2 == 0
                    and seq > args.groups * args.sessions_per_group // max(args.concurrency, 1)
                    else "route")
        env = _envelope(msg_type, f"{run}-w{wid}-{seq}", session_id, group_id)
        t0 = time.monotonic()
        try:
            r = await client.post(f"{base}/{msg_type}", json=env,
                                  timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001 - 传输层错误计数不中断
            stats.record_transport_error(type(exc).__name__)
            continue
        dt = (time.monotonic() - t0) * 1000
        if time.monotonic() >= warmup_until:
            body = {}
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                pass
            stats.record(dt, r.status_code, body.get("error_code"))


async def main() -> int:
    args = _parse_args()
    base = args.base_url.rstrip("/")
    run = "load-" + time.strftime("%m%d%H%M%S") + "-" + "".join(
        random.choices(string.ascii_lowercase, k=4))

    print(f"[load] run={run} scenario={args.scenario} concurrency={args.concurrency} "
          f"rps={args.rps or 'closed-loop'} duration={args.duration}s "
          f"warmup={args.warmup}s target={base}")
    print("[load] 注意：queued 场景的 503 SCOPE_FULL 属预期容量满快失败路径；"
          "只报告不判败")

    stats = Stats()
    scopes: list[dict] = []
    tpl_ids: list[str] = []
    async with httpx.AsyncClient() as client:
        # 上线检查
        probe = await client.get(base.rsplit("/api/session", 1)[0] + "/healthz",
                                 timeout=10)
        probe.raise_for_status()
        print(f"[load] target online: {probe.json()}")

        if not args.no_seed:
            scopes = await _seed(client, base, run, args)
            tpl_ids = sorted({s["template_id"] for s in scopes})
            print(f"[seed] {len(scopes)} scopes → templates {tpl_ids}")

        bucket = _TokenBucket(args.rps) if args.rps > 0 else None
        t_start = time.monotonic()
        warmup_until = t_start + args.warmup
        stop_at = t_start + args.duration
        workers = [asyncio.create_task(
            _worker(i, client, base, run, args, stats, bucket, stop_at, warmup_until))
            for i in range(args.concurrency)]

        # 浸泡周期报告（增量窗口）
        last_count, last_t = 0, time.monotonic()
        try:
            while any(not w.done() for w in workers):
                await asyncio.sleep(min(args.report_interval, 1.0))
                now = time.monotonic()
                if args.report_interval > 0 and now - last_t >= args.report_interval \
                        and now > warmup_until:
                    snap = _window(stats, last_count)
                    _print_report("soak", snap, now - last_t)
                    last_count, last_t = len(stats.latencies), now
        except KeyboardInterrupt:
            print("\n[load] Ctrl-C：等待 worker 收尾后输出部分报告…")
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        await asyncio.gather(*workers, return_exceptions=True)
        elapsed = max(time.monotonic() - warmup_until, 1e-6)

        if args.cleanup == "config" and scopes:
            await _cleanup_config(client, base, run, scopes, tpl_ids)

    snap = stats.snapshot()
    _print_report("final", snap, elapsed)
    transport_rate = (stats.transport_total /
                      max(stats.transport_total + stats.total, 1))
    if transport_rate > args.max_error_rate:
        print(f"[load] FAIL: transport error rate {transport_rate:.1%} > "
              f"{args.max_error_rate:.0%}")
        return 1
    if args.json:
        print(json.dumps({"run": run, "elapsed_sec": round(elapsed, 1),
                          **snap}, ensure_ascii=False))
    print("[load] done")
    return 0


def _window(stats: Stats, last_count: int) -> dict:
    """取自 last_count 之后的增量窗口统计。"""
    lat = stats.latencies[last_count:]

    def pct(q: float) -> float:
        if not lat:
            return 0.0
        s = sorted(lat)
        return round(s[min(len(s) - 1, int(q * len(s)))], 1)

    return {
        "count": len(lat), "p50": pct(0.5), "p90": pct(0.9),
        "p99": pct(0.99), "max": round(max(lat), 1) if lat else 0.0,
        "errors": {}, "transport_errors": {},
    }


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
