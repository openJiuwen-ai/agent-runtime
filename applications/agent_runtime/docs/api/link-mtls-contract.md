# HTTP/SSE 链路 mTLS 接口契约

## 1. 启用方式

```ini
JIUWENSWARM_LINK_MTLS_MODE=enforce
```

该变量默认值为 `off`。部署工具负责向各工作负载注入当前角色的 profile 和证书文件。

| 模式 | 接口传输与认证 |
|---|---|
| `off` | 保持既有 HTTP 接口行为 |
| `observe` | 保持既有 HTTP 接口行为，并执行本地材料预检 |
| `enforce` | HTTPS + 客户端证书 + 角色指纹 + 绑定 Header |

## 2. TLS 契约

`enforce` 模式下：

- TLS 最低版本为 1.2；
- 服务端要求客户端证书；
- 客户端校验 CA、证书有效期、主机名/SAN 和目标角色证书指纹；
- 服务端从 TLS 连接读取实际对端证书并识别角色；
- HTTP/SSE 使用同一套 TLS 和绑定校验。

## 3. 绑定 Header

非健康检查请求必须携带：

| Header | 值 |
|---|---|
| `X-Jiuwenswarm-Mtls-Binding-Id` | 当前 `mtls_binding_id` |
| `X-Jiuwenswarm-Mtls-Binding-Epoch` | 当前 `mtls_binding_epoch` 的十进制字符串 |

Header 由当前角色 profile 或 Manager 的 `instance_link_binding` 记录生成。

以下健康检查路径只校验客户端证书角色，不要求绑定 Header：

- `/healthz`
- `/api/health`
- `/api/v1/health`
- `/api/v1/ready`

## 4. 调用权限

### 4.1 Agent Runtime

| 接口 | 客户端角色 |
|---|---|
| `GET /healthz` | `manager`、`gateway`、`runtime` |
| `POST /api/session/route` | `gateway` |
| `POST /api/session/touch` | `gateway` |
| `POST /api/session/cleanup` | `gateway` |
| `POST /api/session/config_refresh` | `gateway` |
| `POST /api/session/config_sync` | `manager` |
| `/visualization/*` | `manager` |

Agent Runtime 中未列入 Gateway 专属集合的非健康路径按 Manager 角色校验。

### 4.2 Gateway

| 接口 | 客户端角色 |
|---|---|
| 健康检查 | `manager`、`gateway`、`runtime` |
| `/api/v1/*` 内部配置接口 | `manager` |

### 4.3 AgentServer

| 接口 | 客户端角色 |
|---|---|
| 健康检查 | `manager`、`gateway`、`runtime` |
| HTTP/SSE 业务接口 | `gateway` |

## 5. Runtime 端点行为

### 5.1 健康检查

```http
GET /healthz HTTP/1.1
Host: jiuwenclaw-agent-runtime:8091
```

客户端提供受信任角色证书即可。响应沿用 Runtime 原有健康检查结构，并包含当前 Runtime 副本的 `instance_id`、命名空间和 Pod 名称。

### 5.2 配置同步

Manager 调用 `POST /api/session/config_sync` 时：

1. 根据业务 `jiuwenclaw_id` 读取生效的 `instance_link_binding`；
2. 校验请求目标与记录中的 Runtime authority 一致；
3. 使用 Manager 客户端证书及该记录的 Runtime CA/叶证书指纹；
4. 携带该记录的 binding ID 和 epoch；
5. 请求体继续使用 [`config-plane-api.md`](config-plane-api.md) 定义的业务信封。

### 5.3 路由响应扩展

`POST /api/session/route` 在 `enforce` 模式下返回的 `rawdata` 增加：

| 字段 | 类型 | 说明 |
|---|---|---|
| `mtls_deployment_id` | string | 当前部署材料标识 |
| `mtls_binding_id` | string | 当前绑定标识 |
| `mtls_binding_epoch` | integer | 当前绑定版本 |

原有 `pod_id`、`pod_sse_url` 字段保持不变；`pod_sse_url` 为与 AgentServer 证书 SAN 匹配的 HTTPS Pod DNS。

## 6. Manager 绑定接口

绑定接口前缀为 `/api/v1/instances/{jiuwenclaw_id}/link-binding`，使用 Manager 原有管理员认证。

| 方法 | 行为 |
|---|---|
| `PUT` | 创建绑定；完全相同请求幂等返回，冲突返回 409 |
| `GET` | 查询当前绑定；不存在返回 404 |
| `DELETE` | 将绑定置为 `unbound`、递增 epoch 并返回 `rotation_required` |

`PUT` 请求字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `mtls_binding_id` | string | 新绑定唯一标识 |
| `mtls_binding_epoch` | integer | 绑定版本，重绑时大于历史版本 |
| `mtls_gateway_endpoint` | string | 与 `instance_info.gateway_config_host` 相同的 authority |
| `mtls_runtime_endpoint` | string | 与 `instance_info.runtime_config_host` 相同的 authority |
| `manager_cert_fingerprint` | string | Manager 证书 SHA-256 DER 指纹 |
| `gateway_cert_fingerprint` | string | Gateway 证书指纹 |
| `runtime_cert_fingerprint` | string | Runtime 证书指纹 |
| `agentserver_cert_fingerprint` | string | AgentServer 证书指纹 |
| `trust_bundle_ref` | string | `profile://ca` 或已挂载的 `file:///绝对路径` |
| `updated_by` | string | 操作者 |
| `data` | object? | 绑定附加信息 |

Gateway、Runtime endpoint 仅接受无凭据、无路径、无查询参数、无 fragment 的 HTTP(S) authority，并与当前实例记录精确匹配。

## 7. 错误响应

mTLS 或绑定校验失败返回：

```http
HTTP/1.1 403 Forbidden
Content-Type: application/json
```

```json
{
  "ok": false,
  "error_code": "LINK_BINDING_MISMATCH",
  "error_message": "peer certificate is not authorized for this binding and role"
}
```

常见拒绝条件包括：

- 客户端证书缺失、过期或不受信任；
- 客户端证书指纹不属于该接口允许的角色；
- 绑定 ID 或 epoch 缺失、不一致；
- Manager 请求目标与 `instance_link_binding` 不一致；
- 当前 profile、证书文件与数据库授权状态不一致。

## 8. 地址规则

profile 中声明的受管 Gateway、Runtime authority 可使用原有 `http://` 配置值或裸 authority 作为业务配置输入；`enforce` 在建立内部连接时解析为 HTTPS，并保留原路径和查询参数。

不属于当前 profile 的外部地址直接按配置使用，在 `enforce` 模式下其 scheme 为 `https://`。地址中不接受用户名、密码或 fragment。

## 9. 相关文档

- [`../design/http-sse-link-mtls-design.md`](../design/http-sse-link-mtls-design.md)
- [`../spec/link-mtls.md`](../spec/link-mtls.md)
- [`config-plane-api.md`](config-plane-api.md)
- [`../../../../docs/zh/内部链路mTLS接入.md`](../../../../docs/zh/内部链路mTLS接入.md)
