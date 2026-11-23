# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""压缩器模型解析：model_template 行 → 压缩器内联 model/model_client 配置。"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from manager_server.core.template.compressor_model_resolver import (
    build_compressor_model,
    extract_react_model_template_ids,
    find_react_templates_referencing_model,
    resolve_react_body_models,
)

pytestmark = pytest.mark.unit


class _FakeHandler:
    def __init__(self, model_rows: dict[str, object] | None = None) -> None:
        self._model_rows = model_rows or {}

    async def get(self, table: str, pk: dict[str, str]) -> object | None:
        return self._model_rows.get(pk["template_id"])

    async def list_records(
        self, table: str, filters: dict, limit: int, offset: int, order_by=None
    ) -> list:
        return []


def _model_row() -> SimpleNamespace:
    return SimpleNamespace(
        model_id="gpt-4o",
        model_provider="openai",
        api_key="sk-test",
        api_base="https://api.openai.com/v1",
        parameters={"temperature": 0.2, "max_tokens": 1024},
        timeout=60,
        retry_count=3,
        verify_ssl=False,
        data={"headers": {"X-Custom": "1"}},
    )


def test_build_compressor_model() -> None:
    model, client = build_compressor_model(_model_row())
    assert model == {"model": "gpt-4o", "temperature": 0.2, "max_tokens": 1024}
    assert client["client_provider"] == "openai"
    assert client["api_key"] == "sk-test"
    assert client["api_base"] == "https://api.openai.com/v1"
    assert client["timeout"] == 60
    assert client["verify_ssl"] is False
    assert client["max_retries"] == 3
    assert client["custom_headers"] == {"X-Custom": "1"}


def test_build_compressor_model_defaults() -> None:
    # verify_ssl 列为 nullable=False、default=False，未显式配置即为 False；
    # 解析器忠实透传该列值，这里用真实默认值而非 None 来固化行为。
    row = SimpleNamespace(
        model_id="m",
        model_provider="p",
        api_key=None,
        api_base=None,
        parameters=None,
        timeout=None,
        retry_count=None,
        verify_ssl=False,
        data=None,
    )
    model, client = build_compressor_model(row)
    assert model == {"model": "m"}
    assert client["api_key"] == ""
    assert client["api_base"] == ""
    assert math.isclose(client["timeout"], 360.0)
    assert client["verify_ssl"] is False
    assert "max_retries" not in client
    assert "custom_headers" not in client


def test_extract_react_model_template_ids() -> None:
    body = {
        "context_engine_config": {
            "dialogue_compressor_config": {"model_template_id": "mt-1"},
            "current_round_compressor_config": {"model_template_id": "mt-2", "keep_recent_messages": 4},
            "round_level_compressor_config": {},
        }
    }
    assert extract_react_model_template_ids(body) == {"mt-1", "mt-2"}


@pytest.mark.asyncio
async def test_resolve_react_body_models_injects_and_strips() -> None:
    handler = _FakeHandler({"mt-1": _model_row()})
    body = {
        "context_engine_config": {
            "dialogue_compressor_config": {"enabled": True, "model_template_id": "mt-1"},
        }
    }
    resolved = await resolve_react_body_models(handler, body)
    cfg = resolved["context_engine_config"]["dialogue_compressor_config"]
    assert "model_template_id" not in cfg
    assert cfg["model"]["model"] == "gpt-4o"
    assert cfg["model_client"]["client_provider"] == "openai"
    # 原 body 未被原地修改
    assert body["context_engine_config"]["dialogue_compressor_config"]["model_template_id"] == "mt-1"


@pytest.mark.asyncio
async def test_resolve_react_body_models_missing_raises() -> None:
    handler = _FakeHandler({})
    body = {"context_engine_config": {"dialogue_compressor_config": {"model_template_id": "gone"}}}
    with pytest.raises(ValueError):
        await resolve_react_body_models(handler, body)


@pytest.mark.asyncio
async def test_resolve_react_body_models_skips_when_unset() -> None:
    handler = _FakeHandler({})
    body = {"context_engine_config": {"dialogue_compressor_config": {"enabled": True}}}
    resolved = await resolve_react_body_models(handler, body)
    cfg = resolved["context_engine_config"]["dialogue_compressor_config"]
    assert "model" not in cfg and "model_client" not in cfg


@pytest.mark.asyncio
async def test_find_react_templates_referencing_model() -> None:
    class Handler(_FakeHandler):
        async def list_records(self, table, filters, limit, offset, order_by=None):
            return [
                SimpleNamespace(
                    template_id="rt-1",
                    data={
                        "context_engine_config": {
                            "dialogue_compressor_config": {"model_template_id": "mt-1"}
                        }
                    },
                ),
                SimpleNamespace(template_id="rt-2", data={"context_engine_config": {}}),
            ]

    assert await find_react_templates_referencing_model(Handler(), "mt-1") == ["rt-1"]
