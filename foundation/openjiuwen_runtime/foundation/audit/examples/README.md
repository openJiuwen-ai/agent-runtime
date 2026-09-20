# foundation.audit examples

可运行示例，回答「怎么打点、怎么绑上下文、怎么热更配置」。

本阶段范围与模块一致：字段清单 `format` + `log_audit` → emit（示例用 `MemoryEmitter`，不接 collector）。  
不做：本地审计文件、管道行、脱敏、SIEM、真 OTEL 联调（后续可补 `otel_emit.py`）。

> 这里是开发者示例，不是单测。正确性请看同级 [`../tests/`](../tests/)。

## 目录

```text
examples/
├── README.md
├── basic_memory.py         # 最小闭环
└── context_and_config.py   # ContextVar / apply_config / audit_*
```

| 文件 | 做什么 |
| --- | --- |
| `basic_memory.py` | 注入 `MemoryEmitter`，`bind_audit_context` + `log_audit`，用 logging 输出 attributes |
| `context_and_config.py` | `apply_config`、显式字段覆盖 ContextVar、`audit_info/warn/error`、无 override 时不抛 |

## 怎么跑

在 `foundation` 目录下（保证 `openjiuwen_runtime` 在 `PYTHONPATH` 中）：

```bash
cd foundation
uv run python openjiuwen_runtime/foundation/audit/examples/basic_memory.py
uv run python openjiuwen_runtime/foundation/audit/examples/context_and_config.py
```

无需安装 `audit-otel` extra；示例不连 OTEL endpoint。

## 和主文档的关系

- 模块 [`../README.md`](../README.md)：API 与配置说明
- 本目录：复制粘贴后可跑通的最小脚本
