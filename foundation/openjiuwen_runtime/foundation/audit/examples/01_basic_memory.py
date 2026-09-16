# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""最小闭环：注入 MemoryEmitter → 绑定上下文 → log_audit → 打印写出记录。

不依赖 OTEL collector / audit-otel extra，适合本地快速验证门面用法。
"""

from __future__ import annotations

import json
import sys

from openjiuwen_runtime.foundation.audit import (
    AuditManager,
    MemoryEmitter,
    NoopEmitter,
    bind_audit_context,
    log_audit,
    reset_audit_context,
    reset_audit_manager,
)


def _print_record(index: int, record: dict) -> None:
    print(f"--- record[{index}] severity={record['severity']!r} body={record['body']!r}")
    print(json.dumps(record["attributes"], ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    mem = MemoryEmitter()
    reset_audit_manager(AuditManager(emitter=mem))

    tokens = bind_audit_context(
        session_id="sess_1",
        request_id="req_1",
        user_id="alice",
        src_ip="10.0.0.1",
        dst_ip="10.0.0.2",
    )
    try:
        log_audit(
            "UA",
            level="INFO",
            UA="用户登录成功",
            RSPCD="0000",
            SUBMDL="gateway",
            PROC="authenticate",
            COST=45,
        )
    finally:
        reset_audit_context(tokens)

    print(f"emitted {len(mem.records)} record(s)\n")
    for i, record in enumerate(mem.records):
        _print_record(i, record)

    # 用 Noop 收尾，避免重建默认 Manager 时尝试拉起 OTEL
    reset_audit_manager(AuditManager(emitter=NoopEmitter()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
