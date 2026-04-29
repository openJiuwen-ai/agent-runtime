# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""HTTP request headers 北向→A2A 透传回归测试。

背景：
  - issue 2026-04-28 触发的根因不是 issue 文档说的"a2a_service Redis 缓存只写入首轮"。
    实际隐藏 bug 在 user_router._build_request：把 HTTP request headers 写进了 Redis
    session 缓存，但**没有**写进 A2A SendMessageRequest 的 DataPart struct，导致
    executor.execute() 解出的 original_headers 永远是 {}，续轮调用 versatile_adapter
    时 cust-token / x-user-id 等头一并丢失。
  - 修复：_build_request 增加 headers 参数，写进 DataPart struct["headers"]，与现有
    body / params 字段平级，让 executor.execute() 那一行 data.get("headers", {}) 能
    取到真实值。

本文件锁定：
  1. _build_request 把 headers 放进 A2A Message DataPart.struct 的 "headers" 字段
  2. headers 缺省为 {}（避免 None 进 Struct 时类型错误）
"""
from __future__ import annotations

from google.protobuf.json_format import MessageToDict

from orchestrator.user_router import _build_request


CONV_ID = "conv-headers-1"


def test_build_request_packs_headers_into_data_part():
    msg = _build_request(
        conversation_id=CONV_ID,
        user_query="hello",
        body={"agent_id": "a1"},
        params={"tenant": "t1"},
        headers={"Cookie": "AGENT_SID=u|0", "x-user-id": "u-1"},
    ).message

    data_part = next(p for p in msg.parts if p.WhichOneof("content") == "data")
    carried = MessageToDict(data_part.data)
    assert carried["headers"] == {"Cookie": "AGENT_SID=u|0", "x-user-id": "u-1"}
    # 不能影响已有的 body/params 透传契约
    assert carried["body"] == {"agent_id": "a1"}
    assert carried["params"] == {"tenant": "t1"}


def test_build_request_defaults_headers_to_empty_dict_when_omitted():
    """不传 headers 时，DataPart.struct["headers"] 应为 {}，向下游传递空契约。"""
    msg = _build_request(
        conversation_id=CONV_ID,
        user_query="hello",
        body={"agent_id": "a1"},
    ).message

    data_part = next(p for p in msg.parts if p.WhichOneof("content") == "data")
    carried = MessageToDict(data_part.data)
    assert carried["headers"] == {}
