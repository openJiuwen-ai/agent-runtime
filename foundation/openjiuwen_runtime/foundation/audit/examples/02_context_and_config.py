# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""ContextVar 合并优先级 + apply_config 热更 + audit_* 助手。

演示：
1. apply_config 写入 format / 身份字段 / service；
2. ContextVar 映射到 trace_id / txn_seq / UID / SRCIP / DSTIP；
3. 显式 fields 覆盖 ContextVar（UID / SRCIP）；
4. audit_info / audit_warn / audit_error 自带 level；
5. otel.enabled=false 时降级为 Noop（不抛业务异常）；注入 MemoryEmitter 后仍可观察写出。
"""

from __future__ import annotations

import json
import sys

from openjiuwen_runtime.foundation.audit import (
    AuditLogConfig,
    AuditManager,
    MemoryEmitter,
    NoopEmitter,
    OtelConfig,
    audit_error,
    audit_info,
    audit_warn,
    bind_audit_context,
    get_audit_manager,
    reset_audit_context,
    reset_audit_manager,
)

# 与模块 README 对齐的一份可配 format（本阶段 emit_only）
_DEMO_FORMAT = {
    "schema_version": "1.0.0",
    "header_fields": [
        "schema_version",
        "timestamp",
        "level",
        "data_center",
        "system_code",
        "node",
        "trace_id",
        "txn_seq",
        "pid",
        "tid",
        "caller",
    ],
    "content_fields": [
        "UID",
        "CUSTID",
        "SRCIP",
        "DSTIP",
        "COST",
        "UA",
        "EVT",
        "MSG",
        "RSPCD",
        "SUBMDL",
        "PROC",
        "SVRNAM",
        "ACTION",
        "SANDBOXID",
        "RESULT",
    ],
    "required_fields": [
        "timestamp",
        "level",
        "UID",
        "CUSTID",
        "SRCIP",
        "DSTIP",
        "RSPCD",
        "SUBMDL",
        "PROC",
    ],
    "placeholder": "-",
    "timestamp_format": "yyyyMMdd-HH:mm:ss.SSS",
}


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def _print_record(index: int, record: dict) -> None:
    attrs = record["attributes"]
    keys = (
        "event_type",
        "level",
        "trace_id",
        "txn_seq",
        "UID",
        "SRCIP",
        "DSTIP",
        "UA",
        "EVT",
        "RSPCD",
        "SUBMDL",
        "PROC",
        "data_center",
        "system_code",
        "node",
    )
    slim = {k: attrs.get(k) for k in keys if k in attrs}
    print(
        f"[{index}] severity={record['severity']!r} body={record['body']!r}\n"
        f"{json.dumps(slim, ensure_ascii=False, indent=2)}"
    )


def main() -> int:
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    reset_audit_manager(mgr)

    # 1) 热更配置：format + 身份 + service；otel 关闭 → 真实写出端本应是 Noop，
    #    但本示例用 set_emitter 固定 MemoryEmitter，便于本地打印。
    mgr.apply_config(
        {
            "format": _DEMO_FORMAT,
            "otel": {
                "enabled": False,
                "endpoint": "http://localhost:4317",
                "protocol": "grpc",
                "headers": {},
            },
            "data_center": "N",
            "system_code": "99900180001",
            "node": "10.0.0.1:8080",
            "service": "gateway",
        }
    )
    # apply_config 在存在 override emitter 时会保留 MemoryEmitter；再演示一次
    # 「无 override 时 enabled=false → Noop」（构造时关闭 otel，避免拉起 OTEL）。
    probe = AuditManager(AuditLogConfig(otel=OtelConfig(enabled=False)))
    _print_section("otel.enabled=false without override")
    emitter_name = type(probe._emitter).__name__  # noqa: SLF001
    print(f"emitter type = {emitter_name} (expect NoopEmitter)")
    if not isinstance(probe._emitter, NoopEmitter):  # noqa: SLF001
        print("warning: expected NoopEmitter when otel.enabled=false")
    probe.log_audit(
        "UA",
        level="INFO",
        UA="noop path",
        RSPCD="0000",
        SUBMDL="gateway",
        PROC="authenticate",
    )
    print("log_audit on NoopEmitter: no exception raised")

    # 2) ContextVar 映射 + 显式覆盖
    tokens = bind_audit_context(
        session_id="sess_ctx",
        request_id="req_ctx",
        user_id="alice",
        src_ip="10.0.0.1",
        dst_ip="10.0.0.2",
    )
    try:
        _print_section("context mapped (UID/SRCIP from ContextVar)")
        audit_info(
            "UA",
            UA="登录成功（UID 来自 ContextVar）",
            RSPCD="0000",
            SUBMDL="gateway",
            PROC="authenticate",
            COST=12,
        )

        _print_section("explicit fields override ContextVar (UID/SRCIP)")
        audit_info(
            "UA",
            UA="登录成功（UID/SRCIP 显式覆盖）",
            UID="bob",
            SRCIP="203.0.113.10",
            RSPCD="0000",
            SUBMDL="gateway",
            PROC="authenticate",
        )

        _print_section("audit_warn / audit_error levels")
        audit_warn(
            "EVT",
            EVT="鉴权失败次数偏高",
            RSPCD="E001",
            SUBMDL="gateway",
            PROC="authenticate",
        )
        audit_error(
            "EVT",
            EVT="鉴权通道不可用",
            RSPCD="E005",
            SUBMDL="gateway",
            PROC="authenticate",
        )
    finally:
        reset_audit_context(tokens)

    _print_section("emitted records (MemoryEmitter)")
    print(f"total = {len(mem.records)}")
    for i, record in enumerate(mem.records):
        _print_record(i, record)

    if get_audit_manager() is not mgr:
        print("warning: process singleton was replaced unexpectedly")

    reset_audit_manager(AuditManager(emitter=NoopEmitter()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
