# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FormatSpec / 配置预检。"""

from __future__ import annotations

import pytest

from openjiuwen_runtime.foundation.audit import (
    AuditLogConfig,
    FormatSpec,
    SchemaValidationError,
    default_config,
    default_format,
    validate_format,
)


def test_default_format_matches_design_lists():
    fmt = default_format()
    assert fmt.schema_version == "1.0.0"
    assert fmt.header_fields[0] == "schema_version"
    assert "timestamp" in fmt.header_fields
    assert "UID" in fmt.content_fields
    assert "RSPCD" in fmt.required_fields
    assert fmt.placeholder == "-"


def test_default_config_passes_gate():
    cfg = default_config()
    assert cfg.otel.enabled is True
    assert cfg.otel.protocol == "grpc"
    assert cfg.ntp.servers == ()
    cfg.validate()


def test_required_must_be_subset_of_enabled():
    fmt = FormatSpec(
        required_fields=("timestamp", "level", "NOT_IN_LIST"),
    )
    with pytest.raises(SchemaValidationError, match="subset"):
        validate_format(fmt)


def test_invalid_semver_rejected():
    with pytest.raises(SchemaValidationError, match="semver"):
        AuditLogConfig.from_dict(
            {
                "format": {
                    "schema_version": "v1",
                    "header_fields": list(default_format().header_fields),
                    "content_fields": list(default_format().content_fields),
                    "required_fields": list(default_format().required_fields),
                }
            }
        )


def test_invalid_otel_protocol_rejected():
    with pytest.raises(SchemaValidationError, match="protocol"):
        AuditLogConfig.from_dict(
            {
                "otel": {"enabled": True, "endpoint": "http://x", "protocol": "udp"},
            }
        )


def test_empty_placeholder_rejected():
    with pytest.raises(SchemaValidationError, match="placeholder"):
        FormatSpec(placeholder="")  # noqa: not validated until validate_format
        validate_format(FormatSpec(placeholder=""))


def test_duplicate_header_fields_rejected():
    fmt = FormatSpec(header_fields=("timestamp", "timestamp", "level"))
    with pytest.raises(SchemaValidationError, match="duplicates"):
        validate_format(fmt)
