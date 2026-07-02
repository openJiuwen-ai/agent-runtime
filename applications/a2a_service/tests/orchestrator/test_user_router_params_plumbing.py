# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
URL query_params 首跳透传回归测试。

背景：yougq (!127) 在 user_router/executor/versatile_proxy 四个文件铺设了一条
"URL query string 从北向入口一路透传到 Versatile 后端 URL" 的链路。其中 user_router
首跳负责从 FastAPI Request 取 query_params、写入 Redis session 缓存、并传给
_build_request。这三处首跳在 !131 (ipanyin 日志规范化) 的 user_router 大段重写里
被误删，导致后续链路永远读到空 params。

本文件锁定：
  1. _extract_query_params 把 FastAPI Request 的 query_params 转 dict
  2. _extract_query_params 对缺失 query_params 属性的对象降级返回 {}
  3. _build_request 把 params 放进 A2A Message DataPart.struct 的 "params" 字段
"""
from __future__ import annotations

from google.protobuf.json_format import MessageToDict

from api.dispatch import _build_request, _extract_query_params


CONV_ID = "conv-params-1"


class _FakeQueryParams:
    """最小模拟 Starlette QueryParams 的迭代协议，dict() 可直接转换。"""

    def __init__(self, items):
        self._items = list(items)

    def __iter__(self):
        return iter(k for k, _ in self._items)

    def __getitem__(self, key):
        for k, v in self._items:
            if k == key:
                return v
        raise KeyError(key)

    def keys(self):
        return [k for k, _ in self._items]


class _FakeRequest:
    def __init__(self, query_params):
        self.query_params = query_params


# ════════════════════════════════════════════════════════════════════
# _extract_query_params
# ════════════════════════════════════════════════════════════════════


def test_extract_query_params_returns_dict_from_request():
    req = _FakeRequest(_FakeQueryParams([("foo", "bar"), ("x", "1")]))
    assert _extract_query_params(req) == {"foo": "bar", "x": "1"}


def test_extract_query_params_empty_when_attr_missing():
    """非 FastAPI Request 的对象（无 query_params 属性）降级返回空 dict。"""

    class NoQueryParams:
        pass

    assert _extract_query_params(NoQueryParams()) == {}


def test_extract_query_params_empty_query_string():
    req = _FakeRequest(_FakeQueryParams([]))
    assert _extract_query_params(req) == {}


# ════════════════════════════════════════════════════════════════════
# _build_request 契约锁定：params 要进 DataPart.struct["params"]
# ════════════════════════════════════════════════════════════════════


def test_build_request_packs_params_into_data_part():
    msg = _build_request(
        conversation_id=CONV_ID,
        user_query="hello",
        body={"agent_id": "a1"},
        params={"tenant": "t1", "debug": "1"},
    ).message

    data_part = next(p for p in msg.parts if p.WhichOneof("content") == "data")
    carried = MessageToDict(data_part.data)
    assert carried["body"] == {"agent_id": "a1"}
    assert carried["params"] == {"tenant": "t1", "debug": "1"}


def test_build_request_defaults_params_to_empty_dict_when_none():
    msg = _build_request(
        conversation_id=CONV_ID,
        user_query="hello",
        body={"agent_id": "a1"},
    ).message

    data_part = next(p for p in msg.parts if p.WhichOneof("content") == "data")
    carried = MessageToDict(data_part.data)
    assert carried["params"] == {}
