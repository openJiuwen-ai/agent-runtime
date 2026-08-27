# coding: utf-8
"""util 纯函数测试:fingerprint 确定性(缺陷④回归网)。

2026-08-26 真环境实测教训:agent_env 这类嵌套 dict 字段经 MySQL JSON 列回读会
重排键序,repr(dict) 序列化导致同一模板算出不同 deploy_ver → 暖 Pod 复用永不
命中。fingerprint 必须对嵌套结构键序无关。
"""

from __future__ import annotations

from agent_runtime.util import fingerprint


def test_fingerprint_nested_dict_key_order_insensitive():
    """嵌套 dict 键序打乱(模拟 MySQL JSON 列回读重排)→ 指纹必须不变。"""
    payload_order = {
        "agent_env": {"AGENT_HTTP_ENABLED": "true", "AGENT_HTTP_HOST": "0.0.0.0",
                      "AGENT_HTTP_PORT": "8080"},
        "agent_image": "img:1", "sse_port": 8080,
    }
    db_roundtrip_order = {  # MySQL JSON 归一化后的典型键序
        "sse_port": 8080, "agent_image": "img:1",
        "agent_env": {"AGENT_HTTP_HOST": "0.0.0.0", "AGENT_HTTP_PORT": "8080",
                      "AGENT_HTTP_ENABLED": "true"},
    }
    assert fingerprint(payload_order) == fingerprint(db_roundtrip_order)


def test_fingerprint_value_change_detected():
    """值变(标量 / 嵌套 dict 内)→ 指纹必须变(A 类日落判定依赖)。"""
    base = {"agent_image": "img:1", "agent_env": {"A": "1", "B": "2"}}
    assert fingerprint(base) != fingerprint({**base, "agent_image": "img:2"})
    assert fingerprint(base) != fingerprint(
        {"agent_image": "img:1", "agent_env": {"A": "1", "B": "3"}})
    assert fingerprint(base) != fingerprint(
        {"agent_image": "img:1", "agent_env": {"A": "1"}})


def test_fingerprint_deterministic_and_none_filtered():
    """同输入恒等(跨进程/跨副本);None 字段不参与(A 类 kubeconfig 例外依赖)。"""
    a = {"x": "1", "n": None}
    assert fingerprint(a) == fingerprint({"x": "1"})
    assert fingerprint(a) == fingerprint({"n": None, "x": "1"})
