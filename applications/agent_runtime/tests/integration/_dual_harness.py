# coding: utf-8
"""进程内双实例测试 harness（多副本语义 / 离线 / fakeredis）。

同一事件循环里跑两个完整 App（各自 SystemContext + 全部后台 Job），共享
**同一** FakeRedis / SQLiteHandler / FakeK8s —— 等价于两个副本指向同一
Redis/DB/K8s，用于确定性地验证跨副本逻辑（选主互斥、deploy 锁竞争、
PubSub 跨副本唤醒、幂等重放、配置失效传播……）。

要点（开发交接文档「多副本踩点」）：
- httpx.ASGITransport 不发 lifespan 事件，而 RestAdapter 每请求经
  ``_ensure_sysctx_async`` 惰性建 ctx —— 必须先手动驱动 lifespan，否则
  每 App 悄悄建出第二个 ctx，绕过后台 Job 启停。``asgi_lifespan`` 即为此。
- 两个 TestClient 不可行（各自事件循环共享一个 FakeRedis 会破坏
  loop 绑定的 pubsub future）→ 单事件循环 + ASGITransport。
- 共享资源由 harness 独占 connect/disconnect，App 侧
  ``own_resources=False``（避免双 start 双 connect / 双 stop 双 close）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from fakeredis.aioredis import FakeRedis

from agent_runtime.config import AgentRuntimeConfig
from agent_runtime.main import create_app
from agent_runtime.util import scope_id_of

REPLICA_IDS = ("replica-a", "replica-b")

LIFESPAN_TIMEOUT = 10.0        # lifespan startup/shutdown 完成等待上限


# ------------------------------------------------------------ ASGI lifespan


@asynccontextmanager
async def asgi_lifespan(asgi_app):
    """手写 ASGI lifespan 协议驱动（httpx.ASGITransport 不会触发）。"""
    events: list[str] = []
    done = asyncio.Event()
    queue: asyncio.Queue = asyncio.Queue()

    async def receive() -> dict:
        return await queue.get()

    async def send(message: dict) -> None:
        events.append(message["type"])
        if message["type"].startswith("lifespan.") and message["type"].endswith(
            ("complete", "failed")
        ):
            done.set()

    task = asyncio.create_task(asgi_app({"type": "lifespan"}, receive, send))
    await queue.put({"type": "lifespan.startup"})
    await asyncio.wait_for(done.wait(), timeout=LIFESPAN_TIMEOUT)
    if "lifespan.startup.failed" in events:
        await task
        raise RuntimeError("app lifespan startup failed")
    try:
        yield
    finally:
        done.clear()
        await queue.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(done.wait(), timeout=LIFESPAN_TIMEOUT)
        await task


# ------------------------------------------------------------ 可减速 FakeK8s


class SlowFakeK8sPodClient:
    """包装 FakeK8sPodClient：deploy 可减速并记录窗口（跨副本串行化断言）。

    延迟落在 deploy() 内 —— 真实流程中 deploy 全程持有 per-scope
    ``lock_deploy``，因此「部署窗口重叠」等价于「锁未串行化」。
    """

    def __init__(self, inner, deploy_delay: float = 0.0) -> None:
        self._inner = inner
        self.deploy_delay = deploy_delay
        # (pod_id, t_start, t_end)，time.monotonic()
        self.deploy_log: list[tuple[str, float, float]] = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def set_deploy_failures(self, count: int) -> None:
        """写透内层 FakeK8s 的失败旋钮（__getattr__ 只代理读，不代理写）。"""
        self._inner.deploy_failures = count

    async def deploy(self, pod_spec: dict):
        t0 = time.monotonic()
        if self.deploy_delay:
            await asyncio.sleep(self.deploy_delay)
        info = await self._inner.deploy(pod_spec)
        self.deploy_log.append((info.pod_id, t0, time.monotonic()))
        return info

    def deploy_windows_overlap(self) -> bool:
        """任意两次 deploy 的时间窗是否存在交集。"""
        windows = sorted((t0, t1) for _, t0, t1 in self.deploy_log)
        return any(
            next_start < prev_end
            for (_, prev_end), (next_start, _) in zip(windows, windows[1:])
        )


# ------------------------------------------------------------ 双副本组装


class DualReplicas:
    """两个完整 App 实例（A=0 / B=1），共享同一组物理资源。"""

    def __init__(self, settings, arc, redis, db, k8s) -> None:
        self.redis = redis
        self.db = db
        self.k8s = k8s
        self.apps = [
            create_app(
                settings, arc,
                resources=(redis, db, k8s),
                instance_id=REPLICA_IDS[i],
                own_resources=False,
            )
            for i in range(2)
        ]
        self.clients = [
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app.asgi),
                              base_url="http://dual.test")
            for app in self.apps
        ]
        self._lifespans = [asgi_lifespan(app.asgi) for app in self.apps]

    async def start(self) -> None:
        for cm in self._lifespans:
            await cm.__aenter__()
        # lifespan 已设 state.sysctx —— 保证后续请求走 _ensure_sysctx_async
        # 的「复用」分支而不是惰性二建（否则后台 Job 生命周期被绕过）。
        for app in self.apps:
            assert getattr(app.asgi.state, "sysctx", None) is not None

    async def stop(self) -> None:
        for cm in reversed(self._lifespans):
            await cm.__aexit__(None, None, None)
        for client in self.clients:
            await client.aclose()

    # ------------------------------------------------------------ 访问器

    def sysctx(self, i: int):
        return self.apps[i].asgi.state.sysctx

    def envelope(self, msg_type, *, session_id=None, group_id="grp", bot_id="bot",
                 rawdata=None, request_id=None) -> dict:
        return {
            "type": msg_type,
            "metadata": {
                "request_id": request_id or f"req-{uuid.uuid4().hex[:8]}",
                "session_id": session_id,
                "bot_id": bot_id,
                "extra": {"group_id": group_id},
            },
            "rawdata": rawdata or {},
        }

    async def post(self, i: int, msg_type: str, **kw):
        """POST /api/session/{msg_type} → (status, rawdata, body)。"""
        resp = await self.clients[i].post(
            f"/api/session/{msg_type}", json=self.envelope(msg_type, **kw))
        body = resp.json()
        return resp.status_code, (body.get("rawdata") or {}), body

    async def healthz(self, i: int):
        resp = await self.clients[i].get("/healthz")
        return resp.status_code, resp.json()

    @staticmethod
    def _s(value):
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def smembers(self, key: str) -> set[str]:
        return {self._s(m) for m in await self.redis.smembers(key)}

    async def get(self, key: str):
        return self._s(await self.redis.get(key))

    # ------------------------------------------------------------ 播种 / 选主采样

    async def seed_template(self, template_id="tpl-1", rule_id="rule-all",
                            **overrides) -> None:
        """经 B 的 HTTP config_sync 下发 template + 全量路由规则（串行）。"""
        template = {
            "agent_image": "agentserver:1.0",
            "namespace": "default",
            "scope_concurrency": 3,
            "pod_concurrency": 2,
            "session_ttl": 60,
            "pod_ttl": 300,
            "min_idle_pods": 0,
            **overrides,
        }
        status, _, body = await self.post(
            1, "config_sync",
            rawdata={"kind": "template", "op": "create",
                     "template_id": template_id, "template": template})
        assert status == 200, body
        status, _, body = await self.post(
            1, "config_sync",
            rawdata={"kind": "routing_rule", "op": "create", "rule_id": rule_id,
                     "group_id": "*", "bot_id": "*", "template_id": template_id})
        assert status == 200, body

    async def sample_election(self, job: str, duration: float,
                              interval: float = 0.2) -> dict:
        """采样选主键（TTL ~3s，采样间隔须远小于 TTL）。

        返回 {epoch: {"winner": instance_id, "candidates": {instance_id,...}}}；
        键在中途过期属正常，缺失的 epoch 就是不完整样本。
        """
        samples: dict[str, dict] = {}
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            async for key in self.redis.scan_iter(
                    match=f"agent_runtime:job:{job}:winner:*", count=100):
                epoch = self._s(key).rsplit(":", 1)[-1]
                val = await self.redis.get(key)
                if val is not None:
                    samples.setdefault(epoch, {})["winner"] = self._s(val)
            async for key in self.redis.scan_iter(
                    match=f"agent_runtime:job:{job}:candidates:*", count=100):
                epoch = self._s(key).rsplit(":", 1)[-1]
                members = await self.redis.smembers(key)
                if members:
                    samples.setdefault(epoch, {}).setdefault(
                        "candidates", set()).update(self._s(m) for m in members)
            await asyncio.sleep(interval)
        return samples


@pytest.fixture
async def dual(tmp_path, monkeypatch):
    """两 App + 一组共享资源（fakeredis / 文件 SQLite / SlowFakeK8s）。"""
    from openjiuwen_runtime.foundation.db import SQLiteHandler
    from openjiuwen_runtime.service.config import ServiceConfig

    from agent_runtime.session_manager.config_store import (
        ROUTING_RULE_TABLE_DEF,
        SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    )

    monkeypatch.setenv("AGENT_RUNTIME_SQLITE_PATH", str(tmp_path / "dual.db"))
    settings = ServiceConfig.from_env()
    # 短等待上限：排队类用例快速走到 504，缩短墙钟
    arc = AgentRuntimeConfig(mode="local", scope_full_timeout=3.0)

    from agent_runtime.resource_manager.k8s import FakeK8sPodClient

    redis = FakeRedis()
    db = SQLiteHandler(str(tmp_path / "dual.db"))
    await db.connect()
    await db.init_table(SERVICE_CONFIG_TEMPLATE_TABLE_DEF)
    await db.init_table(ROUTING_RULE_TABLE_DEF)
    k8s = SlowFakeK8sPodClient(FakeK8sPodClient())

    dr = DualReplicas(settings, arc, redis, db, k8s)
    await dr.start()
    yield dr
    await dr.stop()
    await db.disconnect()
    await redis.flushall()
    await redis.aclose()


def scope_of(group_id: str = "grp", bot_id: str = "bot") -> str:
    """测试默认 (grp, bot) 的 scope_id。"""
    return scope_id_of(group_id, bot_id)
