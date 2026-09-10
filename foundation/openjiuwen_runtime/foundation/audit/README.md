# foundation.audit

横切的安全审计日志能力，落在 `openjiuwen_runtime.foundation.audit`。
业务子系统只依赖本包，不感知落盘、外送与管理面。

**当前实现范围：SRS FR1（必选）。** FR2–FR8 已搭配置骨架与接口，方法体为 `AuditNotImplementedError`。

## 定位

- 审计日志 ≠ 业务 DEBUG / metric / APM。
- 审计日志 ≠ Service 层现有的 `ctx.audit()`（结构化应用审计，走标准 logging）。
- 本模块产出 **schema 驱动的管道格式** 行，供后续异步落盘（`SEC-*.log`）与 SIEM 外送。

## 目录

| 文件 | 职责 | 状态 |
| --- | --- | --- |
| `schema.py` / `formatter.py` / `validator.py` / `clock.py` | FR1 格式、校验门、NTP | **已实现** |
| `config.py` / `models.py` / `facade.py` | 配置聚合与门面 | FR1 可用；`log_audit` 待 FR4 |
| `linkpoint.py` | FR2 打点对照表 | 骨架 |
| `redaction.py` | FR3 脱敏 | 骨架 |
| `writer.py` | FR4 异步输出 | 骨架 |
| `storage.py` | FR5 存储轮转留存 | 骨架 |
| `shipper.py` | FR6 采集外送 | 骨架 |
| `access.py` | FR7 访问控制 | 骨架 |
| `deploy.py` | FR8 配置下发 | 骨架 |
| `DESIGN.md` | 模块设计 | — |

## FR1 用法

```python
from openjiuwen_runtime.foundation.audit import (
    AuditLogConfig,
    RuntimeIdentity,
    SyncedClock,
    format_audit_line,
    validate_schema,
)

cfg = AuditLogConfig.from_dict({
    "format": {
        "schema_version": "1.0.0",
        "header": {
            "fields": [
                "schema_version", "timestamp", "level", "data_center",
                "system_code", "node", "trace_id", "txn_seq",
                "pid", "tid", "caller",
            ],
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
        "redline_fields": [
            "timestamp", "UID", "UA", "EVT", "RSPCD",
            "SRCIP", "DSTIP", "CUSTID", "SUBMDL", "PROC",
        ],
    },
    "ntp": {
        "servers": [],          # 部署注入真实时间源，禁止硬编码
        "sync_interval": "300s",
        "max_offset_ms": 500,
        "failover": True,
    },
    "data_center": "N",
    "system_code": "99900180001",
    "node": "10.0.0.1:8080",
})

line = format_audit_line(
    schema=cfg.format,
    identity=RuntimeIdentity(
        data_center=cfg.identity.data_center,
        system_code=cfg.identity.system_code,
        node=cfg.identity.node,
    ),
    level="INFO",
    keyword="UA",
    elements={
        "UID": "wangzeyu",
        "CUSTID": "6217991100012345678",
        "SRCIP": "192.168.1.100",
        "DSTIP": "10.0.0.1",
        "COST": 45,
        "UA": "用户登录成功",
        "MSG": "用户登录成功",
        "RSPCD": "0000",
        "SUBMDL": "gateway",
        "PROC": "authenticate",
    },
    header={"trace_id": "TXN20250420103000123", "txn_seq": "001"},
)
```

默认 schema 产出形态：

```text
1.0.0|{timestamp}|INFO|N|99900180001|{node}|{trace_id}|{txn_seq}|{pid}|{tid}|{caller}:#UA:UID=...|@|CUSTID=...|@|RSPCD=0000|@|...
```

- 头部 11 字段按 schema 顺序输出；无值填占位符 `-`，禁止省略。
- 内容 `#UA` / `#EVT` + `|@|` 键值。成功正常流程打 `#UA`，失败/违规/告警打 `#EVT`。
- 值中的 `|` 在默认 `replace` 模式下替换为 `_`。
- `schema_version` 逐行输出，滚动升级期可并存多版本。

## NTP

```python
from openjiuwen_runtime.foundation.audit import NtpConfig, SequenceNtpClient, SyncedClock

clock = SyncedClock(NtpConfig(servers=("ntp-a", "ntp-b")), client=SequenceNtpClient({
    "ntp-a": ConnectionError("down"),
    "ntp-b": 1710000000.0,
}))
clock.sync_once()          # 主源失败则切备用并产生 ClockEvent
ts = clock.now()           # 本地时钟 + offset
clock.start_periodic()     # 后台周期同步；测试记得 stop()
```

默认 `ntp.servers` 为空。全部不可达时 `degraded=True`，`now()` 退回本地时钟。

## 测试

```bash
cd foundation
uv run pytest tests/unit_tests/test_audit_schema.py \
              tests/unit_tests/test_audit_formatter.py \
              tests/unit_tests/test_audit_clock.py -q
```

## 尚未实现

`log_audit()` 会抛 `AuditNotImplementedError`：落盘前脱敏、异步写、`SEC-` 文件、外送、ACL、配置滚动下发分别对应 FR3–FR8。
