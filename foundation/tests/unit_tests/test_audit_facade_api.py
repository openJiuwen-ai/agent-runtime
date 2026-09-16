# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""门面 API：level / event_type / audit_* 助手。"""

from __future__ import annotations

import pytest

from openjiuwen_runtime.foundation.audit import (
    AuditManager,
    FormatError,
    MemoryEmitter,
    audit_error,
    audit_info,
    audit_warn,
    reset_audit_manager,
)


@pytest.fixture()
def mem_manager():
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    reset_audit_manager(mgr)
    yield mem, mgr
    reset_audit_manager()


def test_level_required(mem_manager):
    mem, mgr = mem_manager
    with pytest.raises(FormatError, match="level"):
        mgr.log_audit("UA", level="")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        mgr.log_audit("UA")  # missing level kw  # type: ignore[call-arg]


def test_event_type_required_and_validated(mem_manager):
    mem, mgr = mem_manager
    with pytest.raises(FormatError, match="event_type"):
        mgr.log_audit("OTHER", level="INFO")


def test_audit_helpers_set_level(mem_manager):
    mem, mgr = mem_manager
    audit_info(
        "UA",
        UA="ok",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    audit_warn(
        "EVT",
        EVT="warn",
        RSPCD="E001",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    audit_error(
        "EVT",
        EVT="err",
        RSPCD="E005",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    assert [r["severity"] for r in mem.records] == ["INFO", "WARN", "ERROR"]
