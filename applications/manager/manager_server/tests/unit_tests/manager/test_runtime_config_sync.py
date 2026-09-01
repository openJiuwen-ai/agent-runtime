from types import SimpleNamespace

import pytest

from manager_server.core.instance_resource.runtime_config_sync import (
    build_runtime_config,
    rule_groups_to_routing_rules,
)


class FakeHandler:
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
            return SimpleNamespace(
                template_id="svc-1",
                template_name="svc",
                description="",
                enabled=True,
                agent_image="agent:latest",
                namespace="tenant",
                node_name="arm-master",
                pod_name="agentserver",
                container_name="agent",
                container_port=8080,
                sse_port=8766,
                sse_path="/api/v1/events/stream",
                health_path="/api/v1/health",
                image_pull_policy="IfNotPresent",
                session_concurrency=3,
                service_concurrency=2,
                session_ttl=60,
                service_ttl=300,
                min_idle_services=0,
                readiness_initial_delay=5,
                readiness_period=5,
                ready_timeout=300,
                ready_poll_interval=2,
                message_timeout=600,
                main_container_id="c-agentserver",
                sidecar_container_ids=["c-jiuwenbox"],
                volumes=[{"name": "data", "persistentVolumeClaim": {"claimName": "pvc-1"}}],
                run_as_user=None,
                run_as_group=None,
                kubeconfig=None,
                agent_env={"FOO": "bar"},
                agent_cpu_request=None,
                agent_memory_request=None,
                agent_cpu_limit=None,
                agent_memory_limit=None,
                data={
                    "config_sync": {
                        "containers": [
                            {
                                "container_id": "c-agentserver",
                                "name": "jiuwenclaw-agentserver",
                                "image": "agent:latest",
                                "imagePullPolicy": "IfNotPresent",
                                "ports": [{"name": "sse", "containerPort": 8766}],
                                "volumeMounts": [
                                    {"name": "data", "mountPath": "/data"},
                                ],
                            },
                            {
                                "container_id": "c-jiuwenbox",
                                "name": "jiuwenbox",
                                "image": "box:latest",
                                "imagePullPolicy": "IfNotPresent",
                                "ports": [{"containerPort": 8321}],
                                "volumeMounts": [
                                    {"name": "data", "mountPath": "/data"},
                                ],
                            },
                        ]
                    }
                },
            )
        return None


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
    assert "agent_image" not in tpl
    assert "node_name" not in tpl
    assert tpl["scope_concurrency"] == 3
    assert tpl["pod_concurrency"] == 2
    assert tpl["min_idle_pods"] == 0

    scope = payload["scopes"][0]
    assert scope["scope_id"] == "service-service-1"
    assert scope["index"] == -10
    assert scope["routing_rules"] == (
        "bot_id in ('bot-1') and group_id in ('group-1')"
    )


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
