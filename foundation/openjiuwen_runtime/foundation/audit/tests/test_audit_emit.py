# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

# pylint: disable=protected-access

"""log_audit emit 行为（MemoryEmitter）。"""

from __future__ import annotations

import logging

from openjiuwen_runtime.foundation.audit import (
    EVENT_TYPE_ATTR,
    AuditManager,
    MemoryEmitter,
    SharedProviderEmitter,
    reset_audit_manager,
)
from openjiuwen_runtime.foundation.audit.emitter import build_emitter


def test_emit_attribute_keys_include_format_and_bridge():
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
    assert enabled.issubset(set(attrs.keys()))
    assert attrs[EVENT_TYPE_ATTR] == "UA"
    assert attrs["RSPCD"] == "0000"
    assert attrs["audit_type"] == "ua"
    assert attrs["submdl"] == "gateway"
    assert attrs["proc"] == "authenticate"
    assert attrs["outcome"] == "success"
    assert mem.records[0]["severity"] == "INFO"


def test_apply_config_does_not_rebuild_emitter_from_otel():
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    mgr.apply_config(
        {
            "otel": {
                "enabled": False,
                "endpoint": "http://localhost:4318",
                "protocol": "http",
            },
            "system_code": "KEEP",
        }
    )
    # override 保持；otel.enabled 不再把写出端打成 Noop
    assert mgr._emitter is mem  # noqa: SLF001
    mgr.log_audit(
        "UA",
        level="INFO",
        UA="x",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    assert len(mem.records) == 1
    assert mgr.config.identity.system_code == "KEEP"


def test_missing_required_still_emits_with_warning(caplog):
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    with caplog.at_level(logging.WARNING):
        mgr.log_audit(
            "UA",
            level="INFO",
        )
    assert len(mem.records) == 1
    assert any("required fields" in r.message for r in caplog.records)


def test_build_emitter_returns_shared_provider():
    emitter = build_emitter(None)
    assert isinstance(emitter, SharedProviderEmitter)


def test_log_event_maps_success_to_ua():
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    mgr.log_event(submdl="gateway", proc="ws_resolve_identity", success=True)
    assert len(mem.records) == 1
    attrs = mem.records[0]["attributes"]
    assert attrs[EVENT_TYPE_ATTR] == "UA"
    assert attrs["SUBMDL"] == "gateway"
    assert attrs["PROC"] == "ws_resolve_identity"
    assert attrs["RSPCD"] == "0000"
    assert attrs["audit_type"] == "ua"
    assert attrs["outcome"] == "success"


def test_log_event_extra_cost_and_session_mapping():
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    mgr.log_event(
        submdl="api_client",
        proc="http_agent_send",
        success=True,
        session_id="webhttp_abc",
        request_id="req_1",
        extra={"COST": 33},
    )
    attrs = mem.records[0]["attributes"]
    assert attrs["COST"] == "33"
    assert attrs["trace_id"] == "webhttp_abc"
    assert attrs["txn_seq"] == "req_1"
