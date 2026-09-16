# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""log_audit emit 行为（MemoryEmitter）。"""

from __future__ import annotations

import logging

from openjiuwen_runtime.foundation.audit import (
    AuditManager,
    EVENT_TYPE_ATTR,
    MemoryEmitter,
    NoopEmitter,
    OtelConfig,
    reset_audit_manager,
)


def test_emit_attribute_keys_match_format():
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    reset_audit_manager(mgr)

    mgr.log_audit(
        "UA",
        level="INFO",
        UA="用户登录成功",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
        COST=45,
    )

    assert len(mem.records) == 1
    attrs = mem.records[0]["attributes"]
    enabled = set(mgr.config.format.enabled_fields) | {EVENT_TYPE_ATTR}
    assert set(attrs.keys()) == enabled
    assert attrs[EVENT_TYPE_ATTR] == "UA"
    assert attrs["RSPCD"] == "0000"
    assert mem.records[0]["severity"] == "INFO"


def test_enabled_false_uses_noop_without_raise():
    mgr = AuditManager()
    mgr.apply_config(
        {
            "otel": {
                "enabled": False,
                "endpoint": "http://localhost:4317",
                "protocol": "grpc",
            }
        }
    )
    assert isinstance(mgr._emitter, NoopEmitter)  # noqa: SLF001
    mgr.log_audit(
        "UA",
        level="INFO",
        UA="x",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )


def test_missing_required_still_emits_with_warning(caplog):
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    with caplog.at_level(logging.WARNING):
        mgr.log_audit(
            "UA",
            level="INFO",
            # missing UA / RSPCD / SUBMDL / PROC → warning but still emit
        )
    assert len(mem.records) == 1
    assert any("required fields" in r.message for r in caplog.records)


def test_build_emitter_disabled_is_noop():
    from openjiuwen_runtime.foundation.audit.emitter import build_emitter

    emitter = build_emitter(OtelConfig(enabled=False))
    assert isinstance(emitter, NoopEmitter)
