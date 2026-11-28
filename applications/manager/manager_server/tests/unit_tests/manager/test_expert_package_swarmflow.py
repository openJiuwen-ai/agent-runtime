# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Expert package template Swarmflow 企业策略列（JSON）的模型、出参与持久化。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.db.table_def import TableDefinition
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    inspect,
)

from manager_server.core.template.expert_package_template import (
    ExpertPackageTemplateService,
    row_to_out,
    swarmflow_from_row,
)
from manager_server.infrastructure.utils import utc_now
from manager_server.models.table_init import (
    _ensure_expert_package_template_swarmflow_column,
    init_all_tables,
)
from manager_server.models.template_models import EXPERT_PACKAGE_TEMPLATE_TABLE_DEF
from manager_server.schemas.template_schemas import (
    ExpertPackageTemplateCreateBody,
    ExpertPackageTemplateUpdateBody,
)

_SWARMFLOW = {
    "allowed": False,
    "default_enabled": True,
    "max_workflows": 2,
    "agents_per_run": 3,
    "max_agents_total": 4,
    "swarmflow_budget": 1000,
}
_PACKAGES = [{"kind": "agent_group", "asset_id": "presentation-generation", "version": ""}]

_TYPES = {
    "integer": Integer,
    "string": String,
    "datetime": DateTime,
    "json": JSON,
    "boolean": Boolean,
}


def _as_sqlalchemy_table(definition) -> Table:
    metadata = MetaData()
    columns = []
    for item in definition.columns:
        type_class = _TYPES[item.data_type]
        column_type = (
            type_class(item.length) if item.data_type == "string" and item.length else type_class
        )
        columns.append(
            Column(
                item.name,
                column_type,
                primary_key=item.primary_key,
                nullable=item.nullable,
                autoincrement=item.autoincrement,
            )
        )
    table = Table(definition.table_name, metadata, *columns)
    for item in definition.indexes:
        Index(
            item.name or f"ix_{definition.table_name}_{'_'.join(item.columns)}",
            *(table.c[name] for name in item.columns),
            unique=item.unique,
        )
    return table


def _row(swarmflow: object | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        template_id="tpl-1",
        template_name="ep",
        description=None,
        packages=[{"kind": "agent_group", "asset_id": "a", "version": ""}],
        swarmflow=swarmflow,
        enabled=True,
        revision=0,
        created_at=None,
        updated_at=None,
    )


def test_swarmflow_from_row_variants() -> None:
    assert swarmflow_from_row(_row(_SWARMFLOW)).model_dump() == _SWARMFLOW
    assert swarmflow_from_row(_row(json.dumps(_SWARMFLOW))).model_dump() == _SWARMFLOW
    assert swarmflow_from_row(_row(None)) is None
    assert swarmflow_from_row(_row("not-json")) is None
    assert swarmflow_from_row(_row([1, 2])) is None
    # 键白名单外 / 违反取值约束的脏数据按未配置处理
    assert swarmflow_from_row(_row({"foo": 1})) is None
    assert swarmflow_from_row(_row({"max_workflows": 0})) is None


def test_row_to_out_includes_swarmflow() -> None:
    out = row_to_out(_row(_SWARMFLOW))
    assert out.swarmflow.model_dump() == _SWARMFLOW
    assert row_to_out(_row(None)).swarmflow is None


def test_expert_package_template_def_has_swarmflow_column() -> None:
    columns = {column.name: column for column in EXPERT_PACKAGE_TEMPLATE_TABLE_DEF.columns}
    column = columns["swarmflow"]
    assert column.data_type == "json"
    assert column.nullable is True


def test_create_and_update_body_accept_swarmflow() -> None:
    create = ExpertPackageTemplateCreateBody.model_validate(
        {"template_name": "ep", "packages": _PACKAGES, "swarmflow": _SWARMFLOW}
    )
    assert create.swarmflow.model_dump() == _SWARMFLOW
    update = ExpertPackageTemplateUpdateBody.model_validate({"swarmflow": _SWARMFLOW})
    assert update.model_dump(exclude_unset=True)["swarmflow"] == _SWARMFLOW


def test_swarmflow_body_rejects_unknown_keys_and_bad_values() -> None:
    def _create(swarmflow: object) -> None:
        ExpertPackageTemplateCreateBody.model_validate(
            {"template_name": "ep", "packages": _PACKAGES, "swarmflow": swarmflow}
        )

    with pytest.raises(ValidationError):
        _create({"foo": 1})
    with pytest.raises(ValidationError):
        _create({"max_workflows": 0})
    with pytest.raises(ValidationError):
        _create({"max_agents_total": -1})
    with pytest.raises(ValidationError):
        _create({"swarmflow_budget": 10**9})
    # 字符串布尔值在写入侧归一化为真实 bool，杜绝「显示与生效相反」
    coerced = ExpertPackageTemplateCreateBody.model_validate(
        {"template_name": "ep", "packages": _PACKAGES, "swarmflow": {"allowed": "false"}}
    )
    assert coerced.swarmflow.allowed is False


@pytest.mark.asyncio
async def test_service_persists_swarmflow(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "manager_server.core.template.expert_package_template."
        "update_template_on_referencing_gateways",
        AsyncMock(),
    )
    handler = SQLiteHandler(str(tmp_path / "manager.db"))
    await handler.connect()
    await init_all_tables(handler)
    service = ExpertPackageTemplateService(handler)

    created = await service.create(
        ExpertPackageTemplateCreateBody.model_validate(
            {"template_name": "ep", "packages": _PACKAGES, "swarmflow": _SWARMFLOW}
        )
    )
    assert created.swarmflow.model_dump() == _SWARMFLOW
    fetched = await service.get(created.template_id)
    assert fetched.swarmflow.model_dump() == _SWARMFLOW

    await service.update(
        created.template_id,
        ExpertPackageTemplateUpdateBody.model_validate(
            {
                "swarmflow": {**_SWARMFLOW, "swarmflow_budget": 2000},
                "expected_revision": created.revision,
            }
        ),
    )
    updated = await service.get(created.template_id)
    assert updated.swarmflow.swarmflow_budget == 2000


@pytest.mark.asyncio
async def test_swarmflow_column_migrates_legacy_sqlite(tmp_path) -> None:
    definition = EXPERT_PACKAGE_TEMPLATE_TABLE_DEF
    handler = SQLiteHandler(str(tmp_path / "legacy.db"))
    await handler.connect()
    legacy_columns = [column for column in definition.columns if column.name != "swarmflow"]
    legacy_table = _as_sqlalchemy_table(
        TableDefinition(
            table_name=definition.table_name,
            columns=legacy_columns,
            indexes=definition.indexes,
        )
    )
    async with handler.engine.begin() as connection:
        await connection.run_sync(legacy_table.metadata.create_all)
    async with handler.engine.begin() as connection:
        await connection.execute(
            legacy_table.insert().values(
                template_id="tpl-legacy",
                template_name="ep",
                packages=[],
                enabled=True,
                revision=0,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )

    await _ensure_expert_package_template_swarmflow_column(handler)
    # 重复执行幂等
    await _ensure_expert_package_template_swarmflow_column(handler)

    async with handler.engine.begin() as connection:
        columns = await connection.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns(definition.table_name)
            }
        )
    assert "swarmflow" in columns
