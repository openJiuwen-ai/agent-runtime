# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
app.py 健康检查与 bootstrap 协调单元测试。

覆盖点：
1) health_check 的默认/透传返回行为。
2) _BootstrapCoordinator 的 disabled/leader/follower 分支。
3) mark_failed_if_needed 与 close 的关键行为。
"""
from __future__ import annotations

import importlib
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest


async def _noop_initialize():
    return None


if "agents" not in sys.modules:
    sys.modules["agents"] = ModuleType("agents")

fake_edp_agent = sys.modules.get("agents.EDPAgent")
if fake_edp_agent is None:
    fake_edp_agent = ModuleType("agents.EDPAgent")
setattr(fake_edp_agent, "initialize", _noop_initialize)
sys.modules["agents.EDPAgent"] = fake_edp_agent
setattr(sys.modules["agents"], "EDPAgent", fake_edp_agent)

app_module = importlib.import_module("app")

# 本测试模块的目标即覆盖 ``app._BootstrapCoordinator`` 这一实现细节类的
# disabled / leader / follower / close 等分支。这里通过一次性引入本地别名
# 集中访问，避免在每个用例中反复触发 G.CLS.11（受保护成员访问）。
BootstrapCoordinator = app_module._BootstrapCoordinator  # pylint: disable=protected-access


class _FakeRedis:
    def __init__(self, *, lock_granted: bool = False, follower_state: dict | None = None):
        self.lock_granted = lock_granted
        self.follower_state = follower_state or {"status": "initializing"}
        self.acquire_calls = []
        self.release_calls = []

    async def acquire_lock(self, *, lock_key: str, owner_id: str, ttl_seconds: int) -> bool:
        self.acquire_calls.append(
            {
                "lock_key": lock_key,
                "owner_id": owner_id,
                "ttl_seconds": ttl_seconds,
            }
        )
        return self.lock_granted

    async def release_lock(self, *, lock_key: str, owner_id: str) -> bool:
        self.release_calls.append({"lock_key": lock_key, "owner_id": owner_id})
        return True

    async def get_json(self, _key: str):
        return self.follower_state


def _settings(**kwargs):
    defaults = {
        "bootstrap_coordination_enabled": True,
        "bootstrap_lock_name": "a2a_global_bootstrap",
        "bootstrap_lock_ttl_sec": 180,
        "bootstrap_wait_timeout_sec": 60,
        "bootstrap_poll_interval_sec": 0.2,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ============================================================================
# health_check
# ============================================================================


@pytest.mark.asyncio
async def test_health_check_returns_default_payload():
    payload = await app_module.health_check()
    assert payload == {"status": "healthy", "service": "A2A Service"}


@pytest.mark.asyncio
async def test_health_check_returns_success_passthrough():
    payload = await app_module.health_check(success="ok")
    assert payload == "ok"


# ============================================================================
# _BootstrapCoordinator
# ============================================================================


@pytest.mark.asyncio
async def test_bootstrap_run_skips_when_disabled():
    redis = _FakeRedis(lock_granted=True)
    coordinator = BootstrapCoordinator(
        settings=_settings(bootstrap_coordination_enabled=False),
        redis=redis,
    )

    await coordinator.run()

    assert redis.acquire_calls == []
    assert coordinator.bootstrap_ready is False


@pytest.mark.asyncio
async def test_bootstrap_run_leader_flow_marks_ready_and_releases_lock(monkeypatch):
    status_calls: list[dict] = []

    async def _fake_set_bootstrap_status(_redis, **kwargs):
        status_calls.append(kwargs)

    run_once_calls: list[str] = []

    async def _fake_run_global_bootstrap_once():
        run_once_calls.append("called")

    monkeypatch.setattr(app_module, "_set_bootstrap_status", _fake_set_bootstrap_status)
    monkeypatch.setattr(app_module, "_run_global_bootstrap_once", _fake_run_global_bootstrap_once)

    redis = _FakeRedis(lock_granted=True)
    coordinator = BootstrapCoordinator(settings=_settings(), redis=redis)

    await coordinator.run()

    assert run_once_calls == ["called"]
    assert [item["status"] for item in status_calls] == ["initializing", "ready"]
    assert coordinator.bootstrap_ready is True
    assert coordinator.leader_locked is False
    assert len(redis.release_calls) == 1


@pytest.mark.asyncio
async def test_bootstrap_run_follower_flow_waits_ready(monkeypatch):
    wait_args = {}

    async def _fake_wait_for_bootstrap_ready(redis, *, status_key, timeout_seconds, poll_interval_seconds):
        wait_args["redis"] = redis
        wait_args["status_key"] = status_key
        wait_args["timeout_seconds"] = timeout_seconds
        wait_args["poll_interval_seconds"] = poll_interval_seconds
        return True

    monkeypatch.setattr(app_module, "_wait_for_bootstrap_ready", _fake_wait_for_bootstrap_ready)

    redis = _FakeRedis(lock_granted=False)
    coordinator = BootstrapCoordinator(settings=_settings(), redis=redis)

    await coordinator.run()

    assert len(redis.acquire_calls) == 1
    assert wait_args["redis"] is redis
    assert wait_args["status_key"] == coordinator.bootstrap_status_key
    assert wait_args["timeout_seconds"] == coordinator.bootstrap_wait_timeout


@pytest.mark.asyncio
async def test_bootstrap_run_follower_flow_raises_when_wait_timeout(monkeypatch):
    async def _fake_wait_for_bootstrap_ready(*_args, **_kwargs):
        return False

    monkeypatch.setattr(app_module, "_wait_for_bootstrap_ready", _fake_wait_for_bootstrap_ready)

    redis = _FakeRedis(lock_granted=False, follower_state={"status": "initializing", "owner_id": "x"})
    coordinator = BootstrapCoordinator(settings=_settings(), redis=redis)

    with pytest.raises(RuntimeError, match="等待 LEADER bootstrap 完成失败"):
        await coordinator.run()


@pytest.mark.asyncio
async def test_mark_failed_if_needed_sets_failed_status_when_needed(monkeypatch):
    status_calls: list[dict] = []

    async def _fake_set_bootstrap_status(_redis, **kwargs):
        status_calls.append(kwargs)

    monkeypatch.setattr(app_module, "_set_bootstrap_status", _fake_set_bootstrap_status)

    redis = _FakeRedis(lock_granted=False)
    coordinator = BootstrapCoordinator(settings=_settings(), redis=redis)
    coordinator.leader_locked = True
    coordinator.bootstrap_ready = False

    await coordinator.mark_failed_if_needed(RuntimeError("boom"))

    assert len(status_calls) == 1
    assert status_calls[0]["status"] == "failed"
    assert "boom" in status_calls[0]["message"]


@pytest.mark.asyncio
async def test_mark_failed_if_needed_swallows_internal_errors(monkeypatch):
    async def _fake_set_bootstrap_status(*_args, **_kwargs):
        raise RuntimeError("set failed")

    monkeypatch.setattr(app_module, "_set_bootstrap_status", _fake_set_bootstrap_status)

    redis = _FakeRedis(lock_granted=False)
    coordinator = BootstrapCoordinator(settings=_settings(), redis=redis)
    coordinator.leader_locked = True
    coordinator.bootstrap_ready = False

    await coordinator.mark_failed_if_needed(RuntimeError("boom"))


@pytest.mark.asyncio
async def test_close_releases_leader_lock_when_held():
    redis = _FakeRedis(lock_granted=False)
    coordinator = BootstrapCoordinator(settings=_settings(), redis=redis)
    coordinator.leader_locked = True

    await coordinator.close()

    assert coordinator.leader_locked is False
    assert len(redis.release_calls) == 1