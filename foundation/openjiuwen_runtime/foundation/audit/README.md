# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""foundation.audit

横切的安全审计日志能力，落在 ``openjiuwen_runtime.foundation.audit``。

**本阶段范围：** 字段清单可配的 ``format`` + ``log_audit`` / ``log_event`` → **OTEL Logs emit**  
（复用进程内由 telemetry 安装的 ``LoggerProvider``）。  
**otel 开关 / endpoint：** 由部署 env（``OTEL_*``）与 telemetry 配置，**不由 Manager 下发驱动**。  
**不做：** 管道文本行、本地 ``audit-*.log``、NTP 同步、脱敏、SIEM。

## 用法

```python
from openjiuwen_runtime.foundation.audit import (
    log_audit,
    log_event,
    audit_info,
    get_audit_manager,
    bind_audit_context,
    reset_audit_context,
)

tokens = bind_audit_context(session_id="sess_1", request_id="req_1", user_id="alice")
try:
    log_event(submdl="gateway", proc="authenticate", success=True, desc="用户登录成功")
    # 或契约形态
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

# Manager 只下发 format / identity（otel 块若仍存在则忽略写出）
get_audit_manager().apply_config({
    "format": {
        "schema_version": "1.0.0",
        "header_fields": [
            "schema_version", "timestamp", "level", "data_center",
            "system_code", "node", "trace_id", "txn_seq",
            "pid", "tid", "caller",
        ],
        "content_fields": [
            "UID", "CUSTID", "SRCIP", "DSTIP", "COST",
            "UA", "EVT", "MSG", "RSPCD", "SUBMDL", "PROC",
            "SVRNAM", "ACTION", "SANDBOXID", "RESULT",
        ],
        "required_fields": [
            "timestamp", "level", "UID", "CUSTID", "SRCIP", "DSTIP",
            "RSPCD", "SUBMDL", "PROC",
        ],
        "placeholder": "-",
        "timestamp_format": "yyyyMMdd-HH:mm:ss.SSS",
    },
    "data_center": "N",
    "system_code": "99900180001",
    "node": "10.0.0.1:8080",
    "service": "gateway",
})
```

## OTEL 依赖与写出

- foundation 默认依赖含 ``opentelemetry-api/sdk`` 与 OTLP/HTTP exporter（对齐第二版）。
- 写出端：``SharedProviderEmitter`` 复用 ``opentelemetry._logs`` 全局 SDK ``LoggerProvider``
  （由 jiuwenswarm ``TelemetryRuntime`` 在 ``OTEL_ENABLED=true`` 且 logs exporter=otlp 时安装）。
- 无 Provider 时 emit 降级为 no-op 并 warning，**不抛业务异常**。

单测可注入 ``MemoryEmitter``：

```python
from openjiuwen_runtime.foundation.audit import AuditManager, MemoryEmitter, reset_audit_manager

mem = MemoryEmitter()
reset_audit_manager(AuditManager(emitter=mem))
```

## Loki 桥接字段

每条记录在规范大写字段之外，始终附加小写桥接键：``audit_type`` / ``submdl`` / ``proc`` /
``outcome`` / ``session_id`` / ``user_id`` 等，供 Observability Web LogQL 查询。
