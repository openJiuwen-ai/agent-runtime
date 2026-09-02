# 模板表策略四列改名:DB 列名统一为 wire 术语

- 日期:2026-09-02
- 涉及模块:session_manager / 测试 / 文档

## 背景与动机

`service_config_template` 表的策略四列自首提交起沿用 manager/EE 侧词汇(`min_idle_services`/`service_concurrency`/`service_ttl`/`session_concurrency`),与 wire 契约术语(`min_idle_pods`/`pod_concurrency`/`pod_ttl`/`scope_concurrency`)不一致——读 DB 排查问题时需靠 `_COLUMN_OF` 映射做心理翻译,且 `session_concurrency` 在 runtime 语义里实为 **scope** 并发,最易误读。wire 契约与 manager 侧零改动(manager 发出的 wire 本就是 runtime 术语,manager 自己的库表不受影响)。

## 方案

- 四列 DB 列名改为与 wire 同名:`min_idle_services→min_idle_pods`、`service_concurrency→pod_concurrency`、`service_ttl→pod_ttl`、`session_concurrency→scope_concurrency`(类型/默认值不变)。
- `_COLUMN_OF` 四条映射退化为 identity——**保留 identity 条目而非删除**:消费方(水合 `getattr(row, column)` / 落库 `row[_COLUMN_OF[field_name]]`)为直接索引,删条目会 KeyError;identity + 注释保留映射表完整性。
- 被否掉的备选:同步改 manager 侧表列名——manager 写自己的库(`manager_wmq`),与 runtime 通过 wire 通信,两侧表结构互不依赖,不值得为词汇统一让 manager 存量库也迁移。

## 实现

- `src/agent_runtime/session_manager/config_store.py`:TABLE_DEF 四列改名(注释记录曾用名与存量 RENAME 义务);`_COLUMN_OF` 四条 identity 化。
- `src/agent_runtime/session_manager/models.py`:模块 docstring 的概念↔DB 映射说明删除。
- `tests/session_manager/test_config_store.py`:ghost 容器防御用例直插行的旧列名更新。
- wire 契约、Redis 键、`service_config_container`/`routing_scope` 表、模板表 legacy 内联列(`agent_*`/`sidecars` 等)均不动。

## 验证

- 单测:`uv run pytest tests/session_manager/test_config_store.py` 50 passed;全量见提交时 CI。
- 存量库升级(MySQL 8+/PG,发版前执行):
  ```sql
  ALTER TABLE service_config_template
    RENAME COLUMN min_idle_services TO min_idle_pods,
    RENAME COLUMN service_concurrency TO pod_concurrency,
    RENAME COLUMN service_ttl TO pod_ttl,
    RENAME COLUMN session_concurrency TO scope_concurrency;
  ```

## 影响面

- 同步文档:`docs/spec/session-manager.md`(DB 表段落 + RENAME 义务)、`docs/design/session-manager-design.md` §关键列、`docs/design/resource-manager-design.md` §术语、`CLAUDE.md` 环境节。
- 兼容性:RENAME 前发新版会在水合/落库路径按新列名取值而取不到(旧行旧行为未知列默认兜底)——**必须先 ALTER 后发版**,与容器表三列义务同款顺序。
- HLD 无旧列名引用,未改。
