"""User Console accessible agent-context enumeration tests."""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from manager_server.core.instance_resource import runtime_config_sync
from manager_server.core.user_console import UserConsoleService
from manager_server.core.user_console.agent_identity import build_agent_id
from manager_server.core.instance_access import InstanceGrantService
from manager_server.core.user_console import services as user_console_services

pytestmark = pytest.mark.unit


def _uuid(label: str) -> str:
    """稳定的 UUID 形式的模板 id，便于测试里按语义引用。"""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, label))


def _resource(
    *,
    resource_id: str,
    jiuwenclaw_id: str = "gw-1",
    resource_name: str = "",
    ref_template_id: str = "",
    match_expr: object = None,
    enabled: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        jiuwenclaw_id=jiuwenclaw_id,
        resource_id=resource_id,
        resource_name=resource_name or resource_id,
        ref_template_id=ref_template_id,
        match_expr=match_expr if match_expr is not None else [],
        enabled=enabled,
        expires_at=None,
    )


def _service_resource(
    *,
    resource_id: str,
    ref_template_id: str,
    priority: int = 0,
    match_expr: object = None,
    enabled: bool = True,
    expires_at: object = None,
    jiuwenclaw_id: str = "gw-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        jiuwenclaw_id=jiuwenclaw_id,
        resource_id=resource_id,
        ref_template_id=ref_template_id,
        priority=priority,
        match_expr=match_expr if match_expr is not None else [],
        enabled=enabled,
        expires_at=expires_at,
    )


def _service_template(template_id: str, *, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(template_id=template_id, enabled=enabled)


def _instance(jiuwenclaw_id: str = "gw-1", jiuwenclaw_name: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        jiuwenclaw_id=jiuwenclaw_id,
        jiuwenclaw_name=jiuwenclaw_name or jiuwenclaw_id,
    )


def _filter_resources(table: str, resources: list[SimpleNamespace], args, kwargs):
    if table == "instance_info":
        return []
    filters = kwargs.get("filters")
    if filters is None and len(args) >= 1 and isinstance(args[0], dict):
        filters = args[0]
    if not isinstance(filters, dict):
        return resources
    jid = filters.get("jiuwenclaw_id")
    if not jid:
        return resources
    return [r for r in resources if r.jiuwenclaw_id == jid]


@pytest.mark.asyncio
async def test_enumerates_matching_group_and_user_contexts() -> None:
    agent_tpl = _uuid("agent-tpl-A")
    resources = [
        _resource(
            resource_id="bot-sales",
            resource_name="Sales",
            ref_template_id=agent_tpl,
            match_expr="group_id in ('g-sales')",
        ),
        _resource(
            resource_id="bot-alice",
            resource_name="Alice",
            ref_template_id=agent_tpl,
            match_expr="user_id == 'alice'",
        ),
        _resource(
            resource_id="bot-all",
            resource_name="All",
            ref_template_id=agent_tpl,
            match_expr=[],
        ),
    ]
    instances = [_instance("gw-1", "集群一号")]
    definitions = [_definition(agent_tpl, "", "Agent One")]

    async def list_records(table: str, *args, **kwargs):
        if table == "instance_info":
            return instances
        if table == "agent_definition":
            return definitions
        return _filter_resources(table, resources, args, kwargs)

    handler = AsyncMock()
    handler.list_records = AsyncMock(side_effect=list_records)
    service = UserConsoleService(handler)

    with (
        patch.object(
            user_console_services,
            "_load_orgs",
            AsyncMock(
                return_value=[
                    {"group_id": "g-sales", "group_name": "销售组"},
                    {"group_id": "g-other", "group_name": "其他组"},
                ]
            ),
        ),
        patch.object(
            InstanceGrantService,
            "list_instances_for",
            AsyncMock(side_effect=[{"alice": ["gw-1"]}, {"g-sales": ["gw-1"], "g-other": []}]),
        ),
        patch.object(InstanceGrantService, "is_admitted", AsyncMock(return_value=True)),
    ):
        result = await service.list_accessible_contexts(
            "alice",
            ["g-sales", "g-other"],
            authorization="Bearer t",
        )

    keys = {(x["bot_id"], x["group_id"], x["user_id"]) for x in result}
    assert ("bot-sales", "g-sales", "alice") in keys
    assert ("bot-sales", "g-other", "alice") not in keys
    assert ("bot-alice", "g-sales", "alice") in keys
    assert ("bot-alice", "g-other", "alice") in keys
    assert ("bot-all", "g-sales", "alice") in keys
    assert ("bot-all", "g-other", "alice") in keys
    assert all(x["jiuwenclaw_id"] == "gw-1" for x in result)
    assert all(x["jiuwenclaw_name"] == "集群一号" for x in result)
    sales = next(x for x in result if x["bot_id"] == "bot-sales")
    # 名称统一取管理面 Agent 模板中的名称。
    assert sales["agent_name"] == "Agent One"
    assert sales["agent_id"] == build_agent_id("", agent_tpl)
    assert sales["group_name"] == "销售组"


@pytest.mark.asyncio
async def test_no_org_hides_unconfigured_agent() -> None:
    resources = [_resource(resource_id="bot-1", match_expr=[])]
    instances = [_instance("gw-1", "Debug Cluster")]

    async def list_records(table: str, *args, **kwargs):
        if table == "instance_info":
            return instances
        if table == "agent_definition":
            return []
        return _filter_resources(table, resources, args, kwargs)

    handler = AsyncMock()
    handler.list_records = AsyncMock(side_effect=list_records)
    service = UserConsoleService(handler)

    with (
        patch.object(user_console_services, "_load_orgs", AsyncMock(return_value=[])),
        patch.object(
            InstanceGrantService,
            "list_instances_for",
            AsyncMock(return_value={"alice": ["gw-1"]}),
        ),
        patch.object(InstanceGrantService, "is_admitted", AsyncMock(return_value=True)),
    ):
        result = await service.list_accessible_contexts("alice", [])

    # 未在管理面新增 Agent 模板的组合不展示。
    assert result == []


@pytest.mark.asyncio
async def test_empty_when_not_admitted() -> None:
    handler = AsyncMock()
    service = UserConsoleService(handler)

    with (
        patch.object(
            user_console_services,
            "_load_orgs",
            AsyncMock(return_value=[{"group_id": "g-sales", "group_name": "销售组"}]),
        ),
        patch.object(
            InstanceGrantService,
            "list_instances_for",
            AsyncMock(return_value={"alice": ["gw-1"]}),
        ),
        patch.object(InstanceGrantService, "is_admitted", AsyncMock(return_value=False)),
    ):
        result = await service.list_accessible_contexts(
            "alice", ["g-sales"], authorization="Bearer t"
        )

    assert result == []
    handler.list_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_no_longer_bypasses_admission() -> None:
    """平台管理员也须走 instance_grant，不再因 is_admin 短路。"""
    handler = AsyncMock()
    service = UserConsoleService(handler)

    with (
        patch.object(
            user_console_services,
            "_load_orgs",
            AsyncMock(return_value=[{"group_id": "g-any", "group_name": "任意组"}]),
        ),
        patch.object(
            InstanceGrantService,
            "list_instances_for",
            AsyncMock(return_value={}),
        ),
        patch.object(InstanceGrantService, "is_admitted", AsyncMock(return_value=False)),
    ):
        result = await service.list_accessible_contexts(
            "admin", ["g-any"], authorization="Bearer t"
        )

    assert result == []


def _definition(cfg_id: str, rt_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=build_agent_id(rt_id, cfg_id),
        agent_name=name,
        agent_config_template_id=cfg_id,
        runtime_template_id=rt_id,
        deleted=False,
    )


def _context_handler(
    agent_resources: list[SimpleNamespace],
    service_resources: list[SimpleNamespace],
    templates: dict[str, SimpleNamespace],
    instances: list[SimpleNamespace] | None = None,
    definitions: list[SimpleNamespace] | None = None,
) -> AsyncMock:
    instances = instances or [_instance("gw-1", "集群一号")]
    definitions = definitions or []

    async def list_records(table: str, *args, **kwargs):
        filters = kwargs.get("filters")
        if filters is None and args and isinstance(args[0], dict):
            filters = args[0]
        if table == "instance_info":
            return instances
        if table == "agent_definition":
            return definitions
        source = (
            service_resources
            if table == "instance_service_resource"
            else agent_resources
        )
        jid = (filters or {}).get("jiuwenclaw_id")
        return [row for row in source if not jid or row.jiuwenclaw_id == jid]

    async def get(table: str, filters: dict):
        if table == "service_config_template":
            return templates.get(str(filters.get("template_id") or ""))
        return None

    handler = AsyncMock()
    handler.list_records = AsyncMock(side_effect=list_records)
    handler.get = AsyncMock(side_effect=get)
    return handler


def _apply_common_patches(stack: ExitStack) -> None:
    stack.enter_context(
        patch.object(
            user_console_services,
            "_load_orgs",
            AsyncMock(return_value=[{"group_id": "g-1", "group_name": "组一"}]),
        )
    )
    stack.enter_context(
        patch.object(
            InstanceGrantService,
            "list_instances_for",
            AsyncMock(return_value={"alice": ["gw-1"]}),
        )
    )
    stack.enter_context(
        patch.object(InstanceGrantService, "is_admitted", AsyncMock(return_value=True))
    )


@pytest.mark.asyncio
async def test_agent_name_combines_runtime_and_agent_templates() -> None:
    runtime_a = _uuid("runtime-A")
    runtime_b = _uuid("runtime-B")
    agent_tpl = _uuid("agent-tpl-A")
    agents = [
        _resource(
            resource_id="bot-1",
            ref_template_id=agent_tpl,
            match_expr=[],
        )
    ]
    services = [
        _service_resource(resource_id="svc-low", ref_template_id=runtime_b, priority=10),
        _service_resource(resource_id="svc-high", ref_template_id=runtime_a, priority=1),
    ]
    templates = {
        runtime_a: _service_template(runtime_a),
        runtime_b: _service_template(runtime_b),
    }
    handler = _context_handler(
        agents,
        services,
        templates,
        definitions=[_definition(agent_tpl, runtime_a, "Sales Agent")],
    )
    service = UserConsoleService(handler)

    with ExitStack() as stack:
        _apply_common_patches(stack)
        stack.enter_context(
            patch.object(
                runtime_config_sync,
                "service_template_wire",
                AsyncMock(return_value=({}, [])),
            )
        )
        result = await service.list_accessible_contexts("alice", ["g-1"])

    assert len(result) == 1
    item = result[0]
    assert item["agent_template_id"] == agent_tpl
    assert item["runtime_template_id"] == runtime_a
    assert item["agent_id"] == build_agent_id(runtime_a, agent_tpl)
    assert item["agent_name"] == "Sales Agent"
    assert item["resource_name"] == "bot-1"


@pytest.mark.asyncio
async def test_agent_name_priority_tie_breaks_by_scope_id() -> None:
    runtime_a = _uuid("runtime-A")
    runtime_z = _uuid("runtime-Z")
    agent_tpl = _uuid("agent-tpl-A")
    agents = [_resource(resource_id="bot-1", ref_template_id=agent_tpl)]
    services = [
        _service_resource(resource_id="z", ref_template_id=runtime_z, priority=1),
        _service_resource(resource_id="a", ref_template_id=runtime_a, priority=1),
    ]
    templates = {
        runtime_a: _service_template(runtime_a),
        runtime_z: _service_template(runtime_z),
    }
    handler = _context_handler(
        agents,
        services,
        templates,
        definitions=[_definition(agent_tpl, runtime_a, "Tie Agent")],
    )
    service = UserConsoleService(handler)

    with ExitStack() as stack:
        _apply_common_patches(stack)
        stack.enter_context(
            patch.object(
                runtime_config_sync,
                "service_template_wire",
                AsyncMock(return_value=({}, [])),
            )
        )
        result = await service.list_accessible_contexts("alice", ["g-1"])

    assert result[0]["runtime_template_id"] == runtime_a
    assert result[0]["agent_id"] == build_agent_id(runtime_a, agent_tpl)
    assert result[0]["agent_name"] == "Tie Agent"


@pytest.mark.asyncio
async def test_agent_name_skips_disabled_expired_and_unusable_runtime_scopes() -> None:
    agent_tpl = _uuid("agent-tpl-A")
    runtime_ok = _uuid("runtime-ok")
    ids: dict[str, str] = {}
    for name in (
        "runtime-disabled",
        "runtime-expired",
        "runtime-bot2",
        "runtime-disabled-tpl",
        "runtime-unwireable",
    ):
        ids[name] = _uuid(name)
    agents = [_resource(resource_id="bot-1", ref_template_id=agent_tpl)]
    services = [
        _service_resource(
            resource_id="svc-disabled", ref_template_id=ids["runtime-disabled"], enabled=False
        ),
        _service_resource(
            resource_id="svc-expired",
            ref_template_id=ids["runtime-expired"],
            expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        ),
        _service_resource(
            resource_id="svc-bot2",
            ref_template_id=ids["runtime-bot2"],
            match_expr="bot_id == 'bot-2'",
        ),
        _service_resource(
            resource_id="svc-disabled-tpl", ref_template_id=ids["runtime-disabled-tpl"]
        ),
        _service_resource(
            resource_id="svc-unwireable", ref_template_id=ids["runtime-unwireable"]
        ),
        _service_resource(resource_id="svc-ok", ref_template_id=runtime_ok, priority=5),
    ]
    templates = {
        ids["runtime-disabled"]: _service_template(ids["runtime-disabled"], enabled=False),
        ids["runtime-expired"]: _service_template(ids["runtime-expired"]),
        ids["runtime-bot2"]: _service_template(ids["runtime-bot2"]),
        ids["runtime-disabled-tpl"]: _service_template(
            ids["runtime-disabled-tpl"], enabled=False
        ),
        ids["runtime-unwireable"]: _service_template(ids["runtime-unwireable"]),
        runtime_ok: _service_template(runtime_ok),
    }

    async def wire(_handler, template):
        if template.template_id == ids["runtime-unwireable"]:
            return None
        return ({}, [])

    handler = _context_handler(
        agents,
        services,
        templates,
        definitions=[_definition(agent_tpl, runtime_ok, "Runtime OK")],
    )
    service = UserConsoleService(handler)

    with ExitStack() as stack:
        _apply_common_patches(stack)
        stack.enter_context(
            patch.object(
                runtime_config_sync,
                "service_template_wire",
                AsyncMock(side_effect=wire),
            )
        )
        result = await service.list_accessible_contexts("alice", ["g-1"])

    assert result[0]["runtime_template_id"] == runtime_ok
    assert result[0]["agent_id"] == build_agent_id(runtime_ok, agent_tpl)
    assert result[0]["agent_name"] == "Runtime OK"


@pytest.mark.asyncio
async def test_agent_name_uses_definition_for_empty_runtime_template() -> None:
    agent_tpl = _uuid("agent-tpl-A")
    agents = [_resource(resource_id="bot-1", ref_template_id=agent_tpl)]
    handler = _context_handler(
        agents, [], {}, definitions=[_definition(agent_tpl, "", "Empty Runtime")]
    )
    service = UserConsoleService(handler)

    with ExitStack() as stack:
        _apply_common_patches(stack)
        stack.enter_context(
            patch.object(
                runtime_config_sync,
                "service_template_wire",
                AsyncMock(return_value=({}, [])),
            )
        )
        result = await service.list_accessible_contexts("alice", ["g-1"])

    item = result[0]
    assert item["runtime_template_id"] == ""
    assert item["agent_id"] == build_agent_id("", agent_tpl)
    assert item["agent_name"] == "Empty Runtime"


@pytest.mark.asyncio
async def test_agent_name_comes_from_agent_definition() -> None:
    runtime_a = _uuid("runtime-A")
    agent_tpl = _uuid("agent-tpl-A")
    agents = [_resource(resource_id="bot-1", ref_template_id=agent_tpl)]
    services = [_service_resource(resource_id="svc", ref_template_id=runtime_a)]
    templates = {runtime_a: _service_template(runtime_a)}
    handler = _context_handler(
        agents,
        services,
        templates,
        definitions=[_definition(agent_tpl, runtime_a, "Sales Agent")],
    )
    service = UserConsoleService(handler)

    with ExitStack() as stack:
        _apply_common_patches(stack)
        stack.enter_context(
            patch.object(
                runtime_config_sync,
                "service_template_wire",
                AsyncMock(return_value=({}, [])),
            )
        )
        result = await service.list_accessible_contexts("alice", ["g-1"])

    item = result[0]
    assert item["agent_id"] == build_agent_id(runtime_a, agent_tpl)
    assert item["agent_name"] == "Sales Agent"


@pytest.mark.asyncio
async def test_agent_without_definition_is_hidden() -> None:
    agents = [_resource(resource_id="bot-1", resource_name="Sales", ref_template_id="legacy-tpl")]
    handler = _context_handler(agents, [], {})
    service = UserConsoleService(handler)

    with ExitStack() as stack:
        _apply_common_patches(stack)
        stack.enter_context(
            patch.object(
                runtime_config_sync,
                "service_template_wire",
                AsyncMock(return_value=({}, [])),
            )
        )
        result = await service.list_accessible_contexts("alice", ["g-1"])

    # 无法推导 agent_id 的组合无法在管理面新增，用户面不展示。
    assert result == []


def _list_meta_patches() -> ExitStack:
    stack = ExitStack()
    _apply_common_patches(stack)
    stack.enter_context(
        patch.object(
            runtime_config_sync,
            "service_template_wire",
            AsyncMock(return_value=({}, [])),
        )
    )
    return stack


@pytest.mark.asyncio
async def test_empty_reason_no_grant_when_nothing_matches() -> None:
    agent_tpl = _uuid("agent-tpl-A")
    agents = [
        _resource(
            resource_id="bot-1",
            ref_template_id=agent_tpl,
            match_expr="group_id == 'other'",
        )
    ]
    service = UserConsoleService(_context_handler(agents, [], {}))

    with _list_meta_patches():
        data = await service.list_accessible_contexts_with_meta("alice", ["g-1"])

    assert data["contexts"] == []
    assert data["empty_reason"] == "no_grant"


@pytest.mark.asyncio
async def test_empty_reason_no_agent_template_when_matched_but_unconfigured() -> None:
    agent_tpl = _uuid("agent-tpl-A")
    agents = [_resource(resource_id="bot-1", ref_template_id=agent_tpl, match_expr=[])]
    service = UserConsoleService(_context_handler(agents, [], {}))

    with _list_meta_patches():
        data = await service.list_accessible_contexts_with_meta("alice", ["g-1"])

    assert data["contexts"] == []
    assert data["empty_reason"] == "no_agent_template"


@pytest.mark.asyncio
async def test_empty_reason_none_when_contexts_present() -> None:
    agent_tpl = _uuid("agent-tpl-A")
    agents = [_resource(resource_id="bot-1", ref_template_id=agent_tpl, match_expr=[])]
    service = UserConsoleService(
        _context_handler(
            agents, [], {}, definitions=[_definition(agent_tpl, "", "Agent One")]
        )
    )

    with _list_meta_patches():
        data = await service.list_accessible_contexts_with_meta("alice", ["g-1"])

    assert len(data["contexts"]) == 1
    assert data["empty_reason"] is None
