# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""高级配置模板：新建不下发；Gateway 失败不改 Manager 行；被引用时拒禁用/删除。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from manager_server.core.template import advanced_config_template as adv_mod
from manager_server.core.template.advanced_config_template import (
    AdvancedConfigTemplateService,
    row_to_out,
)
from manager_server.core.template.errors import TemplateInUseError
from manager_server.core.template.push_template_to_gateway import (
    AGENT_TEMPLATES_KIND,
    TEMPLATE_KIND_ORDER,
    TEMPLATE_KIND_SPECS,
    _ROW_TO_OUT_MODULES,
    _TEMPLATE_HTTP_PATHS,
)
from manager_server.schemas.advanced_config_fields import DEFAULT_ADVANCED_CONFIG_SWITCHES
from manager_server.schemas.template_schemas import (
    AdvancedConfigTemplateCreateBody,
    AdvancedConfigTemplateUpdateBody,
)
from manager_server.schemas.template_slot_schemas import ADVANCED_CONFIG_SLOT


class _MemoryHandler:
    def __init__(self, row: SimpleNamespace | None = None) -> None:
        self.row = row
        self.created: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, dict, dict]] = []
        self.deleted: list[tuple[str, dict]] = []

    async def create(self, table: str, payload: dict) -> SimpleNamespace:
        self.created.append((table, payload))
        data = dict(payload)
        data.setdefault("id", 1)
        self.row = SimpleNamespace(**data)
        return self.row

    async def get(self, table: str, filters: dict) -> SimpleNamespace | None:
        return self.row

    async def update(self, table: str, filters: dict, payload: dict) -> SimpleNamespace | None:
        self.updated.append((table, filters, payload))
        base = vars(self.row).copy() if self.row is not None else {}
        base.update(payload)
        self.row = SimpleNamespace(**base)
        return self.row

    async def delete(self, table: str, filters: dict) -> bool:
        self.deleted.append((table, filters))
        self.row = None
        return True


def _advanced_row(**overrides: object) -> SimpleNamespace:
    base = {
        "id": 1,
        "template_id": "adv-1",
        "template_name": "高级配置",
        "description": None,
        "enabled": True,
        "progressive_tool_enabled": True,
        "progressive_tool_optimize": False,
        "a4p_enabled": False,
        "auto_memory_enabled": False,
        "enable_kv_cache_affinity": False,
        "data": None,
        "created_at": None,
        "updated_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def gateway_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _update(*_args, **_kwargs) -> None:
        calls.append("update")

    async def _delete(*_args, **_kwargs) -> None:
        calls.append("delete")

    async def _counts(*_args, **_kwargs) -> dict[str, int]:
        return {}

    async def _assert_not_referenced(*_args, **_kwargs) -> None:
        calls.append("assert_not_referenced")

    async def _assert_deletable(*_args, **_kwargs) -> None:
        calls.append("assert_deletable")

    monkeypatch.setattr(adv_mod, "update_template_on_referencing_gateways", _update)
    monkeypatch.setattr(adv_mod, "delete_template_on_referencing_gateways", _delete)
    monkeypatch.setattr(adv_mod, "count_agent_template_reference_counts", _counts)
    monkeypatch.setattr(adv_mod, "assert_template_not_referenced", _assert_not_referenced)
    monkeypatch.setattr(adv_mod, "assert_template_deletable", _assert_deletable)
    return calls


def test_kind_is_registered_before_agent_templates() -> None:
    assert "advanced_config_templates" in TEMPLATE_KIND_ORDER
    assert TEMPLATE_KIND_ORDER.index("advanced_config_templates") < TEMPLATE_KIND_ORDER.index(
        AGENT_TEMPLATES_KIND
    )
    spec = TEMPLATE_KIND_SPECS["advanced_config_templates"]
    assert ADVANCED_CONFIG_SLOT in spec.slot_keys
    assert _TEMPLATE_HTTP_PATHS["advanced_config_templates"] == (
        "/api/v1/advanced-config-templates"
    )


def test_row_to_out_is_loadable_via_push_module_map() -> None:
    module_name = _ROW_TO_OUT_MODULES["advanced_config_templates"]
    assert module_name == "manager_server.core.template.advanced_config_template"
    module = importlib.import_module(module_name)
    loaded = getattr(module, "row_to_out")
    assert loaded is row_to_out
    out = loaded(_advanced_row())
    assert out.template_id == "adv-1"
    assert out.progressive_tool_enabled is True
    assert out.a4p_enabled is False


@pytest.mark.asyncio
async def test_create_uses_switch_defaults_and_skips_gateway(
    gateway_calls: list[str],
) -> None:
    handler = _MemoryHandler()
    service = AdvancedConfigTemplateService(handler)
    out = await service.create(
        AdvancedConfigTemplateCreateBody(template_name="默认高级配置")
    )
    assert gateway_calls == []
    assert len(handler.created) == 1
    payload = handler.created[0][1]
    for key, expected in DEFAULT_ADVANCED_CONFIG_SWITCHES.items():
        assert payload[key] is expected
        assert getattr(out, key) is expected
    assert payload["data"] is None


@pytest.mark.asyncio
async def test_patch_normalizes_switch_and_pushes_before_db(
    gateway_calls: list[str],
) -> None:
    handler = _MemoryHandler(_advanced_row())
    service = AdvancedConfigTemplateService(handler)
    out = await service.update(
        "adv-1",
        AdvancedConfigTemplateUpdateBody(a4p_enabled=True),
    )
    assert gateway_calls == ["update"]
    assert len(handler.updated) == 1
    stored = handler.updated[0][2]
    assert stored["a4p_enabled"] is True
    assert "progressive_tool_enabled" not in stored
    assert out is not None
    assert out.a4p_enabled is True


@pytest.mark.asyncio
async def test_invalid_data_is_rejected_before_gateway(
    gateway_calls: list[str],
) -> None:
    handler = _MemoryHandler(_advanced_row())
    service = AdvancedConfigTemplateService(handler)
    with pytest.raises(ValueError, match="JSON object or null"):
        await service.update(
            "adv-1",
            AdvancedConfigTemplateUpdateBody.model_construct(data="bad"),  # type: ignore[arg-type]
        )
    assert gateway_calls == []
    assert handler.updated == []


@pytest.mark.asyncio
async def test_gateway_failure_leaves_manager_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail(*_args, **_kwargs) -> None:
        raise RuntimeError("gateway down")

    async def _counts(*_args, **_kwargs) -> dict[str, int]:
        return {}

    monkeypatch.setattr(adv_mod, "update_template_on_referencing_gateways", _fail)
    monkeypatch.setattr(adv_mod, "count_agent_template_reference_counts", _counts)
    handler = _MemoryHandler(_advanced_row())
    service = AdvancedConfigTemplateService(handler)
    with pytest.raises(RuntimeError, match="gateway down"):
        await service.update(
            "adv-1",
            AdvancedConfigTemplateUpdateBody(template_name="改名"),
        )
    assert handler.updated == []
    assert handler.row is not None
    assert handler.row.template_name == "高级配置"


@pytest.mark.asyncio
async def test_disable_rejected_when_referenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _refuse(*_args, **_kwargs) -> None:
        raise TemplateInUseError(
            "agent_templates_referenced",
            1,
            "cannot disable template: 1 template reference(s) exist, "
            "remove references from agent templates first",
        )

    calls: list[str] = []

    async def _update(*_args, **_kwargs) -> None:
        calls.append("update")

    async def _counts(*_args, **_kwargs) -> dict[str, int]:
        return {}

    monkeypatch.setattr(adv_mod, "assert_template_not_referenced", _refuse)
    monkeypatch.setattr(adv_mod, "update_template_on_referencing_gateways", _update)
    monkeypatch.setattr(adv_mod, "count_agent_template_reference_counts", _counts)

    handler = _MemoryHandler(_advanced_row())
    service = AdvancedConfigTemplateService(handler)
    with pytest.raises(TemplateInUseError, match="cannot disable template"):
        await service.update(
            "adv-1",
            AdvancedConfigTemplateUpdateBody(enabled=False),
        )
    assert calls == []
    assert handler.updated == []
    assert handler.row is not None
    assert handler.row.enabled is True


@pytest.mark.asyncio
async def test_delete_rejected_when_referenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _refuse(*_args, **_kwargs) -> None:
        raise TemplateInUseError(
            "agent_templates_referenced",
            2,
            "cannot delete template: 2 template reference(s) exist, "
            "remove references from agent templates first",
        )

    calls: list[str] = []

    async def _delete(*_args, **_kwargs) -> None:
        calls.append("delete")

    monkeypatch.setattr(adv_mod, "assert_template_deletable", _refuse)
    monkeypatch.setattr(adv_mod, "delete_template_on_referencing_gateways", _delete)

    handler = _MemoryHandler(_advanced_row())
    service = AdvancedConfigTemplateService(handler)
    with pytest.raises(TemplateInUseError, match="cannot delete template"):
        await service.delete("adv-1")
    assert calls == []
    assert handler.deleted == []
    assert handler.row is not None


@pytest.mark.asyncio
async def test_delete_gateway_order(gateway_calls: list[str]) -> None:
    handler = _MemoryHandler(_advanced_row())
    service = AdvancedConfigTemplateService(handler)
    assert await service.delete("adv-1") is True
    assert gateway_calls == ["assert_deletable", "delete"]
    assert handler.deleted == [("advanced_config_template", {"template_id": "adv-1"})]


@pytest.mark.asyncio
async def test_disable_checks_reference_before_gateway(
    gateway_calls: list[str],
) -> None:
    handler = _MemoryHandler(_advanced_row())
    service = AdvancedConfigTemplateService(handler)
    out = await service.update(
        "adv-1",
        AdvancedConfigTemplateUpdateBody(enabled=False),
    )
    assert gateway_calls == ["assert_not_referenced", "update"]
    assert out is not None
    assert out.enabled is False
