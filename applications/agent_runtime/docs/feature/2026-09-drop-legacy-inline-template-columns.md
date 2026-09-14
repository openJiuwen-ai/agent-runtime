# 删除 service_config_template legacy 内联容器列

- 日期:2026-09-14
- 涉及模块:manager / session_manager / manager_web / 测试 / 文档

## 背景与动机

容器表拆分（`2026-08-container-table-split.md`）后，现行契约是三段式：模板只持引用（`main_container_id` / `sidecar_container_ids` / `volumes`），容器规格走 `service_config_container`（Runtime）或 Manager 的 `data.config_sync.containers`。两侧 `service_config_template` 表仍保留拆表前的一批内联列（`agent_image`、`sidecars`、`agent_*_mounts` 等），读路径已基本不用，却造成 schema 噪音与「还能双写」的错觉。Manager sync 曾用内联列合成主容器，进一步固化死列。

## 方案

- **硬删** 24 个 legacy 内联列（DB TABLE_DEF + Manager API Create/Update/Out + 前端类型）。
- Manager 容器 SoT 不变：仍只写/读 `data.config_sync.containers`；删除 `_synthesize_main_container`，缺 stored containers → sync skip。
- Out 增加只读派生 `main_image`（按 `main_container_id` 从 containers 取 image），供列表展示。
- Runtime：`row_from_template_split` 去掉 `agent_image=""` 死值；**保留** `_LEGACY_INLINE_CONTAINER_KEYS` wire 黑名单（mixed → 400）。
- Runtime `Template.agent_image` 等 property 保留（来自 `main_container`，非 DB 列）。

## 删除列清单

`agent_image`, `container_name`, `container_port`, `port_name`, `sse_port`, `health_path`, `agent_env`, `image_pull_policy`, `run_as_user`, `run_as_group`, `readiness_initial_delay`, `readiness_period`, `nfs_server`, `nfs_path`, `nfs_mount_path`, `agent_cpu_request`, `agent_memory_request`, `agent_cpu_limit`, `agent_memory_limit`, `sidecars`, `agent_host_path_mounts`, `agent_configmap_mounts`, `agent_pvc_mounts`

## 实现

- Manager：`template_models` / `template_schemas` / `service_config_template` / `runtime_config_sync` / `safe_text`；前端 types、EditPage、Export、列表；demo/单测。
- Runtime：`config_store.SERVICE_CONFIG_TEMPLATE_TABLE_DEF` + `row_from_template_split`；`test_config_store.py`。

## 验证

- Manager：`test_runtime_config_sync`（含缺 containers / 缺 main_container_id skip）+ `test_template_routers`。
- Runtime：`tests/session_manager/test_config_store.py` 54 passed。

## 存量库升级（发版前手工 DROP；框架不 DROP）

**前置检查（Manager）**：每行须有 `main_container_id` 且 `data.config_sync.containers` 含该主容器完整 wire（含非空 `image`）；否则先 UI 重存或脚本回填，再发版。

**前置检查（Runtime）**：每行须有 `main_container_id` 且容器表可水合；无引用列的行本就不水合。

```sql
-- Manager DB 与 Runtime DB 各自执行一遍（列已不存在则跳过对应 DROP）
ALTER TABLE service_config_template
  DROP COLUMN IF EXISTS agent_image,
  DROP COLUMN IF EXISTS container_name,
  DROP COLUMN IF EXISTS container_port,
  DROP COLUMN IF EXISTS port_name,
  DROP COLUMN IF EXISTS sse_port,
  DROP COLUMN IF EXISTS health_path,
  DROP COLUMN IF EXISTS agent_env,
  DROP COLUMN IF EXISTS image_pull_policy,
  DROP COLUMN IF EXISTS run_as_user,
  DROP COLUMN IF EXISTS run_as_group,
  DROP COLUMN IF EXISTS readiness_initial_delay,
  DROP COLUMN IF EXISTS readiness_period,
  DROP COLUMN IF EXISTS nfs_server,
  DROP COLUMN IF EXISTS nfs_path,
  DROP COLUMN IF EXISTS nfs_mount_path,
  DROP COLUMN IF EXISTS agent_cpu_request,
  DROP COLUMN IF EXISTS agent_memory_request,
  DROP COLUMN IF EXISTS agent_cpu_limit,
  DROP COLUMN IF EXISTS agent_memory_limit,
  DROP COLUMN IF EXISTS sidecars,
  DROP COLUMN IF EXISTS agent_host_path_mounts,
  DROP COLUMN IF EXISTS agent_configmap_mounts,
  DROP COLUMN IF EXISTS agent_pvc_mounts;
```

> MySQL 8.0.29+ 支持 `DROP COLUMN IF EXISTS`；更早版本去掉 `IF EXISTS` 并确认列存在后再执行。PostgreSQL 可用同上语法。

发版顺序：**数据前置检查 → 发代码 → 手工 DROP**。

## 影响面

- Manager API breaking：Create/Update 不再接受上述字段；导入旧 inline JSON 由前端合成 `data.config_sync.containers`。
- wire 契约不变（仍三段式）；`_LEGACY_INLINE_CONTAINER_KEYS` 继续拒 mixed。

## 后续演进（2026-09-14）

Manager 容器 SoT 已从 `data.config_sync.containers` **迁到** `service_config_container` 表：

- create/update：从 body 抽出 wire → UPSERT 落表，并剥离 `data` 中的 containers
- get/list Out：再投影回 `data.config_sync.containers` 供编辑页
- sync：优先读表（存量可回退 JSON）
- 删模板：不级联删容器行

存量库补列：

```sql
ALTER TABLE service_config_container ADD COLUMN IF NOT EXISTS command json;
ALTER TABLE service_config_container ADD COLUMN IF NOT EXISTS args json;
```
