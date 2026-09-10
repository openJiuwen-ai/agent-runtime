# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR1 schema / 合规校验门 / 配置加载。"""

from __future__ import annotations

import pytest

from openjiuwen_runtime.foundation.audit import (
    AuditLogConfig,
    AuditLogSchema,
    DEFAULT_REDLINE_FIELDS,
    NtpConfig,
    SchemaValidationError,
    canonicalize_fields,
    default_config,
    default_schema,
    validate_schema,
)
from openjiuwen_runtime.foundation.audit.schema import HeaderSchema


def test_default_schema_has_eleven_header_fields_and_semver():
    schema = default_schema()
    assert schema.schema_version == "1.0.0"
    assert len(schema.header.fields) == 11
    assert schema.header.fields[0] == "schema_version"
    assert schema.header.fields[1] == "timestamp"
    assert schema.header.separator == "|"
    assert schema.header.placeholder == "-"
    assert schema.content.element_separator == "|@|"
    assert schema.content.escape_mode == "replace"
    assert tuple(schema.redline_fields) == DEFAULT_REDLINE_FIELDS


def test_default_config_passes_compliance_gate():
    cfg = default_config()
    assert cfg.ntp.servers == ()
    assert cfg.redaction.command_force_redact is True
    cfg.validate()


def test_default_ntp_servers_not_hardcoded():
    ntp = NtpConfig()
    assert ntp.servers == ()
    assert "pool.ntp.org" not in str(ntp)
    assert "ntp." not in "".join(ntp.servers)


def test_custom_schema_may_append_header_field():
    schema = AuditLogSchema.from_dict(
        {
            "schema_version": "1.1.0",
            "header": {
                "fields": list(default_schema().header.fields) + ["region"],
                "separator": "|",
                "placeholder": "-",
                "formats": {"timestamp": "yyyyMMdd-HH:mm:ss.SSS"},
            },
            "content": {"escape_mode": "replace", "pipe_escape": "_"},
            "redline_fields": list(DEFAULT_REDLINE_FIELDS),
        }
    )
    validate_schema(schema, ntp=NtpConfig())
    assert "region" in schema.header.fields


def test_gate_rejects_missing_redline_field():
    schema = AuditLogSchema.from_dict(
        {
            "schema_version": "1.0.0",
            "header": {
                "fields": list(default_schema().header.fields),
                "separator": "|",
                "placeholder": "-",
                "formats": {"timestamp": "yyyyMMdd-HH:mm:ss.SSS"},
            },
            "redline_fields": [f for f in DEFAULT_REDLINE_FIELDS if f != "UID"],
        }
    )
    with pytest.raises(SchemaValidationError, match="UID"):
        validate_schema(schema, ntp=NtpConfig())


def test_gate_rejects_timestamp_removed_from_header():
    fields = [f for f in default_schema().header.fields if f != "timestamp"]
    schema = AuditLogSchema(
        header=HeaderSchema(fields=tuple(fields)),
    )
    with pytest.raises(SchemaValidationError, match="timestamp"):
        validate_schema(schema, ntp=NtpConfig())


def test_gate_rejects_empty_placeholder():
    schema = AuditLogSchema(
        header=HeaderSchema(placeholder=""),
    )
    with pytest.raises(SchemaValidationError, match="placeholder"):
        validate_schema(schema, ntp=NtpConfig())


def test_gate_rejects_bad_semver():
    schema = AuditLogSchema(schema_version="v1")
    with pytest.raises(SchemaValidationError, match="semver"):
        validate_schema(schema, ntp=NtpConfig())


def test_gate_rejects_unknown_escape_mode():
    schema = AuditLogSchema.from_dict(
        {
            "schema_version": "1.0.0",
            "header": {
                "fields": list(default_schema().header.fields),
                "placeholder": "-",
                "separator": "|",
                "formats": {"timestamp": "yyyyMMdd-HH:mm:ss.SSS"},
            },
            "content": {"escape_mode": "none"},
        }
    )
    with pytest.raises(SchemaValidationError, match="escape_mode"):
        validate_schema(schema, ntp=NtpConfig())


def test_gate_rejects_missing_ntp():
    with pytest.raises(SchemaValidationError, match="ntp"):
        validate_schema(default_schema(), ntp=None)


def test_gate_rejects_placeholder_ntp_address():
    with pytest.raises(SchemaValidationError, match="placeholder"):
        validate_schema(
            default_schema(),
            ntp=NtpConfig(servers=("<主时间源>",)),
        )


def test_gate_rejects_weakened_redaction():
    with pytest.raises(SchemaValidationError, match="command_force_redact"):
        validate_schema(
            default_schema(),
            ntp=NtpConfig(),
            command_force_redact=False,
        )


def test_from_dict_srs_format_fragment():
    cfg = AuditLogConfig.from_dict(
        {
            "schema_version": "1.0.0",
            "header": {
                "fields": list(default_schema().header.fields),
                "separator": "|",
                "placeholder": "-",
                "formats": {"timestamp": "yyyyMMdd-HH:mm:ss.SSS"},
            },
            "content": {
                "keyword_prefix": "#",
                "element_separator": "|@|",
                "kv_separator": "=",
                "escape_mode": "replace",
                "pipe_escape": "_",
            },
            "redline_fields": list(DEFAULT_REDLINE_FIELDS),
            "ntp": {"servers": [], "sync_interval": "300s", "max_offset_ms": 500},
            "data_center": "N",
            "system_code": "99900180001",
        }
    )
    assert cfg.format.schema_version == "1.0.0"
    assert cfg.identity.data_center == "N"
    assert cfg.identity.system_code == "99900180001"


def test_from_dict_rejects_invalid_schema():
    with pytest.raises(SchemaValidationError):
        AuditLogConfig.from_dict(
            {
                "format": {
                    "schema_version": "1.0.0",
                    "header": {
                        "fields": list(default_schema().header.fields),
                        "placeholder": "",
                        "separator": "|",
                        "formats": {"timestamp": "yyyyMMdd-HH:mm:ss.SSS"},
                    },
                }
            }
        )


def test_canonicalize_fields_maps_aliases_once():
    out = canonicalize_fields(
        {"user": "alice", "src": "1.1.1.1", "RSPCD": "0000"},
        {"user": "UID", "src": "SRCIP"},
    )
    assert out["UID"] == "alice"
    assert out["SRCIP"] == "1.1.1.1"
    assert out["RSPCD"] == "0000"
    assert "user" not in out
