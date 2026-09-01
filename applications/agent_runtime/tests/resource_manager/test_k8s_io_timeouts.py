# coding: utf-8
"""RealK8sPodClient IO 边界测试:per-call 超时 + start/close 生命周期并发。

真集群行为无法在单测复现,注入 fake CoreV1Api / stub kubernetes_asyncio 模块,
断言**调用形态**:四处 API 调用必须携带 _request_timeout(kubernetes_asyncio
不传时 aiohttp ClientTimeout 全 None,网络挂起会无限悬挂拖死 route)、关停
竞态下快照 None 检查归一为 DeployFailed(不裸抛 AttributeError)、并发
start 只建一个 ApiClient(其一泄漏连接/fd)。
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from agent_runtime.errors import DeployFailed
from agent_runtime.resource_manager.k8s import (
    CREATE_TIMEOUT,
    DELETE_TIMEOUT,
    LIST_TIMEOUT,
    READ_TIMEOUT,
    RealK8sPodClient,
)


class _AnyAttrClient(types.SimpleNamespace):
    """kubernetes_asyncio.client 替身:任意 V1* 属性 → 构造 SimpleNamespace。"""

    def __getattr__(self, name):
        def _factory(**kwargs):
            return types.SimpleNamespace(**kwargs)
        return _factory


class _StubApiClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeCore:
    """记录 kwargs 的 CoreV1Api 替身;可对指定方法注入异常。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.raise_on: dict[str, BaseException] = {}

    def _hit(self, name: str) -> None:
        exc = self.raise_on.get(name)
        if exc is not None:
            raise exc

    async def create_namespaced_pod(self, **kwargs):
        self.calls.append(("create", kwargs))
        self._hit("create")
        return types.SimpleNamespace()

    async def read_namespaced_pod(self, **kwargs):
        self.calls.append(("read", kwargs))
        self._hit("read")
        return _fake_pod()

    async def list_namespaced_pod(self, **kwargs):
        self.calls.append(("list", kwargs))
        self._hit("list")
        return types.SimpleNamespace(items=[])

    async def delete_namespaced_pod(self, **kwargs):
        self.calls.append(("delete", kwargs))
        self._hit("delete")
        return types.SimpleNamespace()


def _fake_pod() -> types.SimpleNamespace:
    """Ready Running 的 V1Pod 形状(过 _to_pod_info)。"""
    return types.SimpleNamespace(
        metadata=types.SimpleNamespace(
            name="p1", namespace="default", deletion_timestamp=None, labels=None),
        status=types.SimpleNamespace(
            phase="Running", container_statuses=None, pod_ip="10.42.0.5",
            conditions=[types.SimpleNamespace(type="Ready", status="True")]),
    )


def _armed_client(core: _FakeCore) -> RealK8sPodClient:
    """跳过真实 kubernetes_asyncio 初始化,直接注入 fake CoreV1Api。"""
    client = RealK8sPodClient()
    client._core = core
    client._client = _AnyAttrClient()
    client._api_client = _StubApiClient()
    client._loaded = True
    return client


def _install_k8s_stub(monkeypatch) -> tuple[list[_StubApiClient], type]:
    """把 stub kubernetes_asyncio 塞进 sys.modules,返回 ApiClient 构造记录。"""
    pkg = types.ModuleType("kubernetes_asyncio")
    client_mod = types.ModuleType("kubernetes_asyncio.client")
    config_mod = types.ModuleType("kubernetes_asyncio.config")
    exc_mod = types.ModuleType("kubernetes_asyncio.config.config_exception")

    class ConfigException(Exception):
        pass

    built: list[_StubApiClient] = []

    def _api_client_factory() -> _StubApiClient:
        instance = _StubApiClient()
        built.append(instance)
        return instance

    class CoreV1Api:
        def __init__(self, api_client) -> None:
            self.api_client = api_client

    client_mod.ApiClient = _api_client_factory
    client_mod.CoreV1Api = CoreV1Api
    config_mod.load_incluster_config = lambda: None
    exc_mod.ConfigException = ConfigException
    pkg.client = client_mod
    pkg.config = config_mod
    for name, module in (("kubernetes_asyncio", pkg),
                         ("kubernetes_asyncio.client", client_mod),
                         ("kubernetes_asyncio.config", config_mod),
                         ("kubernetes_asyncio.config.config_exception", exc_mod)):
        monkeypatch.setitem(sys.modules, name, module)
    return built, ConfigException


async def test_all_k8s_calls_carry_request_timeout():
    """create/read/list/delete 四处调用必须带 _request_timeout 且值正确。"""
    core = _FakeCore()
    client = _armed_client(core)

    await client.deploy({"agent_image": "agentserver:1.0", "namespace": "default",
                         "ready_timeout": 1, "ready_poll_interval": 0.01})
    await client.get_pod("p1", "default")
    await client.list_pods("default", "app=x")
    await client.delete("p1", "default")

    timeouts = {name: kwargs.get("_request_timeout") for name, kwargs in core.calls}
    assert timeouts == {
        "create": CREATE_TIMEOUT,
        "read": READ_TIMEOUT,
        "list": LIST_TIMEOUT,
        "delete": DELETE_TIMEOUT,
    }


async def test_read_timeout_is_not_treated_as_404():
    """read 超时(无 .status)不得被当成 NotFound 吞掉——必须显式上抛。"""
    core = _FakeCore()
    core.raise_on["read"] = asyncio.TimeoutError()
    client = _armed_client(core)
    with pytest.raises(asyncio.TimeoutError):
        await client.get_pod("p1", "default")


async def test_delete_timeout_maps_to_deploy_failed():
    core = _FakeCore()
    core.raise_on["delete"] = asyncio.TimeoutError()
    client = _armed_client(core)
    with pytest.raises(DeployFailed):
        await client.delete("p1", "default")


async def test_closed_client_fails_fast_without_attributeerror():
    """close() 摘走引用后的在飞调用:DeployFailed,而非裸 AttributeError。"""
    client = RealK8sPodClient()
    client._loaded = True  # 跳过惰性 start,模拟「start 后引用刚被 close 摘走」
    for call in (lambda: client.get_pod("p1", "default"),
                 lambda: client.list_pods("default", "app=x"),
                 lambda: client.delete("p1", "default")):
        with pytest.raises(DeployFailed, match="k8s client closed"):
            await call()


async def test_concurrent_start_builds_single_apiclient(monkeypatch):
    """并发冷启动只建一个 ApiClient(锁内双检);重复 start 幂等。"""
    built, _ = _install_k8s_stub(monkeypatch)
    client = RealK8sPodClient()
    await asyncio.gather(client.start(), client.start(), client.start())
    assert len(built) == 1
    assert client._core is not None and client._loaded

    await client.close()
    assert client._core is None and client._loaded is False
    assert built[0].closed
