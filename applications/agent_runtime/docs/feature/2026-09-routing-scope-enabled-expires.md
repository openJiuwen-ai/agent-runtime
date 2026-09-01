# routing_scope 增加 enabled / expires_at，生效时过滤

- 日期:2026-09-01
- 里程碑 / commit:—
- 涉及模块:session_manager / 测试 / 文档

## 背景与动机

Manager 侧实例资源表已有 `enabled` / `expires_at`。Runtime `routing_scope`
此前缺少对等字段，禁用或过期只能靠上游不下发/再 sync 删除。需要：

1. DB / wire / 快照持久化这两列；
2. route 匹配与 eager 预热按生效态过滤（过期用墙钟，不必等下一次 sync）。

## 方案

- `routing_scope` 增列：`expires_at datetime NULL`、`enabled boolean NOT NULL DEFAULT true`。
- `RoutingScopeDef` 携带同名字段；`is_active(now)` 判定生效。
- `match_scope` / `has_wildcard_scope` / config_sync 预热：跳过未生效 scope；
  未生效仍落库并进快照（与模板 `enabled=False` 同款），预热推 `min_idle=0`。
- 存量库须手工 ALTER（框架 `init_table` 只 create_all 不补列）。

## 实现

- `session_manager/routing.py`：字段、校验、快照 roundtrip、匹配过滤。
- `session_manager/config_store.py`：表定义、读写、config_sync 推送分支。
- 文档：HLD §3.1、`docs/spec/session-manager.md`。
- 测试：`test_routing.py` / `test_config_store.py`。

## 验证

- `uv run pytest tests/session_manager/test_routing.py tests/session_manager/test_config_store.py`

## 影响面

- 存量库发版前 ALTER：
  `ALTER TABLE routing_scope ADD COLUMN expires_at DATETIME NULL;`
  `ALTER TABLE routing_scope ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT TRUE;`
  （PG 等方言按类型调整。）
- 旧快照无字段时反序列化默认 `enabled=true`、`expires_at=null`（兼容）。
