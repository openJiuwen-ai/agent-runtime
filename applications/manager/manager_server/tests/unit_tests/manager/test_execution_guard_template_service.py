# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""执行护栏模板：新建不下发；Gateway 失败不改 Manager 行；非法 body 在下发前拒绝。"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from manager_server.core.template import execution_guard_template as guard_mod
from manager_server.core.template import model_template as model_mod
from manager_server.core.template.execution_guard_template import ExecutionGuardTemplateService
from manager_server.core.template.model_template import (
    MODEL_TEMPLATE_STORED_RETRY_COUNT,
    ModelTemplateService,
)
from manager_server.core.template.push_template_to_gateway import (
    AGENT_TEMPLATES_KIND,
    TEMPLATE_KIND_ORDER,
)
from manager_server.schemas.execution_guard_body import DEFAULT_EXECUTION_GUARD_BODY
from manager_server.schemas.template_schemas import (
    ExecutionGuardTemplateCreateBody,
    ExecutionGuardTemplateUpdateBody,
    ModelTemplateCreateBody,
    ModelTemplateUpdateBody,
)


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
        return True


def _guard_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        template_id="guard-1",
        template_name="护栏",
        description=None,
        enabled=True,
        body=copy.deepcopy(DEFAULT_EXECUTION_GUARD_BODY),
        data=None,
        created_at=None,
        updated_at=None,
    )


def _model_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        template_id="model-1",
        template_name="模型",
        description=None,
        model_type=[],
        model_tags=None,
        api_base="https://models.example.com/v1",
        api_key="secret",
        model_id="test-model",
        model_provider="OpenAI",
        parameters=None,
        timeout=360,
        retry_count=7,
        verify_ssl=False,
        enabled=True,
        data=None,
        created_at=None,
        updated_at=None,
    )


@pytest.fixture
def gateway_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _update(*_args, **_kwargs) -> None:
        calls.append("update")

    async def _delete(*_args, **_kwargs) -> None:
        calls.append("delete")

    async def _counts(*_args, **_kwargs) -> dict[str, int]:
        return {}

    monkeypatch.setattr(guard_mod, "update_template_on_referencing_gateways", _update)
    monkeypatch.setattr(guard_mod, "delete_template_on_referencing_gateways", _delete)
    monkeypatch.setattr(guard_mod, "count_agent_template_reference_counts", _counts)
    return calls


def test_kind_is_registered_before_agent_templates() -> None:
    assert "execution_guard_templates" in TEMPLATE_KIND_ORDER
    assert TEMPLATE_KIND_ORDER.index("execution_guard_templates") < TEMPLATE_KIND_ORDER.index(
        AGENT_TEMPLATES_KIND
    )


@pytest.mark.asyncio
async def test_create_does_not_call_gateway(gateway_calls: list[str]) -> None:
    handler = _MemoryHandler()
    service = ExecutionGuardTemplateService(handler)
    out = await service.create(
        ExecutionGuardTemplateCreateBody(
            template_name="默认护栏",
            body=copy.deepcopy(DEFAULT_EXECUTION_GUARD_BODY),
        )
    )
    assert gateway_calls == []
    assert len(handler.created) == 1
    assert out.body["model_anomaly_detection_rail"]["transient_max_retries"] == 3
    assert handler.created[0][1]["body"] == DEFAULT_EXECUTION_GUARD_BODY


@pytest.mark.asyncio
async def test_invalid_body_is_rejected_before_gateway(gateway_calls: list[str]) -> None:
    handler = _MemoryHandler(_guard_row())
    service = ExecutionGuardTemplateService(handler)
    body = copy.deepcopy(DEFAULT_EXECUTION_GUARD_BODY)
    body["model_anomaly_detection_rail"]["backoff_seconds"] = [1, 2]
    with pytest.raises(ValueError, match="unknown fields: backoff_seconds"):
        await service.update(
            "guard-1",
            ExecutionGuardTemplateUpdateBody(body=body),
        )
    assert gateway_calls == []
    assert handler.updated == []


@pytest.mark.asyncio
async def test_gateway_failure_leaves_manager_row(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(*_args, **_kwargs) -> None:
        raise RuntimeError("gateway down")

    async def _counts(*_args, **_kwargs) -> dict[str, int]:
        return {}

    monkeypatch.setattr(guard_mod, "update_template_on_referencing_gateways", _fail)
    monkeypatch.setattr(guard_mod, "count_agent_template_reference_counts", _counts)
    handler = _MemoryHandler(_guard_row())
    service = ExecutionGuardTemplateService(handler)
    with pytest.raises(RuntimeError, match="gateway down"):
        await service.update(
            "guard-1",
            ExecutionGuardTemplateUpdateBody(template_name="改名"),
        )
    assert handler.updated == []
    assert handler.row is not None
    assert handler.row.template_name == "护栏"


@pytest.mark.asyncio
async def test_model_create_stores_fixed_retry_count() -> None:
    parsed = ModelTemplateCreateBody.model_validate(
        {
            "template_name": "模型",
            "api_base": "https://models.example.com/v1",
            "api_key": "secret",
            "model_id": "test-model",
            "model_provider": "OpenAI",
            "retry_count": 0,
        }
    )
    assert "retry_count" not in parsed.model_fields_set
    out = await ModelTemplateService(_MemoryHandler()).create(parsed)
    assert out.retry_count == MODEL_TEMPLATE_STORED_RETRY_COUNT


@pytest.mark.asyncio
async def test_model_update_ignores_retry_count(monkeypatch: pytest.MonkeyPatch) -> None:
    pushed: list[dict] = []

    async def _update(_handler, _kind, _template_id, updates, **_kwargs) -> None:
        pushed.append(dict(updates))

    async def _counts(*_args, **_kwargs) -> dict[str, int]:
        return {}

    monkeypatch.setattr(model_mod, "update_template_on_referencing_gateways", _update)
    monkeypatch.setattr(model_mod, "count_agent_template_reference_counts", _counts)
    handler = _MemoryHandler(_model_row())
    service = ModelTemplateService(handler)
    ignored = ModelTemplateUpdateBody.model_validate({"retry_count": 9})
    out = await service.update("model-1", ignored)
    assert pushed == []
    assert handler.updated == []
    assert out is not None
    assert out.retry_count == 7

    changed = ModelTemplateUpdateBody.model_validate({"timeout": 120, "retry_count": 9})
    out = await service.update("model-1", changed)
    assert pushed == [{"timeout": 120}]
    assert "retry_count" not in handler.updated[0][2]
    assert out is not None
    assert out.retry_count == 7
    assert out.timeout == 120
