# Runtime 接口文档


## 1. 总览


| 项 | 说明 |
| --- | --- |
| 服务 | `agent-runtime`（K8s 名多为 `jiuwenswarm-agent-runtime`） |
| 默认端口 | **8091**（环境变量 `OPENJIUWEN_SERVICE_PORT`） |
| Base URL | 集群内一般为 `http://jiuwenswarm-agent-runtime:8091` |
| 业务路径前缀 | `/api/session`（全部为 **POST**） |
| 其它路径 | `GET /healthz`；`GET /visualization/*`（诊断只读） |
| 调用方 | **Gateway**（`route` / `touch` / `rebind`）、**Manager**（`config_sync`；routing 场景下还有 `register_pods`）；`config_refresh` / `cleanup` 多为运维场景 |
| 鉴权 | 默认不校验用户 JWT，依赖集群内网访问；可选开启 Link mTLS（见下文） |


**与其它服务对照**


| 维度 | Runtime（本文） | Manager | Gateway |
| --- | --- | --- | --- |
| 端口（部署） | **8091** | **8765** | 用户面入口（如 `19000`） |
| 路径 | `/api/session/*`、`/healthz`、`/visualization/*` | `/api`、`/api/v1/...` | 用户聊天 / 会话等 |
| 响应形态 | Envelope（`ok` / `rawdata` / `error_code`）或裸 JSON | `{code,message,data}` | 另见 Gateway 文档 |
| 角色 | 会话路由、配置落地、AgentServer 资源池 | 管理面权威配置 | 用户请求入口，再调 Runtime |


本服务不提供用户对话通道，也不提供 AgentServer 自身的业务接口。


---



## 2. 公共约定



### 2.1 鉴权

- 默认：业务接口与 `/healthz`、`/visualization/*` 均不校验 Bearer JWT。
- 可选：环境变量 `JIUWENSWARM_LINK_MTLS_MODE` 设为 `enforce` 时，请求需通过 Link 绑定校验；失败返回 **HTTP 403**：

```json
{
  "ok": false,
  "error_code": "LINK_BINDING_MISMATCH",
  "error_message": "..."
}
```

（该 403 响应不是完整 Envelope。）


### 2.2 请求 Envelope（`/api/session/*`）

业务接口统一 **POST**，Body 为 JSON：

```json
{
  "type": "<msg_type>",
  "metadata": {
    "request_id": "req-...",
    "user_id": null,
    "session_id": null,
    "bot_id": null,
    "extra": {}
  },
  "rawdata": {},
  "version": "1"
}
```


| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `type` | string | 与路径段一致，如 `route` → `POST /api/session/route` |
| `metadata.request_id` | string | 必填；`route` 用作幂等键（约 60 秒内同 id 重放返回相同结果） |
| `metadata.user_id` / `session_id` / `bot_id` | string \| null | 按接口要求填写 |
| `metadata.extra` | object | 扩展字段；`route` 使用 `extra.group_id` |
| `rawdata` | object | 业务载荷；无载荷时传 `{}` |
| `version` | string | 缺省 `"1"` |


### 2.3 响应 Envelope

成功时 HTTP **200**：

```json
{
  "type": "route",
  "metadata": { "...": "..." },
  "rawdata": {},
  "ok": true,
  "error_code": null,
  "error_message": null,
  "version": "1"
}
```

失败时 `ok=false`，并带 `error_code` / `error_message`；部分错误可带 `retry_after`（秒，建议重试间隔）。


| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ok` | bool | 是否成功 |
| `rawdata` | object | 成功时的业务结果 |
| `error_code` | string \| null | 失败业务码 |
| `error_message` | string \| null | 失败说明 |
| `retry_after` | int \| 省略 | 建议重试间隔（秒） |


### 2.4 业务错误码 → HTTP


| error_code | HTTP | 是否可重试 | 说明 |
| --- | --- | --- | --- |
| `VALIDATION` | 400 | 否 | 参数缺失或非法 |
| `CONFIG_SYNC_BUSY` | 409 | 是 | 配置正在更新中，或上一轮刷新尚未完成，请稍后重试 |
| `SCOPE_FULL` | 503 | 是 | 当前容量已满 |
| `NO_POD_AVAILABLE` | 503 | 是 | 暂时没有可用的 AgentServer |
| `CONFIG_NOT_FOUND` | 503 | 否 | 没有匹配的路由配置（例如尚未下发 scope） |
| `STATE_UNAVAILABLE` | 503 | 是 | 后端状态暂不可用 |


### 2.5 调用形态

```text
POST  {base}/api/session/{msg_type}
Content-Type: application/json

GET   {base}/healthz
GET   {base}/visualization/...
```


---



## 3. 系统



### 3.1 健康检查

- **接口名称**：健康检查
- **请求方法**：`GET`
- **请求路径**：`/healthz`
- **鉴权**：无
- **请求参数**：无
- **返回参数**（就绪，HTTP 200）：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `ok` | bool | `true` |
| `instance_id` | string | 本进程实例标识 |
| `service_type` | string | 固定 `runtime` |
| `namespace` | string | 所在命名空间 |
| `pod_name` | array | 当前 Runtime Pod 名（有则一项，无则为 `[]`） |
| `hostname` | string \| null | 主机名 |


- **未就绪**：HTTP **503**，`{"ok": false}`
- **返回示例**：

```json
{
  "ok": true,
  "instance_id": "jiuwenswarm-agent-runtime-0:a1b2c3d4",
  "service_type": "runtime",
  "namespace": "zxy",
  "pod_name": ["jiuwenswarm-agent-runtime-0"],
  "hostname": "jiuwenswarm-agent-runtime-0"
}
```

说明：本接口直接返回对象，不使用 Envelope，也不包 `{code,message,data}`。


---



## 4. 会话与配置（`/api/session/*`）

以下接口均为 **POST**，Body 为 Envelope；成功结果在响应的 `rawdata` 中。所有接口的 `metadata.request_id` 均必填。



### 4.1 路由占位 `route`

- **接口名称**：为会话分配 AgentServer
- **请求方法**：`POST`
- **请求路径**：`/api/session/route`
- **请求参数**：


| 位置 | 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| metadata | `request_id` | string | 是 | 幂等键；约 60 秒内同 id 重放返回相同结果 |
| metadata | `session_id` | string | 是 | 会话 ID（不可含 `{` / `}`） |
| metadata | `user_id` | string | 是 | 用户 ID |
| metadata | `bot_id` | string | 是 | Bot / Agent 标识 |
| metadata.extra | `group_id` | string | 是 | 组织/组，不能为空串 |
| rawdata | — | object | — | 通常 `{}` |


- **返回参数**（`rawdata`）：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `pod_sse_url` | string | 绑定到的 AgentServer 访问地址 |
| `pod_id` | string | Pod 标识 |


- **常见错误**：缺参 → `400 VALIDATION`；无匹配配置 → `503 CONFIG_NOT_FOUND`；容量不足 → `503 SCOPE_FULL` / `NO_POD_AVAILABLE`。
- **请求示例**：

```json
{
  "type": "route",
  "metadata": {
    "request_id": "req-001",
    "session_id": "sess-001",
    "user_id": "u-001",
    "bot_id": "bot-001",
    "extra": { "group_id": "org-001" }
  },
  "rawdata": {},
  "version": "1"
}
```

- **返回示例**：

```json
{
  "type": "route",
  "ok": true,
  "rawdata": {
    "pod_sse_url": "http://agentserver-xxxx:8080",
    "pod_id": "agentserver-xxxx"
  },
  "version": "1"
}
```



### 4.2 保活 `touch`

- **接口名称**：会话保活
- **请求方法**：`POST`
- **请求路径**：`/api/session/touch`
- **请求参数**：


| 位置 | 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| metadata | `session_id` | string | 是 | 要保活的会话 |


- **返回参数**（`rawdata`）：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `touched` | bool | `true` 保活成功；`false` 会话不存在或已过期（可回退重新 `route`） |


- **返回示例**：

```json
{ "ok": true, "rawdata": { "touched": true } }
```



### 4.3 改绑 `rebind`

- **接口名称**：把临时会话 key 改绑为真实会话 id（或释放占位）
- **请求方法**：`POST`
- **请求路径**：`/api/session/rebind`
- **说明**：Gateway 在会话创建拿到真实 id、交还客户端之前调用；`to` 为空表示创建失败时释放占位。同一请求重复调用是安全的。
- **请求参数**：


| 位置 | 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| metadata | `session_id` | string | 是 | 临时 key |
| rawdata | `to` | string | 否 | 真实会话 id；空或缺省表示释放占位 |


- **返回参数**（`rawdata`）：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `action` | string | 结果动作，如 `rebound`（已改绑）、`noop`（无需操作）、`evicted`（已释放）等 |
| `scope_id` | string | 相关 scope |
| `pod_id` | string | 相关 Pod |
| `remaining` | string | 附加计数信息 |


- **请求示例**：

```json
{
  "type": "rebind",
  "metadata": {
    "request_id": "req-rb-1",
    "session_id": "temp-xxxx"
  },
  "rawdata": { "to": "real-sess-001" },
  "version": "1"
}
```



### 4.4 配置全量下发 `config_sync`

- **接口名称**：配置全量下发
- **请求方法**：`POST`
- **请求路径**：`/api/session/config_sync`
- **典型调用方**：Manager
- **请求参数**（`rawdata`）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `containers` | array | 是 | 容器规格列表，可为 `[]` |
| `templates` | array | 是 | 运行时模板列表，可为 `[]`；元素通常含 `template_id`、`main_container_id` 等 |
| `scopes` | array | 是 | 路由范围列表，可为 `[]`；元素通常含 `scope_id`、`template_id` 等 |


说明：三个键都必须存在且为数组，否则 `400 VALIDATION`。`scopes` 为空时下发可成功，但之后 `route` 可能因无匹配配置返回 `503 CONFIG_NOT_FOUND`。元素级完整字段以实际下发为准，本文不展开。

- **返回参数**（`rawdata`）：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `ok` | bool | 成功为 `true` |
| `templates_synced` / `templates_deleted` | int | 本批同步/删除的模板数 |
| `containers_synced` / `containers_deleted` | int | 本批同步/删除的容器数 |
| `scopes_synced` / `scopes_deleted` | int | 本批同步/删除的 scope 数 |
| `affected_scopes` | array | 受影响的 scope |
| `routing_sunset` | array | 进入回收流程的 scope_id 列表 |
| `wildcard_present` | bool | 是否存在通配兜底 scope |


- **常见错误**：载荷非法 → `400 VALIDATION`；配置更新冲突 → `409 CONFIG_SYNC_BUSY`。
- **请求示例**：

```json
{
  "type": "config_sync",
  "metadata": { "request_id": "req-sync-1" },
  "rawdata": {
    "containers": [
      {
        "container_id": "c-main",
        "name": "agent",
        "image": "agentserver:latest",
        "ports": [{ "name": "sse", "containerPort": 8080 }]
      }
    ],
    "templates": [
      {
        "template_id": "tpl-1",
        "main_container_id": "c-main",
        "namespace": "default",
        "scope_concurrency": 3,
        "pod_concurrency": 2,
        "session_ttl": 60,
        "pod_ttl": 300,
        "min_idle_pods": 0
      }
    ],
    "scopes": [
      {
        "scope_id": "scope-1",
        "index": 0,
        "template_id": "tpl-1",
        "routing_rules": ""
      }
    ]
  },
  "version": "1"
}
```



### 4.5 强制刷新 `config_refresh`

- **接口名称**：按当前已落库配置强制刷新 AgentServer
- **请求方法**：`POST`
- **请求路径**：`/api/session/config_refresh`
- **请求参数**：`rawdata` 必须为 `{}`；非空 → `400 VALIDATION`（要改配置请走 `config_sync`）。
- **返回参数**（`rawdata`）：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `ok` | bool | `true` |
| `scopes_refreshed` | int | 刷新的 scope 数 |
| `pods_sunset` | int | 进入回收流程的 Pod 数 |
| `generations` | object | 各 scope 的刷新代数 |


- **说明**：
  - 已有会话一般可继续跑完；新会话会走刷新后的资源。
  - 若上一轮刷新尚未完成，返回 `409 CONFIG_SYNC_BUSY`，可稍后重试。
  - **成功后不要自动再调用**（每次成功都会再触发一轮刷新）。
  - Manager 日常配置下发请用 `config_sync`。



### 4.6 运维清理 `cleanup`

- **接口名称**：批量删除 AgentServer Pod
- **请求方法**：`POST`
- **请求路径**：`/api/session/cleanup`
- **请求参数**（`rawdata`）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `namespace` | string | 否 | 命名空间 |
| `label_selector` | string | 否 | 标签选择器 |


- **返回参数**（`rawdata`）：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `cleaned` | int | 清理数量；无匹配时可为 `0` |




### 4.7 注册外部 Pod `register_pods`（仅 routing 模式）

- **接口名称**：注册外部预建 Pod
- **请求方法**：`POST`
- **请求路径**：`/api/session/register_pods`
- **前置条件**：部署模式为 routing；否则 `400 VALIDATION`。
- **请求参数**（`rawdata`）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `scope_id` | string | 是 | 目标 scope |
| `pods` | array | 是 | 非空；每项须含 `name`、`host` |


- **返回参数**（`rawdata`）：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `scope_id` | string | 回显 |
| `registered` | array | 新注册成功的项（含 `pod`、`pod_sse_url`） |
| `already_registered` | array | 已在池中的 pod 名 |
| `pending` | array | 暂未就绪的项（含 `pod`、`reason`） |




### 4.8 注销外部 Pod `deregister_pods`（仅 routing 模式）

- **接口名称**：注销外部 Pod（会驱逐相关会话，但不删除集群里的 Pod 对象）
- **请求方法**：`POST`
- **请求路径**：`/api/session/deregister_pods`
- **前置条件**：同 `register_pods`。
- **请求参数**（`rawdata`）：


| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `scope_id` | string | 是 | 目标 scope |
| `pods` | array | 是 | 非空的 pod 名列表 |


- **返回参数**（`rawdata`）：


| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `deregistered` | array | 已注销的 pod 名 |
| `evicted_sessions` | int | 被驱逐的会话数 |


---



## 5. 诊断可视化（`GET /visualization/*`）

只读诊断接口，返回普通 JSON（不是 Envelope）。默认不校验 JWT；敏感字段会脱敏。多副本时响应里的 `instance_id` 表示**当前应答的这一台**。

成功时常见字段：`ok`、`instance_id`、`generated_at`。未就绪多为 `503`；缺参 `400`；资源不存在 `404`。


| 方法 | 路径 | 必填 Query | 说明 |
| --- | --- | --- | --- |
| GET | `/visualization/overview` | 无 | 实例总览 |
| GET | `/visualization/session` | `session_id` | 单会话状态 |
| GET | `/visualization/scope` | `scope_id` | 单 scope 状态；可选 `limit` |
| GET | `/visualization/scopes` | 无 | 全部 scope 摘要；可选 `limit` |
| GET | `/visualization/config` | 无 | 当前配置摘要 |
| GET | `/visualization/stats` | 无 | 请求统计 |
| GET | `/visualization/recent_errors` | 无 | 最近错误；可选 `limit` |
| GET | `/visualization/history` | `scope_id` | 趋势采样；可选 `window_sec`、`limit` |
| GET | `/visualization/evaluation` | 无 | 评估报告；可选 `limit` |


各端点字段以实际响应为准，本文不展开完整 schema。


---



## 6. 注意事项

- Runtime 接口面向 Gateway / Manager / 运维，不是终端用户浏览器直连面。
- `route` 的 `request_id` 约 60 秒内幂等；`config_refresh` 成功后不要自动再打。
- 收到 `CONFIG_SYNC_BUSY` 时稍后重试即可。
- `register_pods` / `deregister_pods` 仅在 routing 部署模式可用。
- AgentServer、Gateway 用户面、Manager `/api/v1` 的接口细节见各自文档。
