# coding: utf-8
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import app as app_module


class _Redis:
    def __init__(self, *, lock_result=True, states=None) -> None:
        self.lock_result = lock_result
        self.states = list(states or [])
        self.status_payloads = []
        self.released = []
        self.acquire_kwargs = {}

    async def set_json(self, key, value, ex=None):
        self.status_payloads.append((key, value, ex))

    async def get_json(self, key):
        if self.states:
            return self.states.pop(0)
        return {}

    async def acquire_lock(self, **kwargs):
        self.acquire_kwargs = kwargs
        return self.lock_result

    async def release_lock(self, **kwargs):
        self.released.append(kwargs)
        return True


def _settings(**kwargs):
    values = {
        "bootstrap_coordination_enabled": True,
        "bootstrap_lock_name": "boot",
        "bootstrap_lock_ttl_sec": 2,
        "bootstrap_wait_timeout_sec": 2,
        "bootstrap_poll_interval_sec": 0.2,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_log_format_and_agent_cards(monkeypatch):
    record = {"extra": {}, "time": None}
    assert "{message}" in app_module.dynamic_format(record)
    assert app_module.LOG_FIELD_SEPARATOR in app_module.dynamic_format({"extra": {"trace_id": "t"}})
    assert app_module.LOG_FIELD_SEPARATOR in app_module.dynamic_format({"extra": {"tag": "TAG", "cost": 1}})

    monkeypatch.setattr(
        app_module,
        "get_settings",
        lambda: SimpleNamespace(fastapi_host="0.0.0.0", fastapi_port=9000),
    )
    dpa_card = app_module._build_dpa_card()
    assert dpa_card.supported_interfaces[0].url == "http://localhost:9000/a2a/"
    assert app_module._build_va_card("http://va").supported_interfaces[0].url == "http://va"
    assert app_module._bootstrap_lock_key("x") == "a2a:bootstrap:lock:x"
    assert app_module._bootstrap_status_key("x") == "a2a:bootstrap:status:x"


@pytest.mark.asyncio
async def test_bootstrap_status_wait_ready_failed_and_timeout(monkeypatch):
    redis = _Redis(states=[{}, {"status": "ready", "owner_id": "o"}])

    async def fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(app_module.asyncio, "sleep", fast_sleep)

    await app_module._set_bootstrap_status(
        redis,
        status_key="status",
        status="ready",
        owner_id="owner",
        message=None,
        ttl_seconds=1,
    )
    assert redis.status_payloads[0][2] == 60
    assert await app_module._wait_for_bootstrap_ready(
        redis,
        status_key="status",
        timeout_seconds=1,
        poll_interval_seconds=0.2,
    ) is True

    failed = _Redis(states=[{"status": "failed", "owner_id": "o"}])
    assert await app_module._wait_for_bootstrap_ready(
        failed,
        status_key="status",
        timeout_seconds=1,
        poll_interval_seconds=0.2,
    ) is False


@pytest.mark.asyncio
async def test_bootstrap_coordinator_leader_and_disabled(monkeypatch):
    redis = _Redis(lock_result=True)
    coordinator = app_module._BootstrapCoordinator(settings=_settings(), redis=redis)
    await coordinator.run()

    assert coordinator.bootstrap_ready is True
    assert coordinator.leader_locked is False
    assert any(payload[1]["status"] == "ready" for payload in redis.status_payloads)
    assert redis.released

    disabled = app_module._BootstrapCoordinator(
        settings=_settings(bootstrap_coordination_enabled=False),
        redis=_Redis(),
    )
    await disabled.run()
    await disabled.close()
    assert disabled.leader_locked is False


@pytest.mark.asyncio
async def test_bootstrap_coordinator_follower_and_failure(monkeypatch):
    follower = app_module._BootstrapCoordinator(
        settings=_settings(),
        redis=_Redis(lock_result=False, states=[{"status": "ready"}]),
    )
    await follower.run()
    assert follower.bootstrap_ready is False

    failing = app_module._BootstrapCoordinator(settings=_settings(), redis=_Redis(lock_result=True))

    async def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "_run_global_bootstrap_once", boom)
    with pytest.raises(RuntimeError, match="boom"):
        await failing.run()
    assert any(payload[1]["status"] == "failed" for payload in failing.redis.status_payloads)

    await failing.mark_failed_if_needed(RuntimeError("late"))
    assert any(payload[1]["message"] == "late" for payload in failing.redis.status_payloads)


def test_init_no_proxy_merges_env_and_keeps_defaults(monkeypatch):
    """env 中配置的 NO_PROXY 地址应被合并，同时保留 localhost/127.0.0.1 兜底。"""
    monkeypatch.setenv("NO_PROXY", "100.100.135.209,foo.example.com")
    # 跳过真实 .env 加载，专注测试合并逻辑
    monkeypatch.setattr(app_module, "_load_env_to_environ", lambda: None)

    app_module._init_no_proxy()

    value = os.environ["NO_PROXY"]
    assert "localhost" in value
    assert "127.0.0.1" in value
    assert "100.100.135.209" in value
    assert "foo.example.com" in value
    # 大小写同步（Windows 上 NO_PROXY/no_proxy 同一变量，Linux 上需显式同步）
    assert os.environ["no_proxy"] == value


def test_init_no_proxy_falls_back_to_defaults_when_env_empty(monkeypatch):
    """env 未配置 NO_PROXY 时，仅保留本地地址兜底，大小写同步。"""
    monkeypatch.delenv("NO_PROXY", raising=False)
    # 跳过真实 .env 加载，专注测试合并逻辑
    monkeypatch.setattr(app_module, "_load_env_to_environ", lambda: None)

    app_module._init_no_proxy()

    assert set(os.environ["NO_PROXY"].split(",")) == {"localhost", "127.0.0.1"}
    assert os.environ["no_proxy"] == os.environ["NO_PROXY"]


def test_load_env_to_environ_loads_proxy_vars(tmp_path, monkeypatch):
    """.env 中的代理变量应被同步进 os.environ，使 httpx(trust_env) 能读到。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HTTP_PROXY=http://10.108.8.59:3128\n"
        "HTTPS_PROXY=http://10.108.8.59:3128\n"
        "NO_PROXY=localhost,127.0.0.1,7.213.203.4\n",
        encoding="utf-8",
    )
    # 清理可能存在的同名变量，避免污染
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(app_module, "__file__", str(env_file.parent / "fake_app.py"))

    app_module._load_env_to_environ()

    assert os.environ.get("HTTP_PROXY") == "http://10.108.8.59:3128"
    assert os.environ.get("HTTPS_PROXY") == "http://10.108.8.59:3128"
    assert "7.213.203.4" in os.environ.get("NO_PROXY", "")


def test_load_env_to_environ_skips_when_no_env_file(tmp_path, monkeypatch):
    """.env 不存在时应静默跳过，不报错。"""
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "fake_app.py"))
    # tmp_path 下没有 .env
    app_module._load_env_to_environ()  # 不应抛异常
