"""Agent 模板（agent_definition）手动维护测试。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from manager_server.core.agent_definition.service import (
    AgentDefinitionExists,
    AgentDefinitionService,
    AgentNameDuplicated,
    TemplateUnavailable,
)
from manager_server.core.user_console.agent_identity import build_agent_id
from manager_server.infrastructure.utils import utc_now
from manager_server.schemas.agent_definition_schemas import AgentDefinitionListQuery

pytestmark = pytest.mark.unit


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, label))


async def _seed_cfg(handler, template_id: str, name: str, *, enabled: bool = True) -> None:
    now = utc_now()
    await handler.create(
        "agent_template",
        {
            "template_id": template_id,
            "template_name": name,
            "enabled": enabled,
            "created_at": now,
            "updated_at": now,
        },
    )


async def _seed_rt(handler, template_id: str, name: str, *, enabled: bool = True) -> None:
    now = utc_now()
    await handler.create(
        "service_config_template",
        {
            "template_id": template_id,
            "template_name": name,
            "enabled": enabled,
            "created_at": now,
            "updated_at": now,
        },
    )


async def _rows(handler):
    return await handler.list_records("agent_definition", {}, limit=1000, offset=0)


@pytest.mark.asyncio
async def test_create_and_list_resolves_template_names(manager_api) -> None:
    handler = manager_api.handler
    cfg_a, cfg_b, rt = _uuid("cfg-a"), _uuid("cfg-b"), _uuid("rt-a")
    await _seed_cfg(handler, cfg_a, "Cfg A")
    await _seed_cfg(handler, cfg_b, "Cfg B")
    await _seed_rt(handler, rt, "Rt A")
    service = AgentDefinitionService(handler)

    first = await service.create(cfg_a, rt, "Sales Agent")
    second = await service.create(cfg_b, rt, "Support Agent")

    assert first["agent_id"] == build_agent_id(rt, cfg_a)
    assert first["agent_config_template_name"] == "Cfg A"
    assert first["runtime_template_name"] == "Rt A"
    assert second["agent_id"] != first["agent_id"]

    page = await service.list(AgentDefinitionListQuery())
    assert page["total"] == 2
    names = {item["agent_name"] for item in page["items"]}
    assert names == {"Sales Agent", "Support Agent"}


@pytest.mark.asyncio
async def test_create_rejects_duplicate_combination(manager_api) -> None:
    handler = manager_api.handler
    cfg, rt = _uuid("cfg-a"), _uuid("rt-a")
    await _seed_cfg(handler, cfg, "Cfg A")
    await _seed_rt(handler, rt, "Rt A")
    service = AgentDefinitionService(handler)
    await service.create(cfg, rt, "First")

    with pytest.raises(AgentDefinitionExists):
        await service.create(cfg, rt, "Second")


@pytest.mark.asyncio
async def test_create_rejects_duplicate_name_case_insensitive(manager_api) -> None:
    handler = manager_api.handler
    cfg_a, cfg_b, rt = _uuid("cfg-a"), _uuid("cfg-b"), _uuid("rt-a")
    await _seed_cfg(handler, cfg_a, "Cfg A")
    await _seed_cfg(handler, cfg_b, "Cfg B")
    await _seed_rt(handler, rt, "Rt A")
    service = AgentDefinitionService(handler)
    await service.create(cfg_a, rt, "Sales")

    with pytest.raises(AgentNameDuplicated):
        await service.create(cfg_b, rt, " sALES ")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["cfg_disabled", "rt_disabled", "cfg_missing"])
async def test_create_requires_enabled_templates(manager_api, case: str) -> None:
    handler = manager_api.handler
    cfg, rt = _uuid(f"cfg-{case}"), _uuid(f"rt-{case}")
    if case != "cfg_missing":
        await _seed_cfg(handler, cfg, "Cfg", enabled=case != "cfg_disabled")
    await _seed_rt(handler, rt, "Rt", enabled=case != "rt_disabled")
    service = AgentDefinitionService(handler)

    with pytest.raises(TemplateUnavailable):
        await service.create(cfg, rt, "Agent")


@pytest.mark.asyncio
async def test_rename_updates_name_and_rejects_duplicate(manager_api) -> None:
    handler = manager_api.handler
    cfg_a, cfg_b, rt = _uuid("cfg-a"), _uuid("cfg-b"), _uuid("rt-a")
    await _seed_cfg(handler, cfg_a, "Cfg A")
    await _seed_cfg(handler, cfg_b, "Cfg B")
    await _seed_rt(handler, rt, "Rt A")
    service = AgentDefinitionService(handler)
    first = await service.create(cfg_a, rt, "Sales")
    second = await service.create(cfg_b, rt, "Support")

    renamed = await service.rename(first["agent_id"], " Sales CN ")
    assert renamed is not None and renamed["agent_name"] == "Sales CN"

    with pytest.raises(AgentNameDuplicated):
        await service.rename(second["agent_id"], "sales cn")

    assert await service.rename("missing", "X") is None


@pytest.mark.asyncio
async def test_delete_then_recreate_reuses_agent_id_but_not_name(manager_api) -> None:
    handler = manager_api.handler
    cfg, rt = _uuid("cfg-a"), _uuid("rt-a")
    await _seed_cfg(handler, cfg, "Cfg A")
    await _seed_rt(handler, rt, "Rt A")
    service = AgentDefinitionService(handler)
    created = await service.create(cfg, rt, "Sales")

    assert await service.delete(created["agent_id"]) is True
    assert await _rows(handler) == []
    assert await service.delete(created["agent_id"]) is False

    recreated = await service.create(cfg, rt, "Sales Reborn")
    assert recreated["agent_id"] == created["agent_id"]
    assert recreated["agent_name"] == "Sales Reborn"


@pytest.mark.asyncio
async def test_historical_row_is_listed_and_deletable(manager_api) -> None:
    handler = manager_api.handler
    cfg, rt = _uuid("cfg-a"), _uuid("rt-a")
    await _seed_cfg(handler, cfg, "Cfg A")
    await _seed_rt(handler, rt, "Rt A")
    now = utc_now()
    await handler.create(
        "agent_definition",
        {
            "agent_id": build_agent_id(rt, cfg),
            "agent_name": "Legacy Name",
            "agent_config_template_id": cfg,
            "runtime_template_id": rt,
            "agent_config_template_name": "old snapshot",
            "runtime_template_name": "old snapshot",
            "deleted": True,
            "deleted_reason": "upstream_unavailable",
            "created_at": now,
            "updated_at": now,
        },
    )
    service = AgentDefinitionService(handler)

    page = await service.list(AgentDefinitionListQuery())
    assert page["total"] == 1
    assert page["items"][0]["agent_name"] == "Legacy Name"
    # 名称按模板 id 实时取，不再使用库内快照。
    assert page["items"][0]["agent_config_template_name"] == "Cfg A"

    renamed = await service.rename(build_agent_id(rt, cfg), "Legacy Renamed")
    assert renamed is not None
    assert await service.delete(build_agent_id(rt, cfg)) is True


@pytest.mark.asyncio
async def test_api_create_rename_delete_status_codes(manager_api) -> None:
    handler = manager_api.handler
    cfg, rt = _uuid("cfg-a"), _uuid("rt-a")
    await _seed_cfg(handler, cfg, "Cfg A")
    await _seed_rt(handler, rt, "Rt A")

    resp = await manager_api.http.post(
        "/api/v1/agent-definitions/",
        json={
            "agent_config_template_id": cfg,
            "runtime_template_id": rt,
            "agent_name": "   ",
        },
    )
    assert resp.status_code == 422

    resp = await manager_api.http.post(
        "/api/v1/agent-definitions/",
        json={
            "agent_config_template_id": cfg,
            "runtime_template_id": rt,
            "agent_name": " Sales ",
        },
    )
    assert resp.status_code == 200
    created = resp.json()["data"]
    assert created["agent_name"] == "Sales"
    agent_id = created["agent_id"]

    resp = await manager_api.http.post(
        "/api/v1/agent-definitions/",
        json={
            "agent_config_template_id": cfg,
            "runtime_template_id": rt,
            "agent_name": "Other",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "agent_id_duplicated"

    resp = await manager_api.http.patch(
        f"/api/v1/agent-definitions/{agent_id}", json={"agent_name": "Sales Renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["agent_name"] == "Sales Renamed"

    resp = await manager_api.http.patch(
        "/api/v1/agent-definitions/missing", json={"agent_name": "X"}
    )
    assert resp.status_code == 404

    resp = await manager_api.http.delete(f"/api/v1/agent-definitions/{agent_id}")
    assert resp.status_code == 200
    resp = await manager_api.http.delete(f"/api/v1/agent-definitions/{agent_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_maps_unique_violation_to_conflict(manager_api, monkeypatch) -> None:
    handler = manager_api.handler
    cfg, rt = _uuid("cfg-integrity"), _uuid("rt-integrity")
    await _seed_cfg(handler, cfg, "Cfg A")
    await _seed_rt(handler, rt, "Rt A")
    service = AgentDefinitionService(handler)
    original = handler.create

    async def boom(table, payload):
        if table == "agent_definition":
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        return await original(table, payload)

    monkeypatch.setattr(handler, "create", boom)
    with pytest.raises(AgentDefinitionExists):
        await service.create(cfg, rt, "Agent")


@pytest.mark.asyncio
async def test_template_delete_and_disable_blocked_by_reference(manager_api) -> None:
    handler = manager_api.handler
    cfg, rt = _uuid("cfg-a"), _uuid("rt-a")
    await _seed_cfg(handler, cfg, "Cfg A")
    await _seed_rt(handler, rt, "Rt A")
    await AgentDefinitionService(handler).create(cfg, rt, "Sales")

    resp = await manager_api.http.patch(
        f"/api/v1/agent-templates/{cfg}", json={"enabled": False}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "agent_definitions_referenced"
    assert resp.json()["detail"]["count"] == 1

    resp = await manager_api.http.delete(f"/api/v1/agent-templates/{cfg}")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "agent_definitions_referenced"

    resp = await manager_api.http.patch(
        f"/api/v1/service-config-templates/{rt}", json={"enabled": False}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "agent_definitions_referenced"
    assert resp.json()["detail"]["count"] == 1

    resp = await manager_api.http.delete(f"/api/v1/service-config-templates/{rt}")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "agent_definitions_referenced"
