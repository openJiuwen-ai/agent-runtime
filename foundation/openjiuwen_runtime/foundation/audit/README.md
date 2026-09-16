# foundation.audit

横切的安全审计日志能力，落在 `openjiuwen_runtime.foundation.audit`。

**本阶段范围：** 字段清单可配的 `format` + `log_audit` → **OTEL Logs emit**（`emit_only`）。  
**不做：** 管道文本行、本地 `audit-*.log`、NTP 同步、脱敏、SIEM。

## 用法

```python
from openjiuwen_runtime.foundation.audit import (
    log_audit,
    audit_info,
    get_audit_manager,
    bind_audit_context,
    reset_audit_context,
)

tokens = bind_audit_context(session_id="sess_1", request_id="req_1", user_id="alice")
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
    # 或
    audit_info("UA", UA="...", RSPCD="0000", SUBMDL="gateway", PROC="authenticate")
finally:
    reset_audit_context(tokens)

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
    "otel": {
        "enabled": True,
        "endpoint": "http://localhost:4317",
        "protocol": "grpc",
        "headers": {},
    },
    "data_center": "N",
    "system_code": "99900180001",
    "node": "10.0.0.1:8080",
    "service": "gateway",  # 进程本地；推导 service.name=jiuwenclaw-gateway
})
```

## OTEL 依赖

写出端需要 optional extra：

```bash
pip install "openjiuwen-runtime-foundation[audit-otel]"
```

未安装或 `otel.enabled=false` 时：`log_audit` 降级为 noop 并打进程 warning，**不抛业务异常**。

单测可注入 `MemoryEmitter`：

```python
from openjiuwen_runtime.foundation.audit import AuditManager, MemoryEmitter, reset_audit_manager

mem = MemoryEmitter()
reset_audit_manager(AuditManager(emitter=mem))
```

## 测试

```bash
cd foundation
uv run pytest tests/unit_tests/test_audit_format_spec.py \
              tests/unit_tests/test_audit_attributes.py \
              tests/unit_tests/test_audit_emit.py \
              tests/unit_tests/test_audit_apply_config.py \
              tests/unit_tests/test_audit_facade_api.py -q
```
