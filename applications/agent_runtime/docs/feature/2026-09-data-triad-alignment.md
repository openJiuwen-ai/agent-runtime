# data / created_at / updated_at 三元组对齐

- 日期:2026-09-15
- 涉及模块:manager / session_manager / manager_web

## 背景与动机

对照 `instance_service_resource` 写入的 `data` / `created_at` / `updated_at` 三元组，发现若干成对表或缺列、或 sync 投影丢字段：

- `service_config_container`（Manager + Runtime）有时间戳、无 `data`
- `routing_scope`（Runtime）有时间戳、无 `data`；Manager 对偶 `instance_service_resource.data` 无法下传
- `link_binding_state`（Runtime / Gateway）有时间戳、无 `data`；对偶 Manager `instance_link_binding` 有 `data`
- Manager `a2a_access_policy_template` 无 `data`，Gateway 同表有 `data`（跨端不对称）
- `service_template_wire` 不推 `service_config_template.data`（两边表都有列）
- Gateway agent-resource payload 只有 `data`，缺 `created_at` / `updated_at`
- 实例资源 delete+create 更新时会重置 `created_at`

## 方案

- 两侧 `service_config_container` 补 `data` JSON 可空列；wire/行互转透传
- Runtime `routing_scope` 补 `data`；`RoutingScopeDef` / parse / 快照 / 落库透传
- Runtime + Gateway `link_binding_state` 补 `data`；首次写入 `None`，更新不覆盖已有扩展字段
- Manager `a2a_access_policy_template` 补 `data`（对齐 Gateway）；Create/Update/Out 透传
- Manager `build_runtime_config` 把资源 `data` 写入 scope；`service_template_wire` 带上模板 `data`
- Gateway agent-resource / 模板下发**不带** `created_at`/`updated_at`（与 `_PUSH_DROP_KEYS` 一致）；由 Gateway 落库自刷
- 实例 Agent/服务资源更新保留原 `created_at`，只刷 `updated_at`

## 存量库升级（发版前手工 ALTER；框架不补列）

```sql
-- Manager DB
ALTER TABLE service_config_container ADD COLUMN IF NOT EXISTS data json;
ALTER TABLE a2a_access_policy_template ADD COLUMN IF NOT EXISTS data json;
ALTER TABLE manager_identity ADD COLUMN IF NOT EXISTS data json;
ALTER TABLE instance_enc_pubkey ADD COLUMN IF NOT EXISTS data json;
ALTER TABLE instance_enc_pubkey ADD COLUMN IF NOT EXISTS created_at timestamp NOT NULL DEFAULT '1970-01-01 00:00:00';
UPDATE instance_enc_pubkey SET created_at = bound_at WHERE created_at = '1970-01-01 00:00:00';

-- Runtime DB
ALTER TABLE service_config_container ADD COLUMN IF NOT EXISTS data json;
ALTER TABLE routing_scope ADD COLUMN IF NOT EXISTS data json;
ALTER TABLE link_binding_state ADD COLUMN IF NOT EXISTS data json;

-- Gateway DB
ALTER TABLE link_binding_state ADD COLUMN IF NOT EXISTS data json;
ALTER TABLE gateway_enc_keypair ADD COLUMN IF NOT EXISTS data json;
ALTER TABLE gateway_sign_keypair ADD COLUMN IF NOT EXISTS data json;
ALTER TABLE manager_sign_pubkey ADD COLUMN IF NOT EXISTS data json;
ALTER TABLE manager_sign_pubkey ADD COLUMN IF NOT EXISTS created_at timestamp NOT NULL DEFAULT '1970-01-01 00:00:00';
UPDATE manager_sign_pubkey SET created_at = bound_at WHERE created_at = '1970-01-01 00:00:00';
```

> MySQL 更早版本去掉 `IF EXISTS` 并确认列不存在后再执行。
