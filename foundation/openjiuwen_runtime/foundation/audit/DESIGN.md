# foundation.audit 模块设计

> 需求基线：《安全审计日志软件需求规格说明书》。本文描述本包如何落地该 SRS。
> 实现状态：FR1 已实现；FR2–FR8 仅接口与配置骨架。

---

## 1. 职责与边界

审计日志是横切关注点。本包提供统一写入门面，对调用方屏蔽格式化、脱敏、异步、外送、时间同步。

| 在范围内 | 不在范围内 |
| --- | --- |
| 管道格式 schema、红线字段、占位、转义 | 日志分析 / SIEM 本身 |
| NTP 抽象时间源 | 硬编码行内 NTP / 某云日志平台 |
| 后续 FR 的落盘、脱敏、外送、配置下发 | Service `ctx.audit()`、业务 DEBUG、metric、APM |

本包放在 **foundation** 而非 service/management，是为了让 Gateway、AgentServer、Runtime、沙箱采集层都能零控制面依赖地格式化同一规范行。

---

## 2. 包结构

```
audit/
  schema.py       头部 / 内容 schema 数据类
  validator.py    FR1.2.3 合规校验门；FR1.2.4 字段名canonicalize
  formatter.py    行格式化 + 解析
  clock.py        SyncedClock / UdpNtpClient / 可注入 NtpClient
  config.py       FR1–FR8 配置命名空间聚合
  facade.py       AuditFacade.format_line；log_audit 待 FR4
  models.py       RuntimeIdentity / ParsedAuditLine / ClockEvent
  linkpoint.py    FR2 对照表
  redaction.py    FR3
  writer.py       FR4
  storage.py      FR5
  shipper.py      FR6
  access.py       FR7
  deploy.py       FR8
```

调用关系：

```
AuditLogConfig.from_dict
        │
        ▼
validate_schema ──失败──► SchemaValidationError（schema 不生效）
        │成功
        ▼
format_audit_line(schema, clock, identity, keyword, elements)
        │
        ▼
一行管道文本（调用方可测；落盘走 FR4 AuditWriter）
```

---

## 3. FR1 设计

### 3.1 schema 两段

- **头部 schema**：字段集、顺序、分隔符、占位符、`timestamp` 格式。默认 11 字段，`schema_version` 必须逐行作为第 1 字段（由默认 `fields` 顺序保证；自定义 schema 只要把该标识留在 `header.fields` 即可重排）。
- **内容 schema**：关键字前缀、要素分隔符 `|@|`、键值符 `=`、`escape_mode`。内容里出现哪些业务要素 **不由 schema 枚举**，由打点代码填入；formatter 对 required 档缺省占位。

`schema_version` 专指字段集版本。管道协议版本若将来出现，另起 `format_version`，不混用。

### 3.2 占位与红线

- 头部声明的每个字段都会输出；`None` / `""` → placeholder（默认 `-`）。
- 内容 required：`UID CUSTID SRCIP DSTIP RSPCD SUBMDL PROC`，外加与关键字对应的 `UA` 或 `EVT`。
- 红线清单（默认不可删）：`timestamp UID UA EVT RSPCD SRCIP DSTIP CUSTID SUBMDL PROC`。
- 自定义 schema 允许增删非红线字段、调顺序、改分隔符，但合规校验门拒绝削弱红线 / 空占位符 / 弱化 NTP / `command_force_redact=False`。

### 3.3 转义

| mode | 规则 |
| --- | --- |
| `replace`（默认） | 值中 `\|` → `pipe_escape`（默认 `_`）；换行压成空格 |
| `escape` | `\` `\|` `=` 换行反斜杠转义 |

两种模式格式化后都不得残留裸 `|`，否则 `FormatError`。

### 3.4 关键字与 RSPCD

- `#UA`：成功且正常流程；`#EVT`：失败 / 违规 / 异常 / 告警。
- `keyword_required=False` 时允许无关键字，内容从首个要素开始。
- `RSPCD` 取值 `0000` 或 `E` + 3 位数字；已知码见 `RSPCD_KNOWN`，允许扩展但不允许破坏该形态。

### 3.5 EXT

extension 档（含沙箱 `ACTION` / `SANDBOXID` / `RESULT`）不进入规范键位。formatter 将其归一为 `x_` 前缀，按 SRS 示例展开：

```text
|@|EXT=x_policy_name=strict|@|x_sandbox_id=abc|@|x_result=success
```

### 3.6 合规校验门（FR1.2.3）

`AuditLogConfig.from_dict(..., validate=True)` 与 `AuditFacade` 构造时调用 `validate_schema`。失败抛 `SchemaValidationError`，配置不得生效。告警写 `#EVT` 依赖 FR4，当前仅以异常拒绝。

跨 schema 接入（FR1.2.4）只提供 `canonicalize_fields(incoming, aliases)`：在边界把别名映射成规范标识，内部不再认第二套名字。SIEM 字段映射属于 FR6，不进内部 schema。

### 3.7 NTP（FR1.1.1.2）

- `NtpConfig.servers` 默认空元组，**禁止**在代码或默认配置里写公网 / 行内地址。
- 占位字符串 `<主时间源>` 在校验门会被拒绝，避免把示例配进生产。
- `NtpClient` 协议可注入；生产用 `UdpNtpClient`（NTP UDP/123，无额外依赖）。
- `SyncedClock.sync_once()`：按优先级试源；非主源成功 → `ntp_source_failover`；偏差超阈值 → `ntp_offset_exceeded`；全部失败 → 降级本地时钟（offset=0，`degraded=True`），不抛给业务。
- `now() = time.time() + offset`。周期同步为守护线程，默认不自动 start。
- 事件通过 `format_clock_event` 变成 `#EVT` 行（`SUBMDL=audit`，`PROC=ntp_sync`）。

### 3.8 自动头部

| 字段 | 来源 |
| --- | --- |
| schema_version | schema |
| timestamp | clock.now()，毫秒格式 |
| pid | `os.getpid()` |
| tid | `tid-{ident:x}` |
| caller | `模块.函数:行号`（跳过本包栈帧）；缺行号则 `FormatError` |

`data_center` / `system_code` / `node` 来自 `RuntimeIdentity`（配置注入）。

---

## 4. 后续 FR 接入点（未实现）

| FR | 入口 | 约定 |
| --- | --- | --- |
| FR2 | `linkpoint.DEFAULT_LINKPOINTS` | 强制打点在各业务仓调用 `log_audit` |
| FR3 | `redaction.redact` | 必须在 writer 落盘前调用 |
| FR4 | `writer.AuditWriter.enqueue` | 打点入队 <1ms；`log_audit` 走这里 |
| FR5 | `storage.AuditStorage` | 文件名 `SEC-{dc}_{sys}_{node}.log` |
| FR6 | `shipper.AuditShipper` | endpoint 仅配置注入；失败缓冲补传 |
| FR7 | `access.AuditAccessControl` | 管理动作本身打 `#EVT` |
| FR8 | `deploy.AuditConfigDeployer` | 预检即 `validate_schema`；滚动期靠逐行 `schema_version` |

`AuditFacade.log_audit` 在 FR4 完成前故意抛 `AuditNotImplementedError`，避免调用方以为已经留痕。

---

## 5. 错误

| 类型 | 何时 |
| --- | --- |
| `SchemaValidationError` | 合规门失败 |
| `FormatError` | 非法 level / keyword / RSPCD / 缺行号 / 裸 `\|` |
| `ClockSyncError` | 单次 NTP 查询失败（时钟内部消化，业务看不到） |
| `AuditNotImplementedError` | 调用了未实现 FR |

---

## 6. 测试策略（FR1）

- schema / 校验门：默认通过；删红线、空占位符、弱化脱敏、占位 NTP 地址均拒绝。
- formatter：SRS 附录 A.1 金样（给定 header 时逐字段相等）；required 占位；`replace` / `escape`；EXT；无关键字；`parse_audit_line` 往返。
- clock：注入 `SequenceNtpClient` 覆盖主源成功、failover、偏差告警、全失败降级、禁止默认硬编码服务器。

不在单测中访问真实 NTP 网络。
