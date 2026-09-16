# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""边界 / 异常场景：规范化、校验失败隔离、emit 容错、字段契约。"""

from __future__ import annotations

import logging

import pytest

from openjiuwen_runtime.foundation.audit import (
    AuditLogConfig,
    AuditManager,
    AuditSnapshot,
    FormatError,
    FormatSpec,
    MemoryEmitter,
    SchemaValidationError,
    bind_audit_context,
    build_audit_attributes,
    clear_audit_context,
    default_format,
    resolve_service_name,
    reset_audit_context,
    reset_audit_manager,
    validate_format,
)
from openjiuwen_runtime.foundation.audit.clock import LocalClock


class _FixedClock(LocalClock):
    def __init__(self, ts: float) -> None:
        self._ts = ts

    def now(self) -> float:
        return self._ts


class _BoomEmitter:
    def emit(self, *, severity: str, body: str, attributes) -> None:
        raise RuntimeError("boom")

    def shutdown(self) -> None:
        return None


@pytest.fixture()
def mem_manager():
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    reset_audit_manager(mgr)
    yield mem, mgr
    reset_audit_manager(AuditManager(emitter=MemoryEmitter()))


def setup_function() -> None:
    clear_audit_context()


def teardown_function() -> None:
    clear_audit_context()


def test_warning_level_normalized_to_warn(mem_manager):
    mem, mgr = mem_manager
    mgr.log_audit(
        "UA",
        level="WARNING",
        UA="x",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    assert mem.records[0]["severity"] == "WARN"
    assert mem.records[0]["attributes"]["level"] == "WARN"


def test_event_type_case_insensitive(mem_manager):
    mem, mgr = mem_manager
    mgr.log_audit(
        "ua",
        level="info",
        UA="login",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    assert mem.records[0]["attributes"]["event_type"] == "UA"
    assert mem.records[0]["severity"] == "INFO"


def test_invalid_level_raises(mem_manager):
    _, mgr = mem_manager
    with pytest.raises(FormatError, match="level"):
        mgr.log_audit("UA", level="TRACE")


def test_empty_event_type_raises(mem_manager):
    _, mgr = mem_manager
    with pytest.raises(FormatError, match="event_type"):
        mgr.log_audit("", level="INFO")
    with pytest.raises(FormatError, match="event_type"):
        mgr.log_audit("  ", level="INFO")


def test_evt_body_ignores_ua_placeholder(mem_manager):
    mem, mgr = mem_manager
    mgr.log_audit(
        "EVT",
        level="ERROR",
        EVT="鉴权通道不可用",
        RSPCD="E005",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    assert mem.records[0]["body"] == "鉴权通道不可用"


def test_ua_body_prefers_ua_over_msg(mem_manager):
    mem, mgr = mem_manager
    mgr.log_audit(
        "UA",
        level="INFO",
        UA="用户登录成功",
        MSG="should-not-win",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    assert mem.records[0]["body"] == "用户登录成功"


def test_body_falls_back_to_event_type_when_all_placeholder(mem_manager):
    mem, mgr = mem_manager
    mgr.log_audit(
        "UA",
        level="INFO",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    assert mem.records[0]["body"] == "UA"


def test_emit_exception_swallowed_with_warning(mem_manager, caplog):
    _, mgr = mem_manager
    mgr.set_emitter(_BoomEmitter())
    with caplog.at_level(logging.WARNING):
        mgr.log_audit(
            "UA",
            level="INFO",
            UA="x",
            RSPCD="0000",
            SUBMDL="gateway",
            PROC="authenticate",
        )
    assert any("audit emit failed" in r.message for r in caplog.records)


def test_apply_config_reject_keeps_previous_snapshot():
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    mgr.apply_config(
        {
            "data_center": "N",
            "system_code": "keep-me",
            "node": "n1",
            "format": {
                "schema_version": "1.0.0",
                "header_fields": list(default_format().header_fields),
                "content_fields": list(default_format().content_fields),
                "required_fields": list(default_format().required_fields),
            },
        }
    )
    with pytest.raises(SchemaValidationError):
        mgr.apply_config(
            {
                "otel": {"enabled": True, "endpoint": "", "protocol": "grpc"},
            }
        )
    assert mgr.config.identity.system_code == "keep-me"
    mgr.log_audit(
        "UA",
        level="INFO",
        UA="still-works",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    assert mem.records[0]["attributes"]["system_code"] == "keep-me"


def test_apply_config_accepts_audit_log_config_object():
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    cfg = AuditLogConfig.from_dict(
        {
            "data_center": "S",
            "system_code": "obj",
            "node": "n2",
            "otel": {"enabled": False, "endpoint": "http://localhost:4317"},
        }
    )
    mgr.apply_config(cfg)
    assert mgr.config.identity.data_center == "S"
    assert mgr.config.identity.system_code == "obj"


def test_custid_always_placeholder_even_if_explicit():
    cfg = AuditLogConfig.from_dict(
        {"data_center": "N", "system_code": "x", "node": "n"}
    )
    attrs = build_audit_attributes(
        AuditSnapshot.from_config(cfg),
        event_type="UA",
        level="INFO",
        fields={
            "CUSTID": "should-not-appear",
            "UA": "ok",
            "RSPCD": "0000",
            "SUBMDL": "gateway",
            "PROC": "authenticate",
        },
        clock=_FixedClock(1.0),
        caller="t:1",
    )
    assert attrs["CUSTID"] == "-"


def test_empty_string_fields_become_placeholder():
    cfg = AuditLogConfig.from_dict(
        {"data_center": "N", "system_code": "x", "node": "n"}
    )
    attrs = build_audit_attributes(
        AuditSnapshot.from_config(cfg),
        event_type="UA",
        level="INFO",
        fields={
            "UA": "",
            "RSPCD": "0000",
            "SUBMDL": "gateway",
            "PROC": "authenticate",
            "COST": None,
        },
        clock=_FixedClock(1.0),
        caller="t:1",
    )
    assert attrs["UA"] == "-"
    assert attrs["COST"] == "-"


def test_resolve_service_name_prefix():
    assert resolve_service_name(None) is None
    assert resolve_service_name("") is None
    assert resolve_service_name("gateway") == "jiuwenclaw-gateway"
    assert resolve_service_name("jiuwenclaw-gateway") == "jiuwenclaw-gateway"


def test_otel_enabled_requires_endpoint():
    with pytest.raises(SchemaValidationError, match="endpoint"):
        AuditLogConfig.from_dict(
            {"otel": {"enabled": True, "endpoint": "  ", "protocol": "grpc"}}
        )


def test_timestamp_format_must_be_default():
    with pytest.raises(SchemaValidationError, match="timestamp_format"):
        validate_format(
            FormatSpec(
                header_fields=("timestamp", "level"),
                content_fields=("UID",),
                required_fields=("timestamp", "level"),
                timestamp_format="iso8601",
            )
        )


def test_empty_header_fields_rejected():
    with pytest.raises(SchemaValidationError, match="header_fields"):
        validate_format(
            FormatSpec(
                header_fields=(),
                content_fields=("UID",),
                required_fields=("UID",),
            )
        )


def test_context_empty_string_does_not_map():
    tokens = bind_audit_context(session_id="", user_id="")
    try:
        cfg = AuditLogConfig.from_dict(
            {"data_center": "N", "system_code": "x", "node": "n"}
        )
        attrs = build_audit_attributes(
            AuditSnapshot.from_config(cfg),
            event_type="UA",
            level="INFO",
            fields={
                "UA": "ok",
                "RSPCD": "0000",
                "SUBMDL": "gateway",
                "PROC": "authenticate",
            },
            clock=_FixedClock(1.0),
            caller="t:1",
        )
    finally:
        reset_audit_context(tokens)

    assert attrs["trace_id"] == "-"
    assert attrs["UID"] == "-"
