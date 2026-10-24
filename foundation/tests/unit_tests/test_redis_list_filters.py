# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Redis list_records must filter before applying offset/limit."""

import pytest

from openjiuwen_runtime.foundation.db.redis_handler import RedisHandler


class _FakeRedis:
    def __init__(self, keys, records):
        self._keys = keys
        self._records = records

    async def lrange(self, key, start, end):
        keys = self._keys[key]
        if end == -1:
            end = len(keys) - 1
        return keys[start:end + 1]

    async def hgetall(self, key):
        return self._records[key]


@pytest.mark.asyncio
async def test_filtered_page_does_not_look_only_at_the_raw_page():
    handler = RedisHandler()
    handler._table_prefixes["deployment"] = "deployment:"
    handler.client = _FakeRedis(
        {"deployment:list": ["a", "b", "c"]},
        {
            "deployment:a": {"deployment_id": "a", "deployment_status": "failed"},
            "deployment:b": {"deployment_id": "b", "deployment_status": "failed"},
            "deployment:c": {"deployment_id": "c", "deployment_status": "running"},
        },
    )

    rows = await handler.list_records(
        "deployment", {"deployment_status": "running"}, limit=1, offset=0
    )

    assert [row["deployment_id"] for row in rows] == ["c"]
