"""Agent 模板（agent_definition）同步、查询与改名测试。"""

from __future__ import annotations

import uuid

import pytest

from manager_server.core.agent_definition.service import (
    AgentDefinitionService,
    AgentNameDuplicated,
)
from manager_server.core.agent_definition.sync import reconcile_agent_definitions
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
async def test_reconcile_creates_cartesian_product(manager_api) -> None:
    handler = manager_api.handler
    cfg_a, cfg_b = _uuid("cfg-a"), _uuid("cfg-b")
    rt_a, rt_b = _uuid("rt-a"), _uuid("rt-b")
    await _seed_cfg(handler, cfg_a, "Cfg A")
    await _seed_cfg(handler, cfg_b, "Cfg B")
    await _seed_rt(handler, rt_a, "Rt A")
    await _seed_rt(handler, rt_b, "Rt B")

    counts = await reconcile_agent_definitions(handler)

    assert counts["created"] == 4
    rows = await _rows(handler)
    assert {
        (row.agent_config_template_id, row.runtime_template_id) for row in rows
    } == {(c, r) for c in (cfg_a, cfg_b) for r in (rt_a, rt_b)}
    assert all(row.agent_name == f"agent_{row.agent_id}" for row in rows)
    assert all(row.deleted is False for row in rows)
    assert all(row.agent_config_template_name for row in rows)
    assert all(row.runtime_template_name for row in rows)


@pytest.mark.asyncio
async def test_reconcile_disable_and_restore_keeps_custom_name(manager_api) -> None:
    handler = manager_api.handler
    cfg, rt = _uuid("cfg-a"), _uuid("rt-a")
    await _seed_cfg(handler, cfg, "Cfg A")
    await _seed_rt(handler, rt, "Rt A")
    await reconcile_agent_definitions(handler)
    row = (await _rows(handler))[0]

    service = AgentDefinitionService(handler)
    renamed = await service.rename(row.agent_id, "My Agent")
    assert renamed is not None and renamed["agent_name"] == "My Agent"

    await handler.update("agent_template", {"template_id": cfg}, {"enabled": False})
    counts = await reconcile_agent_definitions(handler)
    assert counts["disabled"] == 1
    row = (await _rows(handler))[0]
    assert row.deleted is True
    assert row.deleted_reason == "upstream_unavailable"
    assert row.agent_name == "My Agent"  # 失效仍保留自定义名

    await handler.update("agent_template", {"template_id": cfg}, {"enabled": True})
    counts = await reconcile_agent_definitions(handler)
    assert counts["restored"] == 1
    row = (await _rows(handler))[0]
    assert row.deleted is False
    assert row.agent_name == "My Agent"


@pytest.mark.asyncio
async def test_reconcile_restore_falls_back_to_default_name_on_conflict(manager_api) -> None:
    """恢复时自定义名已被有效行占用：回退默认名并恢复，不抢别人名字。"""
    handler = manager_api.handler
    cfg_a, cfg_b, rt = _uuid("cfg-dup-a"), _uuid("cfg-dup-b"), _uuid("rt-dup")
    await _seed_cfg(handler, cfg_a, "Cfg A")
    await _seed_cfg(handler, cfg_b, "Cfg B")
    await _seed_rt(handler, rt, "Rt A")
    await reconcile_agent_definitions(handler)

    target = next(
        row for row in await _rows(handler) if row.agent_config_template_id == cfg_a
    )
    other = next(
        row for row in await _rows(handler) if row.agent_config_template_id == cfg_b
    )
    default_name = target.agent_name
    agent_id = target.agent_id
    service = AgentDefinitionService(handler)
    await service.rename(agent_id, "Shared")

    await handler.update("agent_template", {"template_id": cfg_a}, {"enabled": False})
    assert (await reconcile_agent_definitions(handler))["disabled"] == 1

    # 失效行的名字不占名，另一个有效行可以占用它
    await service.rename(other.agent_id, "Shared")

    await handler.update("agent_template", {"template_id": cfg_a}, {"enabled": True})
    counts = await reconcile_agent_definitions(handler)
    assert counts["restored"] == 1
    assert counts["renamed"] == 1
    assert counts["conflicted"] == 0

    target = next(
        row for row in await _rows(handler) if row.agent_config_template_id == cfg_a
    )
    other = next(
        row for row in await _rows(handler) if row.agent_config_template_id == cfg_b
    )
    assert target.deleted is False
    assert target.agent_name == default_name
    assert other.agent_name == "Shared"


@pytest.mark.asyncio
async def test_reconcile_restore_stays_disabled_when_default_name_taken(manager_api) -> None:
    """自定义名与默认名同时被占用：保持失效，不覆盖任何有效行。"""
    handler = manager_api.handler
    cfg_a, cfg_b, cfg_c = _uuid("cfg-taken-a"), _uuid("cfg-taken-b"), _uuid("cfg-taken-c")
    rt = _uuid("rt-taken")
    await _seed_cfg(handler, cfg_a, "Cfg A")
    await _seed_cfg(handler, cfg_b, "Cfg B")
    await _seed_cfg(handler, cfg_c, "Cfg C")
    await _seed_rt(handler, rt, "Rt A")
    await reconcile_agent_definitions(handler)

    target = next(
        row for row in await _rows(handler) if row.agent_config_template_id == cfg_a
    )
    agent_id = target.agent_id
    default_name = target.agent_name
    service = AgentDefinitionService(handler)
    await service.rename(agent_id, "Shared")

    await handler.update("agent_template", {"template_id": cfg_a}, {"enabled": False})
    assert (await reconcile_agent_definitions(handler))["disabled"] == 1

    rows = await _rows(handler)
    second = next(row for row in rows if row.agent_config_template_id == cfg_b)
    third = next(row for row in rows if row.agent_config_template_id == cfg_c)
    await service.rename(second.agent_id, "Shared")
    await service.rename(third.agent_id, default_name)

    await handler.update("agent_template", {"template_id": cfg_a}, {"enabled": True})
    counts = await reconcile_agent_definitions(handler)
    assert counts["restored"] == 0
    assert counts["renamed"] == 0
    assert counts["conflicted"] == 1

    target = next(
        row for row in await _rows(handler) if row.agent_config_template_id == cfg_a
    )
    assert target.deleted is True
    assert target.deleted_reason == "upstream_unavailable"
    assert target.agent_name == "Shared"


@pytest.mark.asyncio
async def test_reconcile_refreshes_snapshot_names_and_is_idempotent(manager_api) -> None:
    handler = manager_api.handler
    cfg, rt = _uuid("cfg-a"), _uuid("rt-a")
    await _seed_cfg(handler, cfg, "Cfg Old")
    await _seed_rt(handler, rt, "Rt Old")
    await reconcile_agent_definitions(handler)

    await handler.update("agent_template", {"template_id": cfg}, {"template_name": "Cfg New"})
    counts = await reconcile_agent_definitions(handler)
    assert counts["updated"] == 1
    row = (await _rows(handler))[0]
    assert row.agent_config_template_name == "Cfg New"
    assert row.runtime_template_name == "Rt Old"

    counts = await reconcile_agent_definitions(handler)
    assert counts == {
        "created": 0,
        "restored": 0,
        "renamed": 0,
        "updated": 0,
        "disabled": 0,
        "conflicted": 0,
        "failed": 0,
    }


@pytest.mark.asyncio
async def test_disabled_source_templates_do_not_generate_rows(manager_api) -> None:
    handler = manager_api.handler
    await _seed_cfg(handler, _uuid("cfg-a"), "Cfg A", enabled=False)
    await _seed_rt(handler, _uuid("rt-a"), "Rt A")
    await reconcile_agent_definitions(handler)
    assert await _rows(handler) == []


@pytest.mark.asyncio
async def test_rename_rejects_case_insensitive_duplicate(manager_api) -> None:
    handler = manager_api.handler
    cfg, rt_a, rt_b = _uuid("cfg-a"), _uuid("rt-a"), _uuid("rt-b")
    await _seed_cfg(handler, cfg, "Cfg A")
    await _seed_rt(handler, rt_a, "Rt A")
    await _seed_rt(handler, rt_b, "Rt B")
    await reconcile_agent_definitions(handler)
    rows = await _rows(handler)
    first, second = rows[0], rows[1]

    service = AgentDefinitionService(handler)
    await service.rename(first.agent_id, "Sales")
    with pytest.raises(AgentNameDuplicated):
        await service.rename(second.agent_id, "sALES")


@pytest.mark.asyncio
async def test_rename_ignores_disabled_rows(manager_api) -> None:
    handler = manager_api.handler
    cfg, rt = _uuid("cfg-a"), _uuid("rt-a")
    await _seed_cfg(handler, cfg, "Cfg A")
    await _seed_rt(handler, rt, "Rt A")
    await reconcile_agent_definitions(handler)
    row = (await _rows(handler))[0]
    await handler.update(
        "agent_definition",
        {"agent_id": row.agent_id},
        {"deleted": True, "deleted_reason": "upstream_unavailable"},
    )
    service = AgentDefinitionService(handler)
    assert await service.rename(row.agent_id, "Ghost") is None


@pytest.mark.asyncio
async def test_list_paginates_and_searches(manager_api) -> None:
    handler = manager_api.handler
    cfg = _uuid("cfg-a")
    await _seed_cfg(handler, cfg, "Cfg A")
    for index in range(3):
        await _seed_rt(handler, _uuid(f"rt-{index}"), f"Rt {index}")
    await reconcile_agent_definitions(handler)
    service = AgentDefinitionService(handler)

    page = await service.list(AgentDefinitionListQuery(page=1, page_size=2))
    assert page["total"] == 3 and len(page["items"]) == 2

    rows = await _rows(handler)
    target = rows[0].agent_id
    found = await service.list(AgentDefinitionListQuery(search=target[:10]))
    assert found["total"] >= 1
    assert all(target[:10] in item["agent_id"] for item in found["items"])


@pytest.mark.asyncio
async def test_api_list_rename_and_conflicts(manager_api) -> None:
    handler = manager_api.handler
    cfg, rt_a, rt_b = _uuid("cfg-a"), _uuid("rt-a"), _uuid("rt-b")
    await _seed_cfg(handler, cfg, "Cfg A")
    await _seed_rt(handler, rt_a, "Rt A")
    await _seed_rt(handler, rt_b, "Rt B")
    await reconcile_agent_definitions(handler)
    rows = await _rows(handler)
    first, second = rows[0], rows[1]

    resp = await manager_api.http.get("/api/v1/agent-definitions/")
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 2

    resp = await manager_api.http.patch(
        f"/api/v1/agent-definitions/{first.agent_id}", json={"agent_name": " Sales "}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["agent_name"] == "Sales"

    resp = await manager_api.http.patch(
        f"/api/v1/agent-definitions/{second.agent_id}", json={"agent_name": "sales"}
    )
    assert resp.status_code == 409

    resp = await manager_api.http.patch(
        f"/api/v1/agent-definitions/{second.agent_id}", json={"agent_name": "   "}
    )
    assert resp.status_code == 400

    resp = await manager_api.http.patch(
        "/api/v1/agent-definitions/unknown", json={"agent_name": "x"}
    )
    assert resp.status_code == 404
