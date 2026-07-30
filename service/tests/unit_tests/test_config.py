# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""ServiceConfig 单测：部署相关项从环境变量读取（命名详细清楚），缺省安全回落。"""
import pytest

from openjiuwen_runtime.service.config import ServiceConfig

_ENVS = [
    "OPENJIUWEN_SERVICE_HOST",
    "OPENJIUWEN_SERVICE_PORT",
    "OPENJIUWEN_SERVICE_REDIS_URL",
    "OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX",
    "OPENJIUWEN_SERVICE_TITLE",
]


@pytest.mark.unit
def test_defaults_when_env_unset(monkeypatch):
    for v in _ENVS:
        monkeypatch.delenv(v, raising=False)
    cfg = ServiceConfig.from_env()
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 8090
    assert cfg.redis_url == "redis://localhost:6379/0"
    assert cfg.key_prefix == "service"
    assert cfg.title == "service"


@pytest.mark.unit
def test_reads_all_env_vars(monkeypatch):
    monkeypatch.setenv("OPENJIUWEN_SERVICE_HOST", "127.0.0.1")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_PORT", "9999")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_REDIS_URL", "redis://cache.internal:6380/3")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX", "prod-ns")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_TITLE", "echo-app")
    cfg = ServiceConfig.from_env()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 9999
    assert cfg.redis_url == "redis://cache.internal:6380/3"
    assert cfg.key_prefix == "prod-ns"
    assert cfg.title == "echo-app"


@pytest.mark.unit
def test_invalid_port_fails_fast(monkeypatch):
    monkeypatch.setenv("OPENJIUWEN_SERVICE_PORT", "not-a-port")
    with pytest.raises(ValueError):
        ServiceConfig.from_env()


@pytest.mark.unit
def test_app_run_host_port_from_env(monkeypatch):
    import fakeredis.aioredis

    from openjiuwen_runtime.service import App, SystemContext

    captured: dict = {}

    def fake_run(app, host=None, port=None, **kw):
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setenv("OPENJIUWEN_SERVICE_HOST", "127.0.0.1")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_PORT", "7000")

    app = App(lambda: SystemContext(redis=fakeredis.aioredis.FakeRedis()))
    app.run()
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 7000


@pytest.mark.unit
def test_app_run_explicit_args_override_env(monkeypatch):
    import fakeredis.aioredis

    from openjiuwen_runtime.service import App, SystemContext

    captured: dict = {}

    def fake_run(app, host=None, port=None, **kw):
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setenv("OPENJIUWEN_SERVICE_PORT", "7000")

    app = App(lambda: SystemContext(redis=fakeredis.aioredis.FakeRedis()))
    app.run(host="0.0.0.0", port=1234)
    assert captured["host"] == "0.0.0.0"          # 显式参覆盖环境变量
    assert captured["port"] == 1234
