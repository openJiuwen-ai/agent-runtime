# coding: utf-8
"""e2e 验收脚本公共件（e2e_hld_acceptance.py / e2e_multi_replica.py 共用）。

从 M6 的 e2e_hld_acceptance.py 抽取：结果收集（check/skip）、信封构造、
HTTP Client、kubectl 子进程、FLUSHDB 防误刷守卫。改本文件语义（尤其
redis_guard 的前缀白名单）必须同步两个使用方的行为预期。
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import redis.asyncio as aioredis

RESULTS: list[tuple[str, bool, str]] = []

# 防误刷：目标 DB 只允许本服务前缀
# （session_manager:/resource_manager: 业务键 + agent_runtime:job:* 框架选主键）
OWN_PREFIXES = ("session_manager:", "resource_manager:", "agent_runtime:")

# 播种的通配兜底 scope_id（scope 由 config_sync 下发,不再由 (group,bot) 派生）
SEED_SCOPE = "e2e-main-scope"


def scope_id(group: str, bot: str) -> str:
    """播种的兜底 scope_id（通配,任意 group/bot 命中它）。参数仅为兼容旧签名。"""
    return SEED_SCOPE


def config_sync_payload(templates: list[dict],
                        scopes: list[dict] | None = None) -> dict:
    """构造 config_sync 全量载荷;scopes 缺省 = 一个通配兜底 scope 指向首个模板。"""
    if scopes is None:
        scopes = [{"scope_id": SEED_SCOPE, "index": 0,
                   "template_id": templates[0]["template_id"],
                   "routing_rules": ""}]
    return {"templates": templates, "scopes": scopes}


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    return bool(ok)


def skip(name: str, reason: str) -> None:
    RESULTS.append((name, True, f"SKIP: {reason}"))
    print(f"[SKIP] {name} — {reason}", flush=True)


def summary_and_exit() -> int:
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n==== 验收汇总 ====")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" +
              (f" — {detail}" if detail and not ok else ""))
    if failed:
        print(f"共 {len(RESULTS)} 项，失败 {len(failed)} 项：{failed}")
        return 1
    print(f"共 {len(RESULTS)} 项全部通过（含 SKIP）")
    return 0


def envelope(msg_type: str, *, session_id=None, group="e2e-main", bot="b",
             user="e2e-user", rawdata=None, request_id=None):
    return {
        "type": msg_type,
        "metadata": {
            "request_id": request_id or f"req-{uuid.uuid4().hex[:10]}",
            "session_id": session_id,
            "user_id": user,
            "bot_id": bot,
            "extra": {"group_id": group},
        },
        "rawdata": rawdata or {},
    }


class Client:
    """单入口 HTTP 客户端（base 注入——可以是直连实例或 LB 地址）。"""

    def __init__(self, http: httpx.AsyncClient, base: str, timeout: float = 90.0):
        self.http = http
        self.base = base.rstrip("/")

    async def post(self, msg_type: str, *, session_id=None, group="e2e-main",
                   bot="b", user="e2e-user", rawdata=None, request_id=None):
        resp = await self.http.post(
            f"{self.base}/{msg_type}",
            json=envelope(msg_type, session_id=session_id, group=group, bot=bot,
                          user=user, rawdata=rawdata, request_id=request_id),
        )
        try:
            body = resp.json()
        except Exception:
            body = {}
        return resp.status_code, body.get("rawdata") or {}, body

    async def healthz(self) -> dict | None:
        """经入口打 /healthz；不在线返回 None。"""
        root = self.base.rsplit("/api/session", 1)[0]
        try:
            resp = await self.http.get(f"{root}/healthz", timeout=10.0)
            return resp.json() if resp.status_code == 200 else None
        except Exception:
            return None


async def kubectl(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "kubectl", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return out.decode()


async def pod_exists(pod_id: str, ns: str) -> bool:
    out = await kubectl("get", "pod", "-n", ns, pod_id, "-o", "name")
    return pod_id in out and "NotFound" not in out


async def wait_until(fn, timeout: float, interval: float = 2.0,
                     desc: str = "") -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if await fn():
                return True
        except Exception:
            pass
        await asyncio.sleep(interval)
    return False


async def redis_guard(r: aioredis.Redis, force_flush: bool) -> bool:
    """FLUSHDB 防误刷守卫：目标 DB 存在本服务前缀之外的 key 视为指错库。

    返回 True=可继续（干净或已显式确认），False=中止。
    """
    foreign = [k async for k in r.scan_iter(count=100)
               if not str(k).startswith(OWN_PREFIXES)]
    if foreign:
        if not force_flush:
            check("前置：目标 Redis DB 无外来 key（防误刷）", False,
                  f"存在 {len(foreign)} 个外来 key，例如 {foreign[:3]}；"
                  f"确认库号或显式 --force-flush")
            return False
        check("前置：目标 Redis DB 含外来 key（--force-flush 已确认）", True,
              f"{len(foreign)} 个外来 key 将被 FLUSHDB")
    return True


# ---------------------------------------------------------------- 选主普查


class ElectionCensus:
    """后台采样 agent_runtime:job:* 选主键（元数据 TTL ~3s，轮询须 ≤0.5s）。

    samples: {(job, epoch): {"winner": iid, "candidates": {iid,...}}}
    不变量（断言用）：每 (job, epoch) 至多一个 winner 且 ∈ candidates。
    """

    def __init__(self, r: aioredis.Redis, interval: float = 0.3) -> None:
        self.r = r
        self.interval = interval
        self.samples: dict[tuple[str, str], dict] = {}
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                async for key in self.r.scan_iter(
                        match="agent_runtime:job:*:winner:*", count=200):
                    parts = str(key).split(":")
                    job, epoch = parts[2], parts[-1]
                    val = await self.r.get(key)
                    if val is not None:
                        self.samples.setdefault((job, epoch), {})[
                            "winner"] = val.decode() if isinstance(val, bytes) else val
                async for key in self.r.scan_iter(
                        match="agent_runtime:job:*:candidates:*", count=200):
                    parts = str(key).split(":")
                    job, epoch = parts[2], parts[-1]
                    members = await self.r.smembers(key)
                    if members:
                        iids = {m.decode() if isinstance(m, bytes) else m
                                for m in members}
                        self.samples.setdefault((job, epoch), {}).setdefault(
                            "candidates", set()).update(iids)
            except Exception:
                pass     # 采样容忍瞬时错误（键过期/连接抖动）
            await asyncio.sleep(self.interval)

    def distinct_instance_ids(self) -> set[str]:
        ids: set[str] = set()
        for s in self.samples.values():
            ids.update(s.get("candidates", set()))
            if "winner" in s:
                ids.add(s["winner"])
        return ids

    def assert_ready(self) -> bool:
        """普查数据是否已见到 ≥1 个有效样本。"""
        return bool(self.samples)
