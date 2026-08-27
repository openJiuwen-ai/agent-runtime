from types import SimpleNamespace

import pytest

from manager_server.core.instance_resource.runtime_config_sync import build_runtime_config


class FakeHandler:
    async def list_records(self, table, filters, **kwargs):
        assert table == "instance_service_resource"
        return [SimpleNamespace(
            resource_id="service-1", ref_template_id="svc-1", enabled=True,
            priority=10, expires_at=None,
            match_expr="bot_id == 'bot-1' and group_id in ['group-1']",
        )]

    async def get(self, table, filters):
        if table == "service_config_template":
            return SimpleNamespace(template_id="svc-1", template_name="svc", enabled=True,
                agent_image="agent:latest", namespace="tenant", pod_name="agentserver",
                container_name="agent", container_port=8080, image_pull_policy="IfNotPresent",
                session_concurrency=3, service_concurrency=2, session_ttl=60,
                service_ttl=300, min_idle_services=0, readiness_initial_delay=5,
                readiness_period=5, ready_timeout=300, ready_poll_interval=2,
                message_timeout=600, data={})
        return None


@pytest.mark.asyncio
async def test_build_runtime_config_routes_service_resource():
    payload = await build_runtime_config(FakeHandler(), "jid-1")
    assert payload["templates"][0]["template_id"] == "svc-1"
    scope = payload["scopes"][0]
    assert scope["scope_id"] == "service-service-1"
    assert scope["index"] == -10
    assert scope["routing_rules"][0]["expressions"] == [
        {"field": "bot_id", "op": "in", "values": ["bot-1"]},
        {"field": "group_id", "op": "in", "values": ["group-1"]},
    ]
