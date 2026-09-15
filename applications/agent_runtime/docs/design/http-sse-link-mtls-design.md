# HTTP/SSE 内部链路 mTLS 设计

## 1. 目标与范围

本设计描述 JiuwenSwarm 内部 HTTP/SSE 链路的服务身份认证与实例绑定。覆盖以下组件：

- Manager 向 Gateway、Agent Runtime 下发配置；
- Gateway 向 Agent Runtime 请求路由，并与 AgentServer 建立 SSE 数据通道；
- Agent Runtime 管理 AgentServer Pod，并对 AgentServer 执行健康探测；
- Agent Runtime 创建 AgentServer 时注入其链路证书材料。

链路启用后同时使用 HTTPS、双向 TLS、角色证书指纹和绑定版本校验。用户登录、组织准入、Agent 授权等业务权限仍由原有认证与管理逻辑处理。

## 2. 组件与链路

```text
                         配置面
            ┌──────────────────────────────────┐
            │                                  │
        Manager ── HTTPS/mTLS ──► Gateway      │
            │                                  │
            └──── HTTPS/mTLS ──► Agent Runtime │
                                             │
                                  创建、探活 │
                                             ▼
Gateway ── HTTPS/mTLS ──► AgentServer ◄── HTTPS/mTLS ── Agent Runtime
             HTTP/SSE
```

各连接承担的职责如下：

| 调用方 | 服务端 | 主要用途 |
|---|---|---|
| Manager | Gateway | 模型、Agent、实例资源及其他管理配置下发 |
| Manager | Agent Runtime | `config_sync` 等 Runtime 配置下发 |
| Gateway | Agent Runtime | `route`、`touch`、`cleanup`、`config_refresh` |
| Gateway | AgentServer | HTTP/SSE 业务请求 |
| Agent Runtime | AgentServer | Pod 健康探测与资源管理 |

## 3. 身份模型

### 3.1 服务角色

证书材料按四种角色隔离：

| 角色 | 证书用途 |
|---|---|
| `manager` | 作为配置面客户端访问 Gateway、Agent Runtime |
| `gateway` | 作为 HTTPS 服务端接收 Manager 请求；作为客户端访问 Agent Runtime、AgentServer |
| `runtime` | 作为 HTTPS 服务端接收 Manager、Gateway 请求；作为客户端访问 AgentServer |
| `agentserver` | 作为 HTTPS 服务端接收 Gateway、Agent Runtime 请求 |

同一部署的四个角色由同一 CA 签发，但每个角色使用不同的私钥和叶证书。证书的扩展用途与角色一致：Manager 仅包含客户端用途，AgentServer 仅包含服务端用途，Gateway 和 Runtime 同时包含客户端与服务端用途。

### 3.2 标识分层

| 标识 | 含义 | 生命周期 |
|---|---|---|
| `jiuwenclaw_id` | Manager 业务实例主键，用于实例、资源、用户和组织等业务关联 | 随 Manager 实例记录 |
| `GATEWAY_INSTANCE_ID` | Gateway 运行身份，用于选主及 Redis 键隔离 | 随 Gateway 部署配置 |
| Runtime `instance_id` | 当前 Runtime 进程或副本身份 | 可随 Pod 或进程重建变化 |
| `mtls_deployment_id` | 一套部署证书材料的内部标识 | 首次签发后随材料持久化 |
| `mtls_binding_id` | 当前 mTLS 绑定的唯一标识 | 随绑定材料持久化 |
| `mtls_binding_epoch` | 绑定版本号，单调递增 | 随绑定变更推进 |

`jiuwenclaw_id` 用于 Manager 选择业务实例及其 `instance_link_binding` 记录。TLS 对端身份由实际客户端证书、角色指纹、`mtls_binding_id` 和 `mtls_binding_epoch` 共同确定。

多副本 Gateway 和 Runtime 共享同一套部署绑定材料；每个 Runtime 副本仍保留独立的进程 `instance_id`，因此证书身份不会替代现有的副本选主、日志和运行态标识。

## 4. 认证模型

### 4.1 TLS 层

服务端使用 TLS 1.2 或更高版本，并要求客户端提供证书。客户端完成以下校验：

1. 服务端证书由部署信任 CA 签发；
2. 访问主机名与证书 SAN 匹配；
3. 服务端叶证书指纹属于目标角色；
4. Manager 多实例请求还要求叶证书指纹与当前 `jiuwenclaw_id` 的绑定记录一致。

服务端从 TLS 连接中取得对端 DER 证书并计算 SHA-256 指纹。证书指纹决定调用方角色，请求 Header 不参与角色判定。

### 4.2 绑定层

非健康检查请求携带以下 Header：

```text
X-Jiuwenswarm-Mtls-Binding-Id: <mtls_binding_id>
X-Jiuwenswarm-Mtls-Binding-Epoch: <mtls_binding_epoch>
```

服务端将 Header 与本地已加载 profile 中的绑定标识和版本进行比较。只有 TLS 角色校验和绑定 Header 校验同时通过，请求才进入业务处理。

健康检查路径 `/healthz`、`/api/health`、`/api/v1/health`、`/api/v1/ready` 仍要求合法客户端证书，但不要求绑定 Header，便于组件在配置写入前完成可信探活。

### 4.3 角色权限

| 服务端 | 路径 | 允许的客户端角色 |
|---|---|---|
| Gateway | 健康检查 | `manager`、`gateway`、`runtime` |
| Gateway | 其他内部接口 | `manager` |
| Agent Runtime | 健康检查 | `manager`、`gateway`、`runtime` |
| Agent Runtime | `route`、`touch`、`cleanup`、`config_refresh` | `gateway` |
| Agent Runtime | 其他内部接口，包括 `config_sync` | `manager` |
| AgentServer | 健康检查 | `manager`、`gateway`、`runtime` |
| AgentServer | 业务接口 | `gateway` |

认证失败统一返回 HTTP 403 和 `LINK_BINDING_MISMATCH`，并在服务端记录拒绝原因。

## 5. 证书与 profile

### 5.1 签发结果

首次初始化生成一套十年有效期的材料：

```text
CA
├── manager/ca.crt, tls.crt, tls.key, profile.json
├── gateway/ca.crt, tls.crt, tls.key, profile.json
├── runtime/ca.crt, tls.crt, tls.key, profile.json
└── agentserver/ca.crt, tls.crt, tls.key, profile.json
```

CA 私钥仅在签发过程中使用；持久化材料包含 CA 证书、四个角色的叶证书、角色私钥和 profile。

### 5.2 profile 内容

每个角色的 `profile.json` 包含：

- `version`、`status` 和 `persistence`；
- `mtls_deployment_id`、`mtls_binding_id`、`mtls_binding_epoch`；
- 当前 `role`；
- CA、证书和私钥的相对路径；
- 四种角色的证书指纹集合；
- Gateway、Runtime 等受管端点。

加载 profile 时会校验结构、角色、证书有效期、私钥与证书匹配关系、证书摘要及本地角色指纹。服务运行期间，每个请求都会重新检查 profile 状态、绑定版本、文件引用和材料摘要；检测到变更时要求进程使用新身份重新启动。

### 5.3 地址与 SAN

Gateway 和 Runtime 证书包含 Service 名称、命名空间内 DNS、完整集群 DNS 和回环地址。AgentServer 证书包含 headless Service 下的通配 Pod DNS：

```text
*.<agentserver-headless-service>.<namespace>.svc.<cluster-domain>
```

因此 Agent Runtime 可按以下稳定名称访问动态创建的 AgentServer Pod：

```text
https://<pod-id>.<headless-service>.<namespace>.svc.<cluster-domain>:<port>
```

受管端点与 profile 中声明的 authority 匹配时，`enforce` 模式将其解析为 HTTPS，并保留路径和查询参数。外部地址在 `enforce` 模式下使用显式 `https://`。

## 6. 材料持久化与运行时注入

### 6.1 `link_binding_state`

Gateway 数据库中的 `link_binding_state` 保存 Gateway 公开绑定状态及可恢复的完整四角色材料。部署初始化采用数据库中的唯一 Gateway 记录作为权威状态；重复部署读取并校验该记录，然后复用同一套材料。

Agent Runtime 数据库中的同名表只保存 Runtime 的公开绑定状态和 AgentServer Secret 引用。Runtime 启动时核对数据库状态、当前 profile 和 Runtime 证书指纹是否一致。

### 6.2 Kubernetes Secret

部署集成把四个角色分别写入专属 Secret：

```text
jiuwenswarm-link-manager
jiuwenswarm-link-gateway
jiuwenswarm-link-runtime
jiuwenswarm-link-agentserver
```

Manager、Gateway 和 Agent Runtime 挂载各自的 Secret。Agent Runtime 创建 AgentServer Pod 时，仅向主容器注入 AgentServer Secret 和相关环境变量；普通 sidecar 不接收链路证书材料。

当 AgentServer 主容器以非 root 用户运行或设置 `fsGroup` 时，Runtime 使用内存卷及受限 helper 容器复制私有材料，并保持主容器读取路径不变。helper 删除全部 Linux capabilities，禁止提权，只挂载源 Secret 和目标内存卷。

## 7. Manager 多实例绑定

Manager 使用 `instance_info.jiuwenclaw_id` 选择业务实例，再从 `instance_link_binding` 取得该实例对应的：

- `mtls_binding_id` 和 `mtls_binding_epoch`；
- Gateway、Runtime 精确端点；
- Manager、Gateway、Runtime、AgentServer 证书指纹；
- 信任包引用和绑定状态。

同一 Gateway 或 Runtime authority 在 `bound` 状态下只能属于一个业务实例，数据库唯一索引在并发情况下保持该约束。相同绑定请求幂等返回当前记录；重新绑定要求使用更高的 `mtls_binding_epoch`。

共同部署的 Manager 可在 `instance_info` 中的 Gateway、Runtime 地址与本地 profile 声明完全一致时登记部署绑定。后续配置请求按 `jiuwenclaw_id` 读取绑定记录，并把目标地址、信任包、目标证书指纹和绑定 Header 作为同一个请求目标使用。

## 8. 模式

| 模式 | 行为 |
|---|---|
| `off` | 保持既有 HTTP 行为，不加载链路身份 |
| `observe` | 校验本地材料并记录预检结果，业务传输保持 HTTP |
| `enforce` | 启用 HTTPS、双向证书、角色指纹、绑定 Header 和 AgentServer Secret 注入 |

模式由 `JIUWENSWARM_LINK_MTLS_MODE` 控制，默认值为 `off`。

## 9. 代码边界

| 代码位置 | 职责 |
|---|---|
| `foundation/openjiuwen_runtime/foundation/security/` | 证书签发、profile、TLS transport、材料持久化与部署准备 |
| `applications/agent_runtime/src/agent_runtime/link_mtls.py` | Runtime 模式、端点、TLS 客户端/服务端参数和路由元数据 |
| `applications/agent_runtime/src/agent_runtime/link_binding_state.py` | Runtime 公开绑定状态同步 |
| `applications/agent_runtime/src/agent_runtime/resource_manager/k8s.py` | AgentServer DNS、Secret 与 helper 注入 |
| `applications/manager/manager_server/src/manager_server/` | 多实例绑定模型、接口及 Manager mTLS 客户端 |
| JiuwenSwarm Gateway、AgentServer 与部署脚本 | 对端守卫、HTTP/SSE 数据面及 Kubernetes Secret 安装 |

接口级约束见 [`../api/link-mtls-contract.md`](../api/link-mtls-contract.md)，实现规格见 [`../spec/link-mtls.md`](../spec/link-mtls.md)。
