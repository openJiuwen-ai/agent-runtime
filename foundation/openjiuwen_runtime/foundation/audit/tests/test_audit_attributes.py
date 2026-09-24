# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""build_audit_attributes 字段合并与占位规则。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.audit import (
    AuditLogConfig,
    AuditSnapshot,
    EVENT_TYPE_ATTR,
    bind_audit_context,
    build_audit_attributes,
    clear_audit_context,
    reset_audit_context,
)
from openjiuwen_runtime.foundation.audit.clock import LocalClock


class _FixedClock(LocalClock):
    def __init__(self, ts: float) -> None:
        self._ts = ts

    def now(self) -> float:
        return self._ts


def _snapshot(**overrides) -> AuditSnapshot:
    data = {
        "data_center": "N",
        "system_code": "99900180001",
        "node": "10.0.0.1:8080",
        "service": "gateway",
    }
    data.update(overrides)
    return AuditSnapshot.from_config(AuditLogConfig.from_dict(data))


def setup_function() -> None:
    clear_audit_context()


def teardown_function() -> None:
    clear_audit_context()


def test_contextvar_mapping_and_explicit_override():
    tokens = bind_audit_context(
        session_id="sess_1",
        request_id="req_1",
        user_id="alice",
        src_ip="10.0.0.1",
        dst_ip="10.0.0.2",
    )
    try:
        attrs = build_audit_attributes(
            _snapshot(),
            event_type="UA",
            level="INFO",
            fields={
                "UID": "bob",
                "SRCIP": "-",
                "DSTIP": "-",
                "UA": "ok",
                "RSPCD": "0000",
                "SUBMDL": "gateway",
                "PROC": "authenticate",
            },
            clock=_FixedClock(1_713_610_200.123),
            caller="test.fn:1",
        )
    finally:
        reset_audit_context(tokens)

    assert attrs["trace_id"] == "sess_1"
    assert attrs["txn_seq"] == "req_1"
    assert attrs["UID"] == "bob"  # explicit wins
    assert attrs["SRCIP"] == "-"
    assert attrs["DSTIP"] == "-"
    assert attrs["CUSTID"] == "-"
    assert attrs[EVENT_TYPE_ATTR] == "UA"
    assert attrs["timestamp"].endswith(".123")
    assert attrs["caller"] == "test.fn:1"


def test_uid_system_only_when_explicit():
    attrs = build_audit_attributes(
        _snapshot(),
        event_type="EVT",
        level="WARN",
        fields={
            "UID": "system",
            "EVT": "ntp_placeholder",
            "RSPCD": "0000",
            "SUBMDL": "audit",
            "PROC": "self_check",
        },
        clock=_FixedClock(1.0),
        caller="t:1",
    )
    assert attrs["UID"] == "system"

    attrs2 = build_audit_attributes(
        _snapshot(),
        event_type="EVT",
        level="WARN",
        fields={
            "EVT": "x",
            "RSPCD": "0000",
            "SUBMDL": "audit",
            "PROC": "self_check",
        },
        clock=_FixedClock(1.0),
        caller="t:1",
    )
    assert attrs2["UID"] == "-"


def test_single_component_ips_default_placeholder():
    attrs = build_audit_attributes(
        _snapshot(),
        event_type="UA",
        level="INFO",
        fields={
            "UA": "login",
            "RSPCD": "0000",
            "SUBMDL": "gateway",
            "PROC": "authenticate",
        },
        clock=_FixedClock(1.0),
        caller="t:1",
    )
    assert attrs["SRCIP"] == "-"
    assert attrs["DSTIP"] == "-"


def test_attribute_truncation():
    snap = _snapshot()
    # rebuild with small max length
    cfg = AuditLogConfig.from_dict(
        {
            "data_center": "N",
            "system_code": "x",
            "node": "n",
            "attribute_value_max_length": 5,
        }
    )
    snap = AuditSnapshot.from_config(cfg)
    attrs = build_audit_attributes(
        snap,
        event_type="UA",
        level="INFO",
        fields={
            "UA": "1234567890",
            "RSPCD": "0000",
            "SUBMDL": "gateway",
            "PROC": "authenticate",
        },
        clock=_FixedClock(1.0),
        caller="t:1",
    )
    assert attrs["UA"] == "12345"


def test_explicit_session_request_maps_to_trace_txn():
    """设计 §6.4：无 ContextVar 时，显式 session_id/request_id → trace_id/txn_seq。"""
    attrs = build_audit_attributes(
        _snapshot(),
        event_type="UA",
        level="INFO",
        fields={
            "UA": "sandbox.create_sandbox 成功",
            "RSPCD": "0000",
            "SUBMDL": "sandbox",
            "PROC": "create_sandbox",
            "session_id": "web_sess_abc",
            "request_id": "req_001",
            "UID": "user1",
        },
        clock=_FixedClock(1.0),
        caller="t:1",
    )
    assert attrs["trace_id"] == "web_sess_abc"
    assert attrs["txn_seq"] == "req_001"
    assert "session_id" not in attrs
    assert "request_id" not in attrs
    assert "user_id" not in attrs


def test_extra_cost_overwrites_placeholder():
    """log_event 路径把 COST 放在 extra 时，应覆盖 format 占位 '-'。"""
    attrs = build_audit_attributes(
        _snapshot(),
        event_type="UA",
        level="INFO",
        fields={
            "UA": "api_client.http_agent_send 成功",
            "RSPCD": "0000",
            "SUBMDL": "api_client",
            "PROC": "http_agent_send",
            "extra": {"COST": 42, "sandbox_type": "jiuwenbox"},
        },
        clock=_FixedClock(1.0),
        caller="t:1",
    )
    assert attrs["COST"] == "42"
    assert attrs["sandbox_type"] == "jiuwenbox"


def test_extra_does_not_overwrite_non_placeholder():
    attrs = build_audit_attributes(
        _snapshot(),
        event_type="UA",
        level="INFO",
        fields={
            "UA": "ok",
            "RSPCD": "0000",
            "SUBMDL": "gateway",
            "PROC": "authenticate",
            "COST": 10,
            "extra": {"COST": 99},
        },
        clock=_FixedClock(1.0),
        caller="t:1",
    )
    assert attrs["COST"] == "10"


def test_extra_bridge_keys_map_without_leaking():
    """extra 中的 session_id/request_id 映射到 trace_id/txn_seq，且不原名写出。"""
    attrs = build_audit_attributes(
        _snapshot(),
        event_type="UA",
        level="INFO",
        fields={
            "UA": "gateway.http_resolve_identity 成功",
            "RSPCD": "0000",
            "SUBMDL": "gateway",
            "PROC": "http_resolve_identity",
            "UID": "user1",
            "extra": {
                "session_id": "webhttp_abc",
                "request_id": "req_001",
                "agent_pod": "pod-1",
            },
        },
        clock=_FixedClock(1.0),
        caller="t:1",
    )
    assert attrs["trace_id"] == "webhttp_abc"
    assert attrs["txn_seq"] == "req_001"
    assert "session_id" not in attrs
    assert "request_id" not in attrs
    assert attrs["agent_pod"] == "pod-1"

