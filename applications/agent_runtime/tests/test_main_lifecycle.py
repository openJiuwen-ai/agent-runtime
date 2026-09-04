# coding: utf-8
"""进程生命周期测试(2026-09 健壮性加固):资源所有权与停机兜底对称性。

- rm_sysctx 必须显式 _owns_db=False(框架缺省「传了 db 即拥有」会对共享
  handler 二次 init_database+connect、stop 时 dispose 别人还在用的 engine);
- stop() 对每个组件逐步兜底,单点失败不阻断其余资源回收。
"""

from __future__ import annotations

from fakeredis.aioredis import FakeRedis
from openjiuwen_runtime.foundation.db import SQLiteHandler
from openjiuwen_runtime.service.config import ServiceConfig

from agent_runtime.config import AgentRuntimeConfig
from agent_runtime.main import OrchestratorSystemContext
from agent_runtime.resource_manager.k8s import FakeK8sPodClient


async def _make_ctx(tmp_path):
    settings = ServiceConfig.from_env()
    arc = AgentRuntimeConfig(mode="local")
    redis_client = FakeRedis()
    db = SQLiteHandler(str(tmp_path / "lifecycle.db"))
    k8s = FakeK8sPodClient()
    ctx = OrchestratorSystemContext(
        redis_client=redis_client, db=db, k8s=k8s,
        settings=settings, arc=arc, instance_id="life-1",
    )
    return ctx, redis_client, db


async def test_rm_sysctx_does_not_reown_shared_resources(tmp_path, monkeypatch):
    """全生命周期共享 DB handler 的 connect/disconnect 各恰一次(SM ctx 正主,
    rm_sysctx 不重复持有)。"""
    ctx, _, db = await _make_ctx(tmp_path)
    calls = {"connect": 0, "disconnect": 0}
    real_connect, real_disconnect = db.connect, db.disconnect

    async def _connect():
        calls["connect"] += 1
        return await real_connect()

    async def _disconnect():
        calls["disconnect"] += 1
        return await real_disconnect()

    monkeypatch.setattr(db, "connect", _connect)
    monkeypatch.setattr(db, "disconnect", _disconnect)

    await ctx.start()
    assert ctx.rm_sysctx._owns_db is False
    assert ctx.rm_sysctx._owns_redis is False
    await ctx.stop()

    assert calls == {"connect": 1, "disconnect": 1}, (
        "共享 DB handler 被重复 connect/disconnect(rm_sysctx 所有权泄漏)")


async def test_stop_tolerates_rm_sysctx_failure(tmp_path, monkeypatch):
    """rm_sysctx.stop() 抛错不得阻断 super().stop()(共享 redis/db 回收)。"""
    ctx, redis_client, _ = await _make_ctx(tmp_path)
    await ctx.start()

    async def _boom():
        raise RuntimeError("simulated rm stop failure")

    monkeypatch.setattr(ctx.rm_sysctx, "stop", _boom)
    closed = []
    real_aclose = redis_client.aclose

    async def _aclose():
        closed.append(True)
        return await real_aclose()

    monkeypatch.setattr(redis_client, "aclose", _aclose)

    await ctx.stop()          # 不上抛即通过
    assert closed, "super().stop() 必须仍执行(共享资源回收)"
