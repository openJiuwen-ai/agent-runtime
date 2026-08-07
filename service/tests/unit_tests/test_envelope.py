# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Envelope / Metadata / ResponseEnvelope / StreamChunk 序列化往返与默认值单测。"""
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from openjiuwen_runtime.service.envelope import (
    Envelope,
    Metadata,
    ResponseEnvelope,
    StreamChunk,
)


@pytest.mark.unit
def test_envelope_defaults_and_roundtrip():
    env = Envelope(type="echo", metadata=Metadata(request_id="r1"),
                   rawdata={"message": "hi"})
    assert env.version == "1"                       # 默认 schema 版本
    assert env.metadata.user_id is None             # Metadata 可选字段默认 None
    assert env.metadata.extra == {}                 # extra 默认空 dict

    d = env.to_dict()
    assert d["type"] == "echo"
    assert d["version"] == "1"
    assert d["metadata"]["request_id"] == "r1"
    assert d["rawdata"] == {"message": "hi"}

    assert Envelope.from_dict(d) == env             # 往返无损


@pytest.mark.unit
def test_envelope_from_rest_body_shape():
    # REST/WS body 仅含 type/metadata/rawdata，无 version → 回落默认 "1"
    body = {"type": "echo", "metadata": {"request_id": "r1"}, "rawdata": {"message": "hi"}}
    env = Envelope.from_dict(body)
    assert env.type == "echo"
    assert env.metadata.request_id == "r1"
    assert env.rawdata == {"message": "hi"}
    assert env.version == "1"


@pytest.mark.unit
def test_metadata_extra_preserved():
    md = Metadata(request_id="r1", user_id="u1", extra={"channel_id": "c9"})
    d = md.to_dict()
    assert d["extra"]["channel_id"] == "c9"
    assert Metadata.from_dict(d) == md


@pytest.mark.unit
def test_metadata_instance_id_preserved():
    md = Metadata(request_id="r1", instance_id="workflow-7")
    assert Metadata.from_dict(md.to_dict()).instance_id == "workflow-7"


class _TypedRequest(BaseModel):
    name: str
    created_at: datetime


@pytest.mark.unit
def test_typed_envelope_serializes_pydantic_request_as_json_data():
    request = _TypedRequest(
        name="demo",
        created_at=datetime(2026, 8, 6, 12, 30, tzinfo=timezone.utc),
    )
    env = Envelope(type="typed", metadata=Metadata(request_id="r1"), rawdata=request)

    data = env.to_dict()

    assert env.rawdata is request
    assert data["rawdata"] == {
        "name": "demo",
        "created_at": "2026-08-06T12:30:00Z",
    }


@pytest.mark.unit
def test_response_envelope_to_dict_contract():
    # 适配器据此序列化为 JSON 响应体
    resp = ResponseEnvelope(type="echo", metadata=Metadata(request_id="r1"),
                            rawdata={"echo": "hi", "idx": 1}, ok=True)
    d = resp.to_dict()
    assert d["ok"] is True
    assert d["type"] == "echo"
    assert d["rawdata"] == {"echo": "hi", "idx": 1}
    assert d["metadata"]["request_id"] == "r1"      # 回填 request_id
    assert d["error_code"] is None and d["error_message"] is None
    assert ResponseEnvelope.from_dict(d) == resp


@pytest.mark.unit
def test_response_envelope_error_shape():
    resp = ResponseEnvelope(type="echo", metadata=Metadata(request_id="r1"),
                            rawdata={}, ok=False, error_code="internal",
                            error_message="boom")
    d = resp.to_dict()
    assert d["ok"] is False
    assert d["error_code"] == "internal"
    assert d["error_message"] == "boom"


@pytest.mark.unit
def test_stream_chunk_to_dict_and_roundtrip():
    chunk = StreamChunk(sequence=1, is_final=False, metadata=Metadata(request_id="r1"),
                        rawdata={"delta": "x"})
    d = chunk.to_dict()
    assert d["sequence"] == 1 and d["is_final"] is False
    assert d["rawdata"] == {"delta": "x"}
    assert StreamChunk.from_dict(d) == chunk
