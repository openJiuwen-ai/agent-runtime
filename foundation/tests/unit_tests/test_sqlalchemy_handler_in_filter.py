# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import pytest

from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.db.table_def import ColumnDefinition, TableDefinition


@pytest.mark.asyncio
async def test_list_records_supports_in_filter(tmp_path) -> None:
    db_path = tmp_path / "in_filter.db"
    handler = SQLiteHandler(str(db_path))
    await handler.init_database()
    await handler.connect()

    table_def = TableDefinition(
        table_name="demo_template",
        columns=[
            ColumnDefinition("id", "integer", primary_key=True, autoincrement=True),
            ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
            ColumnDefinition("template_id", "string", length=100, nullable=False),
            ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ],
    )
    await handler.init_table(table_def)

    await handler.create(
        "demo_template",
        {"jiuwenclaw_id": "inst-1", "template_id": "t1", "enabled": True},
    )
    await handler.create(
        "demo_template",
        {"jiuwenclaw_id": "inst-1", "template_id": "t2", "enabled": True},
    )
    await handler.create(
        "demo_template",
        {"jiuwenclaw_id": "inst-1", "template_id": "t3", "enabled": False},
    )

    rows = await handler.list_records(
        "demo_template",
        {
            "jiuwenclaw_id": "inst-1",
            "enabled": True,
            "template_id": ["t1", "t2", "missing"],
        },
        limit=100,
    )
    template_ids = {row.template_id for row in rows}
    assert template_ids == {"t1", "t2"}

    assert await handler.count_records(
        "demo_template",
        {"jiuwenclaw_id": "inst-1", "template_id": ["t1", "t2"]},
    ) == 2

    await handler.disconnect()
