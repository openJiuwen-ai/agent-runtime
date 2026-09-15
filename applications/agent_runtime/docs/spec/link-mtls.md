# HTTP/SSE 链路 mTLS 实现规格

## 1. 配置入口

### 1.1 通用环境变量

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `JIUWENSWARM_LINK_MTLS_MODE` | `off` | `off`、`observe`、`enforce` 三种模式 |
| `JIUWENSWARM_LINK_MTLS_PROFILE` | 按角色解析 | 当前角色 `profile.json` 路径 |
| `JIUWENSWARM_LINK_MTLS_CA_FILE` | 由 profile/部署注入 | CA 证书路径 |
| `JIUWENSWARM_LINK_MTLS_CERT_FILE` | 由 profile/部署注入 | 当前角色证书路径 |
| `JIUWENSWARM_LINK_MTLS_KEY_FILE` | 由 profile/部署注入 | 当前角色私钥路径 |

默认 profile 路径为：

```text
/etc/jiuwenswarm/link-mtls/<role>/profile.json
```

使用部署工具时，客户只需显式设置 `JIUWENSWARM_LINK_MTLS_MODE=enforce`；其余材料路径和角色配置由部署过程写入工作负载。

### 1.2 Agent Runtime 环境变量

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AGENT_RUNTIME_LINK_MTLS_AGENTSERVER_SECRET` | 空 | AgentServer 角色 Secret 名称；`enforce` 下必需 |
| `AGENT_RUNTIME_LINK_MTLS_HEADLESS_SERVICE` | `jiuwenclaw-agentserver` | AgentServer headless Service 名称 |
| `AGENT_RUNTIME_LINK_MTLS_CLUSTER_DOMAIN` | `cluster.local` | Kubernetes 集群 DNS 后缀 |
| `AGENT_RUNTIME_LINK_MTLS_MOUNT_DIR` | `/etc/jiuwenswarm/link-mtls` | AgentServer 主容器中的材料挂载根目录 |

## 2. Runtime 启动流程

1. `LinkMTLSConfig.from_env()` 解析运行模式。
2. `observe`、`enforce` 加载 `runtime` 角色 profile；`enforce` 要求材料完整有效。
3. `OrchestratorSystemContext.start()` 连接数据库并同步 `link_binding_state`。
4. `enforce` 使用 profile 生成 Uvicorn TLS 参数：客户端证书必需、TLS 最低版本 1.2、WebSocket 关闭。
5. Runtime 记录 mode、`mtls_deployment_id`、`mtls_binding_id` 和 epoch 后进入服务状态。

`off` 模式不创建或更新 `link_binding_state`。`observe` 完成材料预检后继续使用既有 HTTP 传输。

## 3. Runtime 请求守卫

`create_app()` 在业务 handler 之前注册 `_link_binding_guard`：

```text
HTTP request
  └─ LinkMTLSConfig.authorize_request()
       └─ LinkProfile.authorize(scope, headers)
            ├─ 读取 TLS peer certificate
            ├─ 校验证书有效期和角色指纹
            ├─ 校验路径对应的允许角色
            └─ 校验 binding ID / epoch Header
```

校验失败响应：

```json
{
  "ok": false,
  "error_code": "LINK_BINDING_MISMATCH",
  "error_message": "<拒绝原因>"
}
```

HTTP 状态码为 403。健康检查路径只执行 TLS 对端角色校验，其他路径同时校验绑定 Header。

## 4. Runtime 客户端行为

### 4.1 AgentServer 地址

`LinkMTLSConfig.agentserver_url()` 在 `enforce` 下生成：

```text
https://<pod-id>.<headless-service>.<namespace>.svc.<cluster-domain>:<port><path>
```

`off`、`observe` 沿用 Pod IP 的 HTTP 地址。

### 4.2 AgentServer 健康探测

`K8sResourceManager.probe_pod_health()` 使用 `httpx.AsyncClient`，加载 Runtime 客户端证书、目标 AgentServer 角色 pin 和绑定 Header。只有 HTTP 200 被视为健康。

### 4.3 路由响应

`route` 在 `enforce` 下除 `pod_id`、`pod_sse_url` 外增加：

```json
{
  "mtls_deployment_id": "<uuid>",
  "mtls_binding_id": "<uuid>",
  "mtls_binding_epoch": 1
}
```

Gateway 使用 `pod_sse_url` 建立 HTTPS/SSE 连接，并依据同一部署 profile 完成 AgentServer 证书和绑定校验。

## 5. AgentServer Pod 注入

`K8sResourceManager._inject_link_mtls()` 只在 `enforce` 模式执行：

1. 校验 `AGENT_RUNTIME_LINK_MTLS_AGENTSERVER_SECRET` 非空；
2. 将 AgentServer Secret 作为 `jiuwenswarm-link-mtls` 卷挂载到主容器；
3. 覆盖主容器中的 profile、CA、证书、私钥路径和 `enforce` 模式变量；
4. 把 readiness probe 设置为 AgentServer 端口的 TCP 探针；
5. 为 Pod 设置 `hostname=pod_id` 和配置的 `subdomain`，形成可由证书 SAN 覆盖的完整 Pod DNS；
6. sidecar 容器保持原有规格，不注入该 Secret 和环境变量。

当主容器以非 root 用户运行或 Pod 设置 `fsGroup` 时：

- Secret 源卷使用只读挂载；
- `link-material-init` 在启动前把材料复制到内存 `emptyDir`；
- `link-material-sync` 在运行时保持副本与 Secret 一致；
- 两个 helper 使用主镜像和主容器 UID/GID，不继承业务挂载、环境和权限；
- helper 禁止提权并删除全部 capabilities。

## 6. profile 与 TLS transport

`LinkProfile.load()` 完成以下校验：

- profile 版本、状态、角色和三类 mTLS 标识；
- CA、证书、私钥文件存在且引用受 profile 目录约束；
- 本地证书与私钥匹配且处于有效期；
- peer pin 为 64 位 SHA-256 DER 指纹，且同一证书不代表多个角色；
- 受管端点 authority 格式有效。

`LinkProfile.current()` 在请求期间重新读取 profile 和材料摘要。`PinnedAsyncTransport` 在标准 CA/SAN 校验后继续核验目标角色指纹；服务端 `PeerCertificateH11Protocol` 把实际 TLS 对端证书写入 ASGI scope，供请求守卫使用。

## 7. 数据库规格

### 7.1 Gateway 数据库：`link_binding_state`

Gateway 数据库只允许一条 `service_role=gateway` 的当前部署记录。公开列包括：

| 字段 | 含义 |
|---|---|
| `service_role` | 当前记录角色，Gateway 库固定为 `gateway` |
| `mtls_deployment_id` | 部署材料标识 |
| `mtls_binding_id` | 当前绑定标识 |
| `protocol_version` | 当前为 `0.0.1` |
| `mtls_binding_epoch` | 当前绑定版本 |
| `local_cert_pem` | Gateway 叶证书 |
| `local_cert_fingerprint` | Gateway 叶证书 SHA-256 指纹 |
| `private_key_ref` | 运行时私钥挂载引用 |
| `peer_trust_bundle_pem` | CA 证书 |
| `agentserver_secret_ref` | AgentServer Secret 引用 |
| `status` | 当前绑定状态 |
| `created_at`、`updated_at` | 创建和更新时间 |

部署专用列为：

| 字段 | 含义 |
|---|---|
| `material_schema_version` | 完整材料序列化版本 |
| `material_epoch` | 材料对应的绑定版本 |
| `certificate_materials` | 四角色完整证书材料 envelope |

首次初始化生成随机 `mtls_deployment_id` 和 `mtls_binding_id`，先提交数据库记录，再将角色材料安装为 Secret。重复初始化读取、完整校验并复用数据库材料；并发初始化由唯一约束选定一套结果。

### 7.2 Runtime 数据库：`link_binding_state`

Runtime 数据库使用同名表保存 `service_role=runtime` 的公开状态。该表的 Runtime 业务模型不映射 `certificate_materials`，并记录 AgentServer Secret 引用。启动时比较：

- `mtls_deployment_id`；
- `mtls_binding_id`；
- `mtls_binding_epoch`；
- Runtime 本地证书指纹；
- `status` 和材料版本。

### 7.3 Manager 数据库：`instance_link_binding`

Manager 表以 `jiuwenclaw_id` 为主键，主要字段为：

| 字段 | 含义 |
|---|---|
| `mtls_binding_id`、`mtls_binding_epoch` | 业务实例使用的 mTLS 绑定及版本 |
| `mtls_gateway_endpoint`、`mtls_runtime_endpoint` | 与 `instance_info` 对应的目标 authority |
| `mtls_gateway_active_key`、`mtls_runtime_active_key` | 仅绑定生效时写入的唯一键 |
| 四角色证书指纹 | Manager、Gateway、Runtime、AgentServer 的目标角色 pin |
| `trust_bundle_ref` | 当前实例的信任包引用 |
| `status` | `bound` 或 `unbound` |
| 时间、操作者和 `data` | 绑定审计信息 |

Gateway、Runtime active key 的唯一索引保证一个目标 authority 只属于一个生效实例。Manager 发送配置请求时按 `jiuwenclaw_id` 读取这一行，不使用 Manager 证书中的 `mtls_deployment_id` 替代业务实例 ID。

## 8. Manager 绑定流程

实例绑定接口由 `InstanceLinkBindingService` 实现：

1. 读取 `instance_info`；
2. 要求提交的 Gateway、Runtime authority 与实例记录精确一致；
3. 检查两个 authority 未被其他生效实例占用；
4. 写入绑定 ID、epoch、各角色指纹和信任包；
5. 相同绑定请求幂等返回；已有不同绑定返回冲突；
6. 已解绑实例重新绑定时，新 epoch 必须大于历史 epoch。

共同部署场景可通过 `adopt_deployment_profile()` 从 Manager 本地 profile 登记绑定。该流程仅在实例的 Gateway、Runtime authority 与 profile 声明完全一致时生效。

Manager 后续请求使用 `ManagerLinkMTLSConfig.target()` 同时解析目标 HTTPS 地址、绑定 Header、信任 CA 和目标叶证书指纹。

## 9. 实现文件

| 文件 | 主要内容 |
|---|---|
| `foundation/.../security/link_certificate_bundle.py` | 四角色证书签发与 bundle 校验 |
| `foundation/.../security/link_material_store.py` | 数据库持久化、复用和公开状态同步 |
| `foundation/.../security/link_material_deploy.py` | 部署准备和角色材料输出 |
| `foundation/.../security/link_profile.py` | profile、角色授权、端点和 TLS 参数 |
| `foundation/.../security/link_transport.py` | TLS peer certificate 与客户端 pin transport |
| `applications/agent_runtime/src/agent_runtime/link_mtls.py` | Runtime mTLS 配置与 URL 生成 |
| `applications/agent_runtime/src/agent_runtime/link_binding_state.py` | Runtime 本地绑定状态 |
| `applications/agent_runtime/src/agent_runtime/resource_manager/k8s.py` | AgentServer Pod 材料注入 |
| `applications/manager/manager_server/src/manager_server/security/link_mtls.py` | Manager 多实例 mTLS 客户端 |
| `applications/manager/manager_server/src/manager_server/core/instance/link_binding_service.py` | Manager 绑定编排 |

设计背景见 [`../design/http-sse-link-mtls-design.md`](../design/http-sse-link-mtls-design.md)，对外契约见 [`../api/link-mtls-contract.md`](../api/link-mtls-contract.md)。
