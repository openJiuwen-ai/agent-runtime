from types import SimpleNamespace
from typing import Any

import pytest

from manager_server.core.instance_resource.runtime_config_sync import (
    build_runtime_config,
    rule_groups_to_routing_rules,
    service_template_wire,
)

_CONTAINERS = {
    "c-agentserver": SimpleNamespace(
        container_id="c-agentserver",
        name="jiuwenclaw-agentserver",
        image="agent:latest",
        image_pull_policy="IfNotPresent",
        ports=[{"name": "sse", "containerPort": 8766}],
        env=None,
        env_from=None,
        resources=None,
        volume_mounts=[{"name": "data", "mountPath": "/data"}],
        security_context=None,
        command=None,
        args=None,
        readiness_probe=None,
    ),
    "c-jiuwenbox": SimpleNamespace(
        container_id="c-jiuwenbox",
        name="jiuwenbox",
        image="box:latest",
        image_pull_policy="IfNotPresent",
        ports=[{"containerPort": 8321}],
        env=None,
        env_from=None,
        resources=None,
        volume_mounts=[{"name": "data", "mountPath": "/data"}],
        security_context=None,
        command=None,
        args=None,
        readiness_probe=None,
    ),
}


def _template_row(**overrides):
    base = dict(
        template_id="svc-1",
        template_name="svc",
        description="",
        enabled=True,
        namespace="tenant",
        node_name="arm-master",
        fs_group=1000,
        pod_name="agentserver",
        sse_path="/api/v1/events/stream",
        scope_concurrency=3,
        pod_concurrency=2,
        session_ttl=60,
        pod_ttl=300,
        min_idle_pods=0,
        ready_timeout=300,
        ready_poll_interval=2,
        message_timeout=600,
        main_container_id="c-agentserver",
        sidecar_container_ids=["c-jiuwenbox"],
        volumes=[{"name": "data", "persistentVolumeClaim": {"claimName": "pvc-1"}}],
        kubeconfig=None,
        data={"demo": "svc"},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeHandler:
    def __init__(self, *, containers: dict[str, Any] | None = None):
        self._containers = containers if containers is not None else dict(_CONTAINERS)

    async def list_records(self, table, filters, **kwargs):
        assert table == "instance_service_resource"
        return [
            SimpleNamespace(
                resource_id="service-1",
                ref_template_id="svc-1",
                enabled=True,
                priority=10,
                expires_at=None,
                match_expr="bot_id == 'bot-1' and group_id in ['group-1']",
            )
        ]

    async def get(self, table, filters):
        if table == "service_config_template":
            return _template_row()
        if table == "service_config_container":
            cid = str(filters.get("container_id") or "")
            return self._containers.get(cid)
        return None

    async def create(self, table, data):
        raise AssertionError("unexpected create")

    async def update(self, table, filters, data):
        raise AssertionError("unexpected update")


@pytest.mark.asyncio
async def test_build_runtime_config_routes_service_resource():
    payload = await build_runtime_config(FakeHandler(), "jid-1")
    assert "containers" in payload
    assert {c["container_id"] for c in payload["containers"]} == {
        "c-agentserver",
        "c-jiuwenbox",
    }

    tpl = payload["templates"][0]
    assert tpl["template_id"] == "svc-1"
    assert tpl["main_container_id"] == "c-agentserver"
    assert tpl["sidecar_container_ids"] == ["c-jiuwenbox"]
    assert tpl["nodeName"] == "arm-master"
    assert tpl["fsGroup"] == 1000
    # ns 不再由管理面配置:行内 namespace="tenant" 也不透传,恒发空串
    # (空串 = 继承,AgentServer 跟随 runtime 自身 ns)
    assert tpl["namespace"] == ""
    assert "agent_image" not in tpl
    assert "node_name" not in tpl
    assert "fs_group" not in tpl
    assert tpl["scope_concurrency"] == 3
    assert tpl["pod_concurrency"] == 2
    assert tpl["min_idle_pods"] == 0
    assert tpl["data"] == {"demo": "svc"}

    scope = payload["scopes"][0]
    assert scope["scope_id"] == "service-service-1"
    assert scope["index"] == 10
    assert scope["routing_rules"] == (
        "bot_id in ('bot-1') and group_id in ('group-1')"
    )
    assert scope["data"] is None


@pytest.mark.asyncio
async def test_service_template_wire_skips_without_container_rows():
    row = _template_row()
    assert await service_template_wire(FakeHandler(containers={}), row) is None


@pytest.mark.asyncio
async def test_service_template_wire_skips_without_main_container_id():
    row = _template_row(main_container_id=None)
    assert await service_template_wire(FakeHandler(), row) is None


@pytest.mark.asyncio
async def test_service_template_wire_falls_back_to_data_json():
    """存量：表无行时仍可读 data.config_sync.containers。"""
    row = _template_row(
        data={
            "config_sync": {
                "containers": [
                    {
                        "container_id": "c-agentserver",
                        "name": "agent",
                        "image": "from-json:latest",
                        "imagePullPolicy": "IfNotPresent",
                    },
                    {
                        "container_id": "c-jiuwenbox",
                        "name": "box",
                        "image": "box:json",
                        "imagePullPolicy": "IfNotPresent",
                    },
                ]
            }
        }
    )
    wired = await service_template_wire(FakeHandler(containers={}), row)
    assert wired is not None
    _tpl, containers = wired
    by_id = {c["container_id"]: c for c in containers}
    assert set(by_id) == {"c-agentserver", "c-jiuwenbox"}
    assert by_id["c-agentserver"]["image"] == "from-json:latest"


def test_rule_groups_to_routing_rules_or_and():
    expr = rule_groups_to_routing_rules(
        [
            {
                "expressions": [
                    {"field": "user_id", "op": "in", "values": ["u1"]},
                ]
            },
            {
                "expressions": [
                    {"field": "bot_id", "op": "not_in", "values": ["b1"]},
                    {"field": "group_id", "op": "in", "values": ["g1"]},
                ]
            },
        ]
    )
    assert expr == "user_id in ('u1') or (bot_id not in ('b1') and group_id in ('g1'))"


@pytest.mark.asyncio
async def test_sync_runtime_config_posts_to_instance_runtime_host(monkeypatch):
    """config_sync 经 runtime_request 打到 /api/session/config_sync。"""
    from unittest.mock import AsyncMock, MagicMock

    from manager_server.core.instance_resource import runtime_config_sync as mod

    posted: dict[str, Any] = {}

    async def _runtime_request(jid, method, path, payload=None, **kwargs):
        posted.update(jid=jid, method=method, path=path, payload=payload, kwargs=kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        mod,
        "build_runtime_config",
        AsyncMock(return_value={"containers": [], "templates": [], "scopes": []}),
    )
    monkeypatch.setattr(mod, "runtime_request", _runtime_request)

    result = await mod.sync_runtime_config(MagicMock(), "jid-wx2")
    assert result == {"ok": True}
    assert posted["jid"] == "jid-wx2"
    assert posted["method"] == "POST"
    assert posted["path"] == "/api/session/config_sync"
    assert posted["payload"]["type"] == "config_sync"
    assert posted["payload"]["rawdata"] == {
        "containers": [],
        "templates": [],
        "scopes": [],
    }
