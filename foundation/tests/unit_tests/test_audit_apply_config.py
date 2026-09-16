# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""apply_config 热更快照隔离。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.audit import (
    AuditManager,
    FormatSpec,
    MemoryEmitter,
    default_format,
)


def test_apply_config_affects_subsequent_emits_only():
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)

    mgr.log_audit(
        "UA",
        level="INFO",
        UA="before",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    assert "SVRNAM" in mem.records[0]["attributes"]  # default content includes SVRNAM

    slim = FormatSpec(
        header_fields=("schema_version", "timestamp", "level"),
        content_fields=("UID", "UA", "RSPCD", "SUBMDL", "PROC"),
        required_fields=("timestamp", "level", "UID", "RSPCD", "SUBMDL", "PROC"),
    )
    mgr.apply_config(
        {
            "format": {
                "schema_version": slim.schema_version,
                "header_fields": list(slim.header_fields),
                "content_fields": list(slim.content_fields),
                "required_fields": list(slim.required_fields),
                "placeholder": "-",
                "timestamp_format": "yyyyMMdd-HH:mm:ss.SSS",
            },
            "data_center": "N",
            "system_code": "x",
            "node": "n",
        }
    )

    mgr.log_audit(
        "UA",
        level="INFO",
        UA="after",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    after = mem.records[1]["attributes"]
    assert "SVRNAM" not in after
    assert set(after.keys()) == set(slim.enabled_fields) | {"event_type"}


def test_apply_config_preserves_local_service_when_absent():
    mem = MemoryEmitter()
    mgr = AuditManager(
        emitter=mem,
    )
    mgr.apply_config(
        {
            "service": "agentserver",
            "data_center": "N",
            "system_code": "x",
            "node": "n",
            "format": {
                "schema_version": "1.0.0",
                "header_fields": list(default_format().header_fields),
                "content_fields": list(default_format().content_fields),
                "required_fields": list(default_format().required_fields),
            },
        }
    )
    assert mgr.config.service == "agentserver"
    mgr.apply_config(
        {
            "data_center": "S",
            "system_code": "y",
            "node": "m",
        }
    )
    assert mgr.config.service == "agentserver"
    assert mgr.config.identity.data_center == "S"
