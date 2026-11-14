# Gateway 接口文档


## 1. 总览


| 项 | 说明 |
| --- | --- |
| 服务 | Gateway 企业版 **Config Receiver**（挂在统一 WEB 宿主） |
| 代码位置 | `jiuwenswarm/gateway/enterprise/config/manager_config/` |
| 默认端口 | `WEB_PORT`，缺省 **19000**（与用户面 WS/HTTP 同端口；原独立 `8775` 已废弃） |
| Base URL | 实例 `gateway_host`（无尾 `/`），如 `http://gateway.sales.svc:19000` |
| 路径前缀 | `/api`；业务写接口在 `/api/v1` |
| 响应信封 | `{ "code": 200, "message": "success", "data": ... }` |
| 调用方 | **仅** Manager 经 `gateway_request`（管理控制台 / 用户面不直连） |
| 鉴权 | Link mTLS / link-binding；管理面 path 不匹配则 HTTP **403** `LINK_BINDING_MISMATCH` |


**与 Manager REST 对照**


| 维度 | Manager REST | Gateway Config Receiver |
| --- | --- | --- |
| 调用方 | 管理控制台 / 运维脚本 | Manager `gateway_request` |
| 配置权威 | 是（读以 Manager DB 为准；写先推 Gateway 再落库） | 否（下发副本，写入企业库 GDB） |
| 身份 | JWT + 本地授权表 | 内网 / TLS / Link 绑定 |
| 模板 `template_id` | 创建时由服务端生成 UUID | Body **必带**已有 `template_id`（或策略的 `policy_id` / 规则的 `rule_id`） |
| 读接口 | 有列表 / 详情 | 模板与多数应用配置 **无** GET/列表；用量等少数只读接口除外 |


**同进程其它面（本文不展开）**


| 面 | 前缀 / 端口 | 说明 |
| --- | --- | --- |
| 用户 Web HTTP | `/api/v1/*`（含 auth、sessions、chat 等） | 响应信封为 `{request_id, ok, data, metadata}`；见用户面接口文档 |
| 文件 / 分享 / 轨迹 | `/file-api`、`/share-api`、`/api/trajectory` | 用户面能力 |
| A2A 入站 | 独立服务，默认 **19100**，`/a2a` | 非 Config Receiver |


配置下发与 Runtime 通知见 [配置下发及生效设计](../设计文档/配置下发及生效设计.md)。


---



## 2. 公共约定



### 2.1 鉴权

管理面 Config Receiver 路径由中间件 `link_binding_guard` 校验（`LinkMTLSConfig.from_env()`）：


| 结果 | HTTP | Body |
| --- | --- | --- |
| 通过 | 进入业务 | — |
| 绑定/证书不匹配 | **403** | `{ "ok": false, "error": { "code": "LINK_BINDING_MISMATCH", "message": "..." } }`（**非** `{code,message,data}` 信封） |


受保护前缀（`_MANAGER_PATH_PREFIXES`）包括：各类 `*-templates`、`a2a-access-policies`、`instance-agent-resources`、`instance-data-lifecycle`、`workspace-quota/`、`team-quota/`、`logging`、`task-memory`、`memory`、`cron-policy`、`user-preference-default`、`audit-log`、`log-masking-rules`。

- `GET /api/health`、`GET /api/v1/ready` **不在**上述前缀内，可供探活。
- 业务 Body **不再**做验签/解密（`deps.py`）；历史环境变量 `GATEWAY_CONFIG_VERIFY_*` / `GATEWAY_CONFIG_DEC_*` 见设计文档，勿当作当前 Handler 行为。


### 2.2 统一响应

```json
{
  "code": 200,
  "message": "success",
  "data": null
}
```


| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int | 业务码，成功为 `200` |
| `message` | string | 文案，成功多为 `success` |
| `data` | object \| array \| null | 业务结果；删除类多为 `null` |


| HTTP | 含义 |
| --- | --- |
| 200 | 成功 |
| 400 | 业务校验失败（`detail`） |
| 403 | Link 绑定失败（见上） |
| 404 | 资源不存在（如 PATCH/DELETE 目标未找到） |
| 500 | 少数内部失败（如默认语言 upsert） |
| 503 | 配额用量等依赖不可用 |


探活接口直接返回对象，**不**包信封。


### 2.3 调用形态

```text
{METHOD}  {gateway_host}/api/v1/...
Content-Type: application/json
# 经 Manager gateway_request + Link mTLS
```

- Manager 客户端：`manager_config_push/client.py` → `gateway_request(jiuwenclaw_id, method, path, business)`。
- 写接口 Body 为**业务字段本身**（无外层 sync 信封）；历史字段 `jiuwenclaw_id` 若传入会被忽略。
- **DELETE** 与无业务字段的写接口：Body 传 `{}`（`SyncEnvelopeOnlyBody`）。
- **PATCH**：仅更新客户端**显式传入**的字段（`exclude_unset`）；未传不改；对 NOT NULL 列勿传 `null`。


### 2.4 模板类通用约定

- **形态**：仅同步写——`POST` 创建、`PATCH /{id}` 更新、`DELETE /{id}` 删除；**无**列表/详情。
- **ID**：创建 Body **必须**带 Manager 已生成的 `template_id`（A2A 访问策略为 `policy_id`）。
- **路径参数**：`template_id` / `policy_id` / `rule_id` / `resource_id` 长度 1–100（规则 ID 上限 64）。
- **副作用**：多数写成功后 `trigger_runtime_config_update()`，通知 Runtime `config_refresh`；**例外**：`cron-policy`、`user-preference-default`、配额策略/用量、`instance-data-lifecycle` 不走该通知（或按各自服务约定）。


| 操作 | 方法 | 路径模式 |
| --- | --- | --- |
| 创建 / 全量 upsert | `POST` 或 `PUT` | `/api/v1/{collection}` |
| 更新 | `PATCH` | `/api/v1/{collection}/{id}` |
| 删除 | `DELETE` | `/api/v1/{collection}/{id}`（Body `{}`） |


### 2.5 下发流程（Manager → Gateway）

```text
Manager REST 写
  → 组装 business JSON（含已有 template_id 等）
  → gateway_request(jid, METHOD, "/api/v1/...", business)
       → Link mTLS 访问实例 gateway_host
       → 解析响应 data
  → 成功后再写 Manager DB
```


---



## 3. 系统



### 3.1 健康检查

- **接口名称**：健康检查
- **请求方法**：`GET`
- **请求路径**：`/api/health`
- **鉴权**：无（不在管理面 path 守卫内）
- **请求参数**：无
- **返回参数**：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 固定 `ok` |


- **返回示例**：

```json
{ "status": "ok" }
```

说明：Manager 实例探活使用本路径；**不**包 `{code,message,data}` 信封。



### 3.2 就绪检查

- **接口名称**：就绪检查
- **请求方法**：`GET`
- **请求路径**：`/api/v1/ready`
- **鉴权**：无
- **返回参数**：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 固定 `ready` |


- **返回示例**：

```json
{ "status": "ready" }
```


---



## 4. 模型模板（`/api/v1/model-templates`）

写成功后触发 Runtime 配置刷新。



### 4.1 创建模型模板

- **接口名称**：创建模型模板
- **请求方法**：`POST`
- **请求路径**：`/api/v1/model-templates`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_id` | string | 是 | Manager 已生成，1–100 |
| `template_name` | string | 是 | 1–128 |
| `api_base` | string | 是 | 1–512 |
| `api_key` | string | 是 | 1–4096 |
| `model_id` | string | 是 | 1–128 |
| `model_provider` | string | 是 | 1–64 |
| `description` | string | 否 | ≤512 |
| `model_type` | string[] | 否 | — |
| `model_tags` | string[] | 否 | — |
| `parameters` | object | 否 | 推理参数 |
| `timeout` | int | 否 | `>=1` |
| `retry_count` | int | 否 | `>=0` |
| `enable_streaming` | bool | 否 | — |
| `enable_function_calling` | bool | 否 | — |
| `verify_ssl` | bool | 否 | — |
| `enabled` | bool | 否 | — |
| `data` | object | 否 | 可含 `headers`（对象，键值均为字符串；最多 50 项） |


- **返回参数**：`data` 为创建结果
- **请求示例**：

```json
{
  "template_id": "a1000001-0000-4000-8000-0000000000m1",
  "template_name": "兜底-经济型",
  "description": "Fallback Agent 使用",
  "model_type": ["default"],
  "model_tags": ["chat"],
  "api_base": "https://api.openai.com/v1",
  "api_key": "sk-demo-global",
  "model_id": "gpt-4o-mini",
  "model_provider": "openai",
  "parameters": { "temperature": 0.7, "max_tokens": 4096 },
  "verify_ssl": true,
  "enabled": true,
  "data": {}
}
```

- **返回示例**：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "template_id": "a1000001-0000-4000-8000-0000000000m1",
    "template_name": "兜底-经济型",
    "enabled": true
  }
}
```



### 4.2 更新模型模板

- **接口名称**：更新模型模板
- **请求方法**：`PATCH`
- **请求路径**：`/api/v1/model-templates/{template_id}`
- **路径参数**：`template_id`
- **请求参数**（Body）：与创建相同业务字段，**均可选**（partial）；不可改 `template_id`
- **返回参数**：`data` 多为 `null`
- **请求示例**：

```json
{
  "template_name": "兜底-经济型-改",
  "enabled": false
}
```



### 4.3 删除模型模板

- **接口名称**：删除模型模板
- **请求方法**：`DELETE`
- **请求路径**：`/api/v1/model-templates/{template_id}`
- **请求参数**（Body）：`{}`
- **返回参数**：`data` 为 `null`
- **返回示例**：

```json
{
  "code": 200,
  "message": "success",
  "data": null
}
```


---



## 5. Embedding 模板（`/api/v1/embedding-templates`）



### 5.1 创建 Embedding 模板

- **请求方法**：`POST`
- **请求路径**：`/api/v1/embedding-templates`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_id` | string | 是 | 1–100 |
| `template_name` | string | 是 | 亦接受别名 `name` |
| `api_base` | string | 是 | — |
| `api_key` | string | 是 | — |
| `model_id` | string | 是 | — |
| `model_provider` | string | 是 | — |
| `description` | string | 否 | ≤512 |
| `embed_tags` | string[] | 否 | — |
| `parameters` | object | 否 | — |
| `client_config` | object | 否 | — |
| `enabled` | bool | 否 | — |
| `data` | object | 否 | — |


- **请求示例**：

```json
{
  "template_id": "e1000001-0000-4000-8000-0000000000e1",
  "template_name": "默认 Embedding",
  "api_base": "https://api.openai.com/v1",
  "api_key": "sk-embed",
  "model_id": "text-embedding-3-small",
  "model_provider": "openai",
  "enabled": true
}
```



### 5.2 更新 / 删除

- **更新**：`PATCH /api/v1/embedding-templates/{template_id}`，Body 为 partial 业务字段
- **删除**：`DELETE /api/v1/embedding-templates/{template_id}`，Body `{}`


---



## 6. 扩展配置模板（`/api/v1/extension-config-templates`）



### 6.1 创建扩展配置模板

- **请求方法**：`POST`
- **请求路径**：`/api/v1/extension-config-templates`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_id` | string | 是 | — |
| `template_name` | string | 是 | — |
| `component` | string | 是 | 1–32 |
| `hook_type` | string | 是 | 1–32 |
| `hook_config` | object | 是 | 见下表 |
| `description` | string | 否 | — |
| `custom_config` | object | 否 | — |
| `enabled` | bool | 否 | — |
| `data` | object | 否 | — |


`hook_config`：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `handler` | string | 是 | 处理器名 |
| `params` | object | 否 | — |
| `schedule` | string | 条件 | `hook_type=schedule` 时必填；合法 cron（5/6/7 段） |
| `data` | object | 否 | — |


- **请求示例**：

```json
{
  "template_id": "x1000001-0000-4000-8000-0000000000x1",
  "template_name": "定时清理",
  "component": "agent",
  "hook_type": "schedule",
  "hook_config": {
    "handler": "cleanup",
    "schedule": "0 */5 * * *"
  },
  "enabled": true
}
```



### 6.2 更新 / 删除

- **更新**：`PATCH /api/v1/extension-config-templates/{template_id}`
- **删除**：`DELETE /api/v1/extension-config-templates/{template_id}`，Body `{}`


---



## 7. 预置 Skill 模板（`/api/v1/skill-prebuilt-templates`）



### 7.1 创建预置 Skill 模板

- **请求方法**：`POST`
- **请求路径**：`/api/v1/skill-prebuilt-templates`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_id` | string | 是 | — |
| `template_name` | string | 是 | — |
| `skill_id` | string | 是 | 1–512 |
| `description` | string | 否 | — |
| `package_url` | string | 否 | 合法 http(s) URL，≤2048；空串视为未填 |
| `source_id` | string | 否 | ≤64 |
| `version_id` | string | 否 | ≤128 |
| `enabled` | bool | 否 | — |
| `data` | object | 否 | — |


- **请求示例**：

```json
{
  "template_id": "s1000001-0000-4000-8000-0000000000s1",
  "template_name": "代码审阅 Skill",
  "skill_id": "code-review",
  "package_url": "https://skillhub.example.com/packages/code-review.zip",
  "enabled": true
}
```



### 7.2 更新 / 删除

- **更新**：`PATCH /api/v1/skill-prebuilt-templates/{template_id}`
- **删除**：`DELETE /api/v1/skill-prebuilt-templates/{template_id}`，Body `{}`


---



## 8. 专家包模板（`/api/v1/expert-package-templates`）



### 8.1 创建专家包模板

- **请求方法**：`POST`
- **请求路径**：`/api/v1/expert-package-templates`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_id` | string | 是 | — |
| `template_name` | string | 是 | — |
| `packages` | array | 是 | 至少 1 项，见下表 |
| `description` | string | 否 | — |
| `enabled` | bool | 否 | — |


`packages[]`：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `kind` | string | 是 | `agent_template` / `agent_group` |
| `asset_id` | string | 是 | 1–128 |
| `version` | string | 否 | ≤64，默认 `""` |


- **请求示例**：

```json
{
  "template_id": "p1000001-0000-4000-8000-0000000000p1",
  "template_name": "销售专家包",
  "packages": [
    { "kind": "agent_template", "asset_id": "agent-sales", "version": "1.0.0" }
  ],
  "enabled": true
}
```



### 8.2 更新 / 删除

- **更新**：`PATCH /api/v1/expert-package-templates/{template_id}`；`packages` 可整表替换（至少 1 项）
- **删除**：`DELETE /api/v1/expert-package-templates/{template_id}`，Body `{}`


---



## 9. 安全护栏模板（`/api/v1/permissions-templates`）



### 9.1 创建安全护栏模板

- **请求方法**：`POST`
- **请求路径**：`/api/v1/permissions-templates`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_id` | string | 是 | — |
| `template_name` | string | 是 | — |
| `body` | object | 是 | 完整 `permissions` 段，结构对齐 `config.yaml::permissions` |
| `description` | string | 否 | — |
| `enabled` | bool | 否 | — |
| `data` | object | 否 | — |


- **请求示例**：

```json
{
  "template_id": "perm-0001",
  "template_name": "默认护栏",
  "body": {
    "default": "deny",
    "rules": []
  },
  "enabled": true
}
```



### 9.2 更新 / 删除

- **更新**：`PATCH /api/v1/permissions-templates/{template_id}`
- **删除**：`DELETE /api/v1/permissions-templates/{template_id}`，Body `{}`


---



## 10. MCP 模板（`/api/v1/mcp-templates`）

企业模板仅允许**远程** MCP；本地 stdio 不开放。



### 10.1 创建 MCP 模板

- **请求方法**：`POST`
- **请求路径**：`/api/v1/mcp-templates`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_id` | string | 是 | — |
| `template_name` | string | 是 | — |
| `mcp_entry` | object | 是 | 见下；条目级 `enabled` 会被丢弃（开关只认模板行） |
| `description` | string | 否 | — |
| `enabled` | bool | 否 | — |
| `data` | object | 否 | — |


`mcp_entry`：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | 是 | 非空 |
| `transport` | string | 是 | `sse` / `http` / `streamable-http` / `streamable_http`；`http` 与 `streamable_http` 存盘归一为 `streamable-http` |
| `url` | string | 是 | 远程 URL |


- **请求示例**：

```json
{
  "template_id": "mcp-0001",
  "template_name": "文档 MCP",
  "mcp_entry": {
    "name": "docs",
    "transport": "streamable-http",
    "url": "https://mcp.example.com/docs"
  },
  "enabled": true
}
```



### 10.2 更新 / 删除

- **更新**：`PATCH /api/v1/mcp-templates/{template_id}`
- **删除**：`DELETE /api/v1/mcp-templates/{template_id}`，Body `{}`


---



## 11. Agent 模板（`/api/v1/agent-templates`）



### 11.1 创建 Agent 模板

- **请求方法**：`POST`
- **请求路径**：`/api/v1/agent-templates`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_id` | string | 是 | — |
| `template_name` | string | 是 | — |
| `template_ref` | object | 否 | `dict[str, list[str]]`，缺省 `{}`；服务端会规范化 |
| `description` | string | 否 | — |
| `agent_tags` | string[] | 否 | — |
| `enabled` | bool | 否 | — |
| `data` | object | 否 | — |


- **请求示例**：

```json
{
  "template_id": "agent-0001",
  "template_name": "销售助手",
  "template_ref": {
    "model": ["a1000001-0000-4000-8000-0000000000m1"],
    "mcp": ["mcp-0001"]
  },
  "agent_tags": ["sales"],
  "enabled": true
}
```



### 11.2 更新 / 删除

- **更新**：`PATCH /api/v1/agent-templates/{template_id}`
- **删除**：`DELETE /api/v1/agent-templates/{template_id}`，Body `{}`


---



## 12. A2A 出站模板（`/api/v1/a2a-outbound-templates`）

`credential.operation=replace` 时会按 `data.network_policy` 做网络目标校验；失败 **400**（`A2A credential sync blocked by network access settings`）。



### 12.1 创建 A2A 出站模板

- **请求方法**：`POST`
- **请求路径**：`/api/v1/a2a-outbound-templates`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_id` | string | 是 | 1–100 |
| `template_name` | string | 是 | — |
| `source_url` | string | 是 | 合法 http(s) URL |
| `card_path` | string | 是 | 同源绝对路径（如 `/.well-known/agent-card.json`），禁止 `..` / query / fragment |
| `agent_card` | object | 是 | 须含非空 `name` |
| `card_fingerprint` | string | 是 | 1–128 |
| `card_revision` | int | 是 | `>=1` |
| `selected_interface` | object | 是 | 须含 `protocol_binding`、`protocol_version`、合法 `url` |
| `connect_timeout_seconds` | float | 是 | `>0` |
| `sync_wait_seconds` | float | 是 | `>0` |
| `enabled` | bool | 是 | — |
| `credential` | object | 是 | 见下；创建时 **禁止** `operation=keep` |
| `updated_at` | datetime | 是 | — |
| `description` | string | 否 | — |
| `a2a_tags` | string[] | 否 | — |
| `data` | object | 否 | 可含 `network_policy` |


`credential`：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `operation` | string | 是 | `keep` / `replace` / `clear` |
| `value` | string | 条件 | 仅 `replace` 时必填；禁止 `ENC:v1:` 前缀 |


- **请求示例**：

```json
{
  "template_id": "a2a-out-0001",
  "template_name": "外部客服 Agent",
  "source_url": "https://partner.example.com",
  "card_path": "/.well-known/agent-card.json",
  "agent_card": { "name": "partner-agent", "description": "demo" },
  "card_fingerprint": "fp-abc",
  "card_revision": 1,
  "selected_interface": {
    "protocol_binding": "JSONRPC",
    "protocol_version": "0.3",
    "url": "https://partner.example.com/a2a"
  },
  "connect_timeout_seconds": 10,
  "sync_wait_seconds": 30,
  "enabled": true,
  "credential": { "operation": "replace", "value": "token-plain" },
  "updated_at": "2026-10-29T08:00:00+00:00",
  "data": { "network_policy": {} }
}
```



### 12.2 更新 A2A 出站模板

- **请求方法**：`PATCH`
- **请求路径**：`/api/v1/a2a-outbound-templates/{template_id}`
- **请求参数**（Body）：业务字段均可选；`credential` 为 `null`/省略表示不改凭证；`operation=keep` 在更新时合法



### 12.3 删除 A2A 出站模板

- **请求方法**：`DELETE`
- **请求路径**：`/api/v1/a2a-outbound-templates/{template_id}`
- **请求参数**（Body）：`{}`


---



## 13. A2A 访问策略（`/api/v1/a2a-access-policies`）

路径参数名为 `{template_id}`（工厂统一），业务主键字段为 **`policy_id`**。



### 13.1 创建访问策略

- **请求方法**：`POST`
- **请求路径**：`/api/v1/a2a-access-policies`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `policy_id` | string | 是 | 1–100 |
| `policy_name` | string | 是 | — |
| `mode` | string | 是 | `allowlist` / `denylist` |
| `member_template_ids` | string[] | 否 | 默认 `[]`；服务端去重保序 |
| `enabled` | bool | 是 | — |
| `revision` | int | 是 | `>=1` |
| `updated_at` | datetime | 是 | — |
| `description` | string | 否 | — |
| `data` | object | 否 | — |


- **请求示例**：

```json
{
  "policy_id": "a2a-pol-0001",
  "policy_name": "销售出站白名单",
  "mode": "allowlist",
  "member_template_ids": ["a2a-out-0001"],
  "enabled": true,
  "revision": 1,
  "updated_at": "2026-10-29T08:00:00+00:00"
}
```



### 13.2 更新 / 删除

- **更新**：`PATCH /api/v1/a2a-access-policies/{template_id}`（路径 ID 即 `policy_id`）
- **删除**：`DELETE /api/v1/a2a-access-policies/{template_id}`，Body `{}`


---



## 14. 实例 Agent 资源（`/api/v1/instance-agent-resources`）

按 `resource_id` **upsert**；写成功后触发 Runtime 刷新。



### 14.1 Upsert 实例 Agent 资源

- **接口名称**：Upsert 实例 Agent 资源
- **请求方法**：`POST`
- **请求路径**：`/api/v1/instance-agent-resources`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `resource_id` | string | 是 | 1–100 |
| `ref_template_id` | string | 是 | 引用的 Agent 模板 ID |
| `resource_name` | string | 是 | 1–128 |
| `resource_desc` | string | 否 | ≤512 |
| `match_expr` | any | 否 | 选路表达式；须通过 Gateway `validate_match_expr` |
| `granted_by` | string | 否 | ≤64 |
| `enabled` | bool | 否 | 默认 `true` |
| `expires_at` | datetime | 否 | — |
| `data` | object | 否 | — |


说明：`created_at` / `updated_at` 由服务层写入，不在 Body 中。

- **请求示例**：

```json
{
  "resource_id": "iar-0001",
  "ref_template_id": "agent-0001",
  "resource_name": "销售 Bot",
  "match_expr": [],
  "enabled": true
}
```



### 14.2 删除实例 Agent 资源

- **请求方法**：`DELETE`
- **请求路径**：`/api/v1/instance-agent-resources/{resource_id}`
- **请求参数**（Body）：`{}`
- **返回参数**：`data` 为 `null`


---



## 15. 实例数据生命周期（`/api/v1/instance-data-lifecycle`）



### 15.1 Purge 本机实例数据

- **接口名称**：实例数据生命周期
- **请求方法**：`POST`
- **请求路径**：`/api/v1/instance-data-lifecycle`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `op` | string | 否 | 仅支持 `"purge"`（默认）；其它值 **400** |


- **行为**：清空本 Gateway 企业库中的实例级配置与相关表（不可逆；每网关独立 DB）。涵盖模板、实例资源、配额、日志脱敏、logging/task-memory/memory/audit/cron/user_preference 等；细节见 `InstanceDataLifecycleService`。
- **副作用**：**不**调用 `trigger_runtime_config_update`
- **请求示例**：

```json
{ "op": "purge" }
```

- **返回示例**：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "op": "purge",
    "purged": true
  }
}
```

说明：`data` 形态以服务返回为准。Manager 删除实例时经 `gateway_request` 调用本接口。


---



## 16. 应用配置



### 16.1 Logging（`/api/v1/logging`）

#### 写入/更新

- **请求方法**：`PUT`
- **请求路径**：`/api/v1/logging`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `level` | string | 否 | 默认 `INFO`，1–16 |
| `console_level` | string | 否 | — |
| `gateway` | string | 否 | — |
| `channel` | string | 否 | — |
| `agent_server` | string | 否 | — |
| `full` | string | 否 | — |


- **副作用**：触发 Runtime 刷新
- **请求示例**：

```json
{
  "level": "INFO",
  "console_level": "WARNING",
  "gateway": "INFO",
  "channel": "INFO",
  "agent_server": "DEBUG",
  "full": null
}
```

#### 删除

- **请求方法**：`DELETE`
- **请求路径**：`/api/v1/logging`
- **请求参数**（Body）：`{}`



### 16.2 Task Memory（`/api/v1/task-memory`）

#### 写入/更新

- **请求方法**：`PUT`
- **请求路径**：`/api/v1/task-memory`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | 否 | 默认 `false` |
| `llm_model` | string | 否 | ≤256 |
| `embedding_model` | string | 否 | ≤256 |
| `api_key` | string | 否 | ≤512 |
| `api_base` | string | 否 | ≤1024 |
| `retrieval_algo` | string | 否 | ≤64 |
| `summary_algo` | string | 否 | ≤64 |


- **副作用**：触发 Runtime 刷新

#### 删除

- **请求方法**：`DELETE` `/api/v1/task-memory`，Body `{}`



### 16.3 Memory（`/api/v1/memory`）

#### 写入/更新

- **请求方法**：`PUT`
- **请求路径**：`/api/v1/memory`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `body` | object | 是 | 对齐 `config.yaml::memory`（`mode` / `engine` / `forbidden_memory_definition` / `external` 等） |


- **副作用**：触发 Runtime 刷新
- **请求示例**：

```json
{
  "body": {
    "mode": "local",
    "engine": {}
  }
}
```

#### 删除

- **请求方法**：`DELETE` `/api/v1/memory`，Body `{}`



### 16.4 Cron Policy（`/api/v1/cron-policy`）

界面同时运行中的定时任务上限；**不**触发 Runtime `config_refresh`。

#### 写入/更新

- **请求方法**：`PUT`
- **请求路径**：`/api/v1/cron-policy`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `max_jobs_per_user` | int | 是 | `>=0`；`0` 表示不能再从界面启用 |


- **请求示例**：

```json
{ "max_jobs_per_user": 10 }
```

#### 删除

- **请求方法**：`DELETE` `/api/v1/cron-policy`，Body `{}`



### 16.5 用户偏好默认语言（`/api/v1/user-preference-default`）

写入 Gateway `user_preference` 的 `__default__` 行；**不**触发 Runtime 刷新。

- **请求方法**：`PUT`
- **请求路径**：`/api/v1/user-preference-default`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `preferred_language` | string | 是 | `zh` / `en` |
| `extras` | object \| null | 否 | 未传=不覆盖；显式 `null`=清空；对象=覆盖 |


- **请求示例**：

```json
{
  "preferred_language": "zh",
  "extras": {}
}
```

说明：无对应 DELETE；内部失败可能 **500**。



### 16.6 Audit Log（`/api/v1/audit-log`）

#### 写入/更新

- **请求方法**：`PUT`
- **请求路径**：`/api/v1/audit-log`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `body` | object | 是 | 完整可下发 audit-log payload |
| `schema_version` | string | 否 | ≤32 |
| `otel_enabled` | bool | 否 | — |
| `otel_endpoint` | string | 否 | ≤512 |
| `otel_protocol` | string | 否 | ≤16 |
| `data_center` | string | 否 | ≤32 |
| `system_code` | string | 否 | ≤64 |
| `node` | string | 否 | ≤128 |
| `ntp_servers` | array | 否 | — |
| `ntp_sync_interval` | string | 否 | ≤32 |
| `ntp_max_offset_ms` | int | 否 | — |
| `ntp_failover` | bool | 否 | — |
| `source` | string | 否 | 默认 `manager` |
| `revision` | int | 否 | 默认 `1` |


- **副作用**：触发 Runtime 刷新

#### 删除

- **请求方法**：`DELETE` `/api/v1/audit-log`，Body `{}`



### 16.7 日志脱敏规则（`/api/v1/log-masking-rules`）

#### 创建

- **请求方法**：`POST`
- **请求路径**：`/api/v1/log-masking-rules`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | string | 是 | 1–64 |
| `rule_name` | string | 是 | 1–128 |
| `pattern` | string | 是 | 正则，1–512 |
| `description` | string | 否 | ≤512 |
| `replacement` | string | 否 | ≤64；省略/null 时服务层默认 `******` |
| `priority` | int | 否 | 默认 `0` |
| `with_fingerprint` | bool | 否 | 默认 `false` |
| `source` | string | 否 | `builtin` / `custom`，默认 `custom` |
| `enabled` | bool | 否 | 默认 `true` |
| `data` | object | 否 | — |


- **副作用**：触发 Runtime 刷新
- **请求示例**：

```json
{
  "rule_id": "lmr-custom-phone",
  "rule_name": "手机号脱敏",
  "description": "匹配 11 位手机号",
  "pattern": "1\\d{10}",
  "replacement": "******",
  "priority": 10,
  "source": "custom",
  "enabled": true
}
```

#### 更新

- **请求方法**：`PATCH`
- **请求路径**：`/api/v1/log-masking-rules/{rule_id}`
- **请求参数**（Body）：业务字段均可选（partial）

#### 删除

- **请求方法**：`DELETE`
- **请求路径**：`/api/v1/log-masking-rules/{rule_id}`
- **请求参数**（Body）：`{}`


---



## 17. 工作区配额（`/api/v1/workspace-quota`）

一套 Gateway 对应一个集群；策略表**不含** `cluster_id`。详细选路与阈值语义见 [用户空间配额管理设计](../设计文档/用户空间配额管理设计.md)。



### 17.1 Upsert 工作区配额策略

- **接口名称**：Upsert 工作区配额策略
- **调用方**：Manager `gateway_request`
- **请求方法**：`PUT`
- **请求路径**：`/api/v1/workspace-quota/policies`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `policy_id` | string | 是 | 1–64 |
| `policy_name` | string | 是 | 1–128，去空白后非空 |
| `priority` | int | 是 | 越小越优先 |
| `limit_bytes` | int | 是 | `>=-1`；`-1` 无限制，`0` 零配额 |
| `enabled` | bool | 是 | — |
| `policy_desc` | string | 否 | ≤512 |
| `match_expr` | any | 否 | 选路；`[]` 为全匹配 |
| `soft_percent` | int | 否 | 0–100，默认 `80`；须 **小于** `hard_percent` |
| `hard_percent` | int | 否 | 0–100，默认 `100` |
| `source` | string | 否 | `manual` / `approval`，默认 `manual` |
| `source_order_num` | string | 否 | 仅 `source=approval` 时保留 |


- **返回参数**：`data` 为 `null`
- **副作用**：不触发 Runtime `config_refresh`
- **请求示例**：

```json
{
  "policy_id": "qp_01",
  "policy_name": "销售组个人额度",
  "policy_desc": "销售组织下个人写作者的配额",
  "match_expr": [],
  "priority": 10,
  "limit_bytes": 21474836480,
  "soft_percent": 80,
  "hard_percent": 100,
  "source": "manual",
  "enabled": true
}
```



### 17.2 删除工作区配额策略

- **请求方法**：`DELETE`
- **请求路径**：`/api/v1/workspace-quota/policies/{policy_id}`
- **请求参数**（Body）：`{}`
- **返回参数**：`data` 为 `null`



### 17.3 查询本集群用量缓存

- **接口名称**：查询本集群用量
- **请求方法**：`GET`
- **请求路径**：`/api/v1/workspace-quota/usage`
- **鉴权**：Link mTLS（path 在守卫前缀内）
- **说明**：只读 Gateway `workspace_quota_usage` 缓存，**不**访问 Agent；`limit_bytes` / `source_policy_id` / `status` 按本集群策略现算，不写回用量表
- **请求参数**（Query）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | string | 否 | 过滤用户 |
| `group_id` | string | 否 | 有则过滤；无组织行可传空串 |
| `bot_id` | string | 否 | 过滤 Bot |
| `limit` | int | 否 | 默认 `20`，1–100；按 `used_bytes` 降序 |


- **返回参数**（`data.items[]`）：`user_id`、`group_id`、`bot_id`、`used_bytes`、`limit_bytes`、`source_policy_id`、`status`、`reported_at`
- **请求示例**：`GET /api/v1/workspace-quota/usage?limit=20`
- **返回示例**：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [
      {
        "user_id": "u_123",
        "group_id": "g_sales",
        "bot_id": "bot_writer",
        "used_bytes": 5368709120,
        "limit_bytes": 21474836480,
        "source_policy_id": "qp_01",
        "status": "ok",
        "reported_at": "2026-03-17T10:05:00Z"
      }
    ]
  }
}
```


---



## 18. 团队配额（`/api/v1/team-quota`）



### 18.1 Upsert 团队配额策略

- **请求方法**：`PUT`
- **请求路径**：`/api/v1/team-quota/policies`
- **请求参数**（Body）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `policy_id` | string | 是 | 1–64 |
| `policy_name` | string | 否 | 默认 `team-quota` |
| `policy_desc` | string | 否 | — |
| `match_expr` | any | 否 | — |
| `priority` | int | 否 | 默认 `0` |
| `max_teams_per_user` | int | 否 | 1–1000，默认 `5` |
| `max_teammates_per_team` | int | 否 | 1–1000，默认 `5` |
| `max_teams_per_org` | int | 否 | 0–1000，默认 `0`（0=不限制组织团队总数） |
| `source` | string | 否 | 默认 `manual` |
| `enabled` | bool | 否 | 默认 `true` |


- **返回参数**：`data` 为策略行
- **请求示例**：

```json
{
  "policy_id": "tq_01",
  "policy_name": "默认团队配额",
  "max_teams_per_user": 5,
  "max_teammates_per_team": 10,
  "max_teams_per_org": 0,
  "enabled": true
}
```



### 18.2 删除团队配额策略

- **请求方法**：`DELETE`
- **请求路径**：`/api/v1/team-quota/policies/{policy_id}`
- **请求参数**（Body）：`{}`



### 18.3 查询团队用量

- **请求方法**：`GET`
- **请求路径**：`/api/v1/team-quota/usage`
- **请求参数**：无
- **返回参数**：`data` 为本集群团队占用（含按组织汇总的 `orgs`）；依赖不可用时 **503**


---



## 19. 接口速查


| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 探活 |
| `GET` | `/api/v1/ready` | 就绪 |
| `POST` / `PATCH` / `DELETE` | `/api/v1/model-templates[/{id}]` | 模型模板 |
| `POST` / `PATCH` / `DELETE` | `/api/v1/embedding-templates[/{id}]` | Embedding |
| `POST` / `PATCH` / `DELETE` | `/api/v1/extension-config-templates[/{id}]` | 扩展配置 |
| `POST` / `PATCH` / `DELETE` | `/api/v1/skill-prebuilt-templates[/{id}]` | 预置 Skill |
| `POST` / `PATCH` / `DELETE` | `/api/v1/expert-package-templates[/{id}]` | 专家包 |
| `POST` / `PATCH` / `DELETE` | `/api/v1/permissions-templates[/{id}]` | 安全护栏 |
| `POST` / `PATCH` / `DELETE` | `/api/v1/mcp-templates[/{id}]` | MCP |
| `POST` / `PATCH` / `DELETE` | `/api/v1/agent-templates[/{id}]` | Agent |
| `POST` / `PATCH` / `DELETE` | `/api/v1/a2a-outbound-templates[/{id}]` | A2A 出站 |
| `POST` / `PATCH` / `DELETE` | `/api/v1/a2a-access-policies[/{id}]` | A2A 访问策略 |
| `POST` / `DELETE` | `/api/v1/instance-agent-resources[/{id}]` | 实例 Agent 资源 |
| `POST` | `/api/v1/instance-data-lifecycle` | Purge |
| `PUT` / `DELETE` | `/api/v1/logging` | 日志级别 |
| `PUT` / `DELETE` | `/api/v1/task-memory` | Task Memory |
| `PUT` / `DELETE` | `/api/v1/memory` | Memory |
| `PUT` / `DELETE` | `/api/v1/cron-policy` | Cron 上限 |
| `PUT` | `/api/v1/user-preference-default` | 默认语言 |
| `PUT` / `DELETE` | `/api/v1/audit-log` | 审计日志 |
| `POST` / `PATCH` / `DELETE` | `/api/v1/log-masking-rules[/{id}]` | 日志脱敏 |
| `PUT` / `DELETE` | `/api/v1/workspace-quota/policies[/{id}]` | 工作区配额策略 |
| `GET` | `/api/v1/workspace-quota/usage` | 工作区用量缓存 |
| `PUT` / `DELETE` | `/api/v1/team-quota/policies[/{id}]` | 团队配额策略 |
| `GET` | `/api/v1/team-quota/usage` | 团队用量 |


---



## 20. 相关文档

- [管理面接口文档](./管理面接口文档.md) — Manager REST；写路径经 `gateway_request` 调用本文接口
- [配置下发及生效设计](../设计文档/配置下发及生效设计.md) — 统一 `WEB_PORT`、GDB、Runtime notify
- [用户空间配额管理设计](../设计文档/用户空间配额管理设计.md) — 配额策略与用量字段细节
