# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR1 管道格式化 / 解析。"""

from __future__ import annotations

import pytest

from openjiuwen_runtime.foundation.audit import (
    AuditFacade,
    AuditLogSchema,
    AuditNotImplementedError,
    FormatError,
    RuntimeIdentity,
    default_schema,
    format_audit_line,
    format_timestamp,
    log_audit,
    parse_audit_line,
)
from openjiuwen_runtime.foundation.audit.schema import ContentSchema, HeaderSchema


class _FixedClock:
    def __init__(self, ts: float) -> None:
        self._ts = ts

    def now(self) -> float:
        return self._ts


IDENTITY = RuntimeIdentity(
    data_center="N",
    system_code="99900180001",
    node="10.0.0.1:8080",
)


def _base_elements(**overrides):
    data = {
        "UID": "wangzeyu",
        "CUSTID": "6217991100012345678",
        "SRCIP": "192.168.1.100",
        "DSTIP": "10.0.0.1",
        "COST": 45,
        "UA": "用户登录成功",
        "MSG": "用户登录成功",
        "RSPCD": "0000",
        "SUBMDL": "gateway",
        "PROC": "authenticate",
    }
    data.update(overrides)
    return data


def test_golden_line_matches_srs_appendix_when_header_fully_specified():
    ts = 1_713_610_200.123  # arbitrary; compared via format_timestamp
    clock = _FixedClock(ts)
    line = format_audit_line(
        clock=clock,
        identity=IDENTITY,
        level="INFO",
        keyword="UA",
        elements=_base_elements(),
        header={
            "trace_id": "TXN20250420103000123",
            "txn_seq": "001",
            "pid": "12345",
            "tid": "tid-7f3a",
            "caller": "platform.gateway:58",
        },
    )
    parsed = parse_audit_line(line)
    assert parsed.header["schema_version"] == "1.0.0"
    assert parsed.header["timestamp"] == format_timestamp(ts)
    assert parsed.header["level"] == "INFO"
    assert parsed.header["data_center"] == "N"
    assert parsed.header["system_code"] == "99900180001"
    assert parsed.header["node"] == "10.0.0.1:8080"
    assert parsed.header["trace_id"] == "TXN20250420103000123"
    assert parsed.header["txn_seq"] == "001"
    assert parsed.header["pid"] == "12345"
    assert parsed.header["tid"] == "tid-7f3a"
    assert parsed.header["caller"] == "platform.gateway:58"
    assert parsed.keyword == "UA"
    assert parsed.elements["UID"] == "wangzeyu"
    assert parsed.elements["RSPCD"] == "0000"
    assert parsed.elements["UA"] == "用户登录成功"
    assert parsed.elements["SUBMDL"] == "gateway"
    assert parsed.elements["PROC"] == "authenticate"
    assert len(parsed.header) == 11
    # 头部以 schema_version 打头，内容以 #UA: 连接，无多余空格。
    assert line.startswith("1.0.0|")
    assert "|#UA:UID=wangzeyu|@|" in line
    assert "|@|RSPCD=0000|@|SUBMDL=gateway|@|PROC=authenticate" in line


def test_header_omitted_fields_use_placeholder_not_dropped():
    line = format_audit_line(
        identity=IDENTITY,
        elements=_base_elements(),
        header={"pid": "1", "tid": "tid-1", "caller": "mod.fn:1"},
    )
    parsed = parse_audit_line(line)
    assert parsed.header["trace_id"] == "-"
    assert parsed.header["txn_seq"] == "-"
    assert parsed.header["schema_version"] == "1.0.0"
    assert all(parsed.header[k] != "" for k in parsed.header)


def test_empty_string_content_becomes_placeholder():
    line = format_audit_line(
        elements=_base_elements(CUSTID="", SRCIP=None),
        header={"caller": "mod.fn:9", "pid": "1", "tid": "tid-1"},
        identity=IDENTITY,
    )
    parsed = parse_audit_line(line)
    assert parsed.elements["CUSTID"] == "-"
    assert parsed.elements["SRCIP"] == "-"


def test_required_fields_always_present_even_if_caller_omits_them():
    line = format_audit_line(
        elements={"UA": "ping", "RSPCD": "0000"},
        keyword="UA",
        header={"caller": "mod.fn:2", "pid": "1", "tid": "t"},
    )
    parsed = parse_audit_line(line)
    for key in ("UID", "CUSTID", "SRCIP", "DSTIP", "RSPCD", "SUBMDL", "PROC", "UA"):
        assert key in parsed.elements
        assert parsed.elements[key] != ""


def test_replace_mode_strips_pipe_in_values():
    line = format_audit_line(
        elements=_base_elements(UA="读取 A|B 两张表"),
        header={"caller": "mod.fn:3", "pid": "1", "tid": "t"},
    )
    parsed = parse_audit_line(line)
    assert parsed.elements["UA"] == "读取 A_B 两张表"
    content = line.split("#UA:", 1)[1]
    # 要素分隔符含 |@|，但值内不得再出现用于「裸字段分隔」的单 | 作为值。
    assert "A|B" not in content


def test_escape_mode_backslash_escapes():
    schema = AuditLogSchema(
        header=default_schema().header,
        content=ContentSchema(escape_mode="escape", pipe_escape="_"),
        redline_fields=default_schema().redline_fields,
    )
    line = format_audit_line(
        schema=schema,
        elements=_base_elements(UA="a|b=c"),
        header={"caller": "mod.fn:4", "pid": "1", "tid": "t"},
    )
    # escape 后值中带 \| 与 \=
    assert r"UA=a\|b\=c" in line


def test_evt_keyword_and_auth_failure_rspcd():
    line = format_audit_line(
        level="ERROR",
        keyword="EVT",
        elements={
            "UID": "wangzeyu",
            "EVT": "auth_failure_exceeded",
            "MSG": "用户认证失败累计3次，会话已锁定",
            "RSPCD": "E001",
            "SUBMDL": "gateway",
            "PROC": "authenticate",
            "SVRNAM": "auth_service",
        },
        header={"caller": "platform.auth:102", "pid": "12345", "tid": "tid-7f3a"},
        identity=IDENTITY,
    )
    parsed = parse_audit_line(line)
    assert parsed.keyword == "EVT"
    assert parsed.header["level"] == "ERROR"
    assert parsed.elements["EVT"] == "auth_failure_exceeded"
    assert parsed.elements["RSPCD"] == "E001"
    assert "UA" not in parsed.elements


def test_invalid_level_rejected():
    with pytest.raises(FormatError, match="level"):
        format_audit_line(
            level="TRACE",
            elements=_base_elements(),
            header={"caller": "m.f:1"},
        )


def test_invalid_rspcd_rejected():
    with pytest.raises(FormatError, match="RSPCD"):
        format_audit_line(
            elements=_base_elements(RSPCD="OK"),
            header={"caller": "m.f:1"},
        )


def test_extended_rspcd_e006_allowed():
    line = format_audit_line(
        elements=_base_elements(RSPCD="E006"),
        header={"caller": "m.f:1", "pid": "1", "tid": "t"},
    )
    assert parse_audit_line(line).elements["RSPCD"] == "E006"


def test_caller_without_lineno_rejected():
    with pytest.raises(FormatError, match="line number"):
        format_audit_line(
            elements=_base_elements(),
            header={"caller": "platform.gateway"},
        )


def test_auto_caller_includes_lineno():
    line = format_audit_line(
        elements=_base_elements(),
        header={"pid": "1", "tid": "t"},
    )
    caller = parse_audit_line(line).header["caller"]
    assert ":" in caller
    assert caller.rsplit(":", 1)[-1].isdigit()
    assert "test_audit_formatter" in caller


def test_ext_segment_uses_x_prefix():
    line = format_audit_line(
        elements=_base_elements(),
        ext={"policy_name": "strict", "sandbox_id": "abc123def456", "result": "success"},
        header={"caller": "sandbox.manager:192", "pid": "1", "tid": "t"},
    )
    parsed = parse_audit_line(line)
    assert parsed.ext["x_policy_name"] == "strict"
    assert parsed.ext["x_sandbox_id"] == "abc123def456"
    assert parsed.ext["x_result"] == "success"
    assert "EXT=x_policy_name=strict" in line


def test_sandbox_action_fields_moved_to_ext():
    line = format_audit_line(
        elements=_base_elements(ACTION="create", SANDBOXID="abc", RESULT="success"),
        header={"caller": "sandbox.manager:192", "pid": "1", "tid": "t"},
    )
    parsed = parse_audit_line(line)
    assert "ACTION" not in parsed.elements
    assert parsed.ext["x_action"] == "create"
    assert parsed.ext["x_sandbox_id"] == "abc"


def test_keyword_optional_starts_from_first_element():
    schema = AuditLogSchema(
        header=default_schema().header,
        content=ContentSchema(keyword_required=False),
        redline_fields=default_schema().redline_fields,
    )
    line = format_audit_line(
        schema=schema,
        keyword=None,
        elements=_base_elements(),
        header={"caller": "m.f:1", "pid": "1", "tid": "t"},
    )
    assert "#UA:" not in line
    assert "UID=wangzeyu|@|" in line
    parsed = parse_audit_line(line, schema)
    assert parsed.keyword is None
    assert parsed.elements["UID"] == "wangzeyu"


def test_keyword_required_schema_rejects_missing_keyword():
    schema = AuditLogSchema(
        header=default_schema().header,
        content=ContentSchema(keyword_required=True),
        redline_fields=default_schema().redline_fields,
    )
    with pytest.raises(FormatError, match="keyword"):
        format_audit_line(
            schema=schema,
            keyword=None,
            elements=_base_elements(),
            header={"caller": "m.f:1"},
        )


def test_custom_header_order_still_emits_every_declared_field():
    fields = list(default_schema().header.fields)
    fields.remove("node")
    fields.append("node")
    schema = AuditLogSchema(header=HeaderSchema(fields=tuple(fields)))
    line = format_audit_line(
        schema=schema,
        identity=IDENTITY,
        elements=_base_elements(),
        header={"caller": "m.f:1", "pid": "1", "tid": "t"},
    )
    parsed = parse_audit_line(line, schema)
    assert list(parsed.header.keys())[-1] == "node"
    assert parsed.header["node"] == "10.0.0.1:8080"


def test_log_audit_not_implemented():
    with pytest.raises(AuditNotImplementedError, match="FR4"):
        log_audit("UA", UID="x")
    with pytest.raises(AuditNotImplementedError, match="FR4"):
        AuditFacade().log_audit("UA", UID="x")


def test_facade_format_line_uses_config_identity():
    facade = AuditFacade()
    # default identity is placeholders
    line = facade.format_line(
        elements=_base_elements(),
        header={"caller": "m.f:8", "pid": "1", "tid": "t"},
    )
    parsed = parse_audit_line(line)
    assert parsed.header["data_center"] == "-"
    assert parsed.keyword == "UA"
