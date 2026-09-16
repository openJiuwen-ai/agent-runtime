# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager audit-log 配置：预检 / CRUD / Gateway 推送。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen_runtime.foundation.audit import default_format

pytestmark = pytest.mark.unit


def _valid_payload(**overrides):
    fmt = default_format()
    data = {
        "format": {
            "schema_version": fmt.schema_version,
            "header_fields": list(fmt.header_fields),
            "content_fields": list(fmt.content_fields),
            "required_fields": list(fmt.required_fields),
            "placeholder": fmt.placeholder,
            "timestamp_format": fmt.timestamp_format,
        },
        "otel": {
            "enabled": True,
            "endpoint": "http://collector:4317",
            "protocol": "grpc",
            "headers": {"a": "b"},
        },
        "ntp": {
            "servers": ["ntp-a"],
            "sync_interval": "300s",
            "max_offset_ms": 500,
            "failover": True,
        },
        "data_center": "N",
        "system_code": "99900180001",
        "node": "10.0.0.1:8080",
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_upsert_pushes_gateway_before_create():
    from manager_server.core.application_config.audit_log_config import (
        AuditLogConfigService,
    )

    handler = AsyncMock()
    handler.get = AsyncMock(return_value=None)
    created = MagicMock(
        id=1,
        jiuwenclaw_id="jid-1",
        schema_version="1.0.0",
        otel_enabled=True,
        otel_endpoint="http://collector:4317",
        otel_protocol="grpc",
        data_center="N",
        system_code="99900180001",
        node="10.0.0.1:8080",
        ntp_servers=["ntp-a"],
        ntp_sync_interval="300s",
        ntp_max_offset_ms=500,
        ntp_failover=True,
        body={"format": {}, "otel": {}, "ntp": {}, "data_center": "N"},
        source="manager",
        revision=1,
        created_at=None,
        updated_at=None,
    )
    handler.create = AsyncMock(return_value=created)

    order: list[str] = []

    async def _gw(*_a, **_k):
        order.append("gateway")
        return {"success_flag": True}

    async def _create(*_a, **_k):
        order.append("create")
        return created

    handler.create = AsyncMock(side_effect=_create)

    with patch(
        "manager_server.core.application_config.audit_log_config.gateway_request",
        new_callable=AsyncMock,
        side_effect=_gw,
    ) as gw_mock:
        svc = AuditLogConfigService(handler)
        result = await svc.upsert("jid-1", _valid_payload())

    assert order == ["gateway", "create"]
    assert result["jiuwenclaw_id"] == "jid-1"
    assert result["revision"] == 1
    assert "service" not in (result.get("body") or {})
    gw_mock.assert_awaited_once()
    args = gw_mock.await_args.args
    assert args[0] == "jid-1"
    assert args[1] == "PUT"
    assert args[2] == "/api/v1/audit-log"
    push_body = args[3]
    assert push_body["otel_endpoint"] == "http://collector:4317"
    assert push_body["revision"] == 1
    assert "body" in push_body
    assert "service" not in push_body["body"]


@pytest.mark.asyncio
async def test_upsert_rejects_invalid_protocol_without_gateway_call():
    from manager_server.core.application_config.audit_log_config import (
        AuditLogConfigService,
    )

    handler = AsyncMock()
    with patch(
        "manager_server.core.application_config.audit_log_config.gateway_request",
        new_callable=AsyncMock,
    ) as gw_mock:
        svc = AuditLogConfigService(handler)
        with pytest.raises(ValueError, match="protocol"):
            await svc.upsert(
                "jid-1",
                _valid_payload(otel={"enabled": True, "endpoint": "http://x", "protocol": "udp"}),
            )
    gw_mock.assert_not_awaited()
    handler.create.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_strips_service_from_body():
    from manager_server.core.application_config.audit_log_config import (
        AuditLogConfigService,
        _precheck_and_build_body,
    )

    body = _precheck_and_build_body(_valid_payload(service="gateway"))
    assert "service" not in body

    handler = AsyncMock()
    handler.get = AsyncMock(return_value=None)
    created = MagicMock(
        id=1,
        jiuwenclaw_id="jid-1",
        schema_version="1.0.0",
        otel_enabled=True,
        otel_endpoint="http://collector:4317",
        otel_protocol="grpc",
        data_center="N",
        system_code="99900180001",
        node="10.0.0.1:8080",
        ntp_servers=[],
        ntp_sync_interval="300s",
        ntp_max_offset_ms=500,
        ntp_failover=True,
        body=body,
        source="manager",
        revision=1,
        created_at=None,
        updated_at=None,
    )
    handler.create = AsyncMock(return_value=created)
    with patch(
        "manager_server.core.application_config.audit_log_config.gateway_request",
        new_callable=AsyncMock,
        return_value={"success_flag": True},
    ):
        svc = AuditLogConfigService(handler)
        await svc.upsert("jid-1", _valid_payload(service="gateway"))
    create_row = handler.create.await_args.args[1]
    assert "service" not in create_row["body"]


@pytest.mark.asyncio
async def test_upsert_increments_revision_on_update():
    from manager_server.core.application_config.audit_log_config import (
        AuditLogConfigService,
    )

    existing = MagicMock(revision=3)
    updated = MagicMock(
        id=1,
        jiuwenclaw_id="jid-1",
        schema_version="1.0.0",
        otel_enabled=True,
        otel_endpoint="http://collector:4317",
        otel_protocol="grpc",
        data_center="N",
        system_code="99900180001",
        node="10.0.0.1:8080",
        ntp_servers=["ntp-a"],
        ntp_sync_interval="300s",
        ntp_max_offset_ms=500,
        ntp_failover=True,
        body={},
        source="manager",
        revision=4,
        created_at=None,
        updated_at=None,
    )
    handler = AsyncMock()
    handler.get = AsyncMock(return_value=existing)
    handler.update = AsyncMock(return_value=updated)

    with patch(
        "manager_server.core.application_config.audit_log_config.gateway_request",
        new_callable=AsyncMock,
        return_value={"success_flag": True},
    ) as gw_mock:
        svc = AuditLogConfigService(handler)
        result = await svc.upsert("jid-1", _valid_payload())

    assert result["revision"] == 4
    assert gw_mock.await_args.args[3]["revision"] == 4


@pytest.mark.asyncio
async def test_delete_gateway_before_mdb():
    from manager_server.core.application_config.audit_log_config import (
        AuditLogConfigService,
    )

    handler = AsyncMock()
    handler.get = AsyncMock(return_value=MagicMock())
    handler.delete = AsyncMock(return_value=True)
    order: list[str] = []

    async def _gw(*_a, **_k):
        order.append("gateway")
        return {"success_flag": True}

    async def _del(*_a, **_k):
        order.append("delete")
        return True

    handler.delete = AsyncMock(side_effect=_del)

    with patch(
        "manager_server.core.application_config.audit_log_config.gateway_request",
        new_callable=AsyncMock,
        side_effect=_gw,
    ) as gw_mock:
        svc = AuditLogConfigService(handler)
        await svc.delete("jid-1")

    assert order == ["gateway", "delete"]
    gw_mock.assert_awaited_once_with("jid-1", "DELETE", "/api/v1/audit-log", {})


@pytest.mark.asyncio
async def test_delete_missing_raises():
    from manager_server.core.application_config.audit_log_config import (
        AuditLogConfigService,
    )

    handler = AsyncMock()
    handler.get = AsyncMock(return_value=None)
    svc = AuditLogConfigService(handler)
    with pytest.raises(ValueError, match="not found"):
        await svc.delete("jid-1")


@pytest.mark.asyncio
async def test_push_sync_skips_when_missing():
    from manager_server.core.application_config.audit_log_config import (
        push_audit_log_config_sync_to_gateway,
    )

    handler = AsyncMock()
    handler.get = AsyncMock(return_value=None)
    with patch(
        "manager_server.core.application_config.audit_log_config.gateway_request",
        new_callable=AsyncMock,
    ) as gw_mock:
        ack = await push_audit_log_config_sync_to_gateway(handler, "jid-1")
    gw_mock.assert_not_awaited()
    assert ack["result"]["synced"] is False


@pytest.mark.asyncio
async def test_push_sync_puts_row():
    from manager_server.core.application_config.audit_log_config import (
        push_audit_log_config_sync_to_gateway,
    )

    row = MagicMock(
        schema_version="1.0.0",
        otel_enabled=True,
        otel_endpoint="http://x",
        otel_protocol="http",
        data_center="N",
        system_code="s",
        node=None,
        ntp_servers=[],
        ntp_sync_interval="300s",
        ntp_max_offset_ms=500,
        ntp_failover=True,
        body={"format": {"schema_version": "1.0.0"}},
        source="manager",
        revision=2,
    )
    handler = AsyncMock()
    handler.get = AsyncMock(return_value=row)
    with patch(
        "manager_server.core.application_config.audit_log_config.gateway_request",
        new_callable=AsyncMock,
        return_value={"success_flag": True, "result": {}, "transport": "http"},
    ) as gw_mock:
        ack = await push_audit_log_config_sync_to_gateway(handler, "jid-1")
    assert ack["success_flag"] is True
    gw_mock.assert_awaited_once()
    assert gw_mock.await_args.args[2] == "/api/v1/audit-log"
    assert gw_mock.await_args.args[3]["revision"] == 2
    assert gw_mock.await_args.args[3]["otel_protocol"] == "http"


@pytest.mark.asyncio
async def test_sync_data_includes_audit_log_push():
    from manager_server.core.instance.instance_data_lifecycle import (
        sync_data_to_gateway_on_register,
    )

    handler = AsyncMock()
    ack = {"success_flag": True, "result": {"synced": True}, "transport": "http"}

    with (
        patch(
            "manager_server.core.instance.instance_data_lifecycle.sync_referenced_templates_to_gateway",
            new_callable=AsyncMock,
            return_value={"model": ack},
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_agent_resources_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_logging_config_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_task_memory_config_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_memory_config_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_audit_log_config_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ) as audit_mock,
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_log_masking_rules_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle._seed_log_masking_if_needed",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.rebuild_jid_template_ref_for_gateway",
            new_callable=AsyncMock,
        ),
    ):
        results = await sync_data_to_gateway_on_register(handler, "jid-app")

    audit_mock.assert_awaited_once_with(handler, "jid-app")
    assert "audit_log" in results
