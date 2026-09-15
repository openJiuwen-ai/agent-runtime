# HTTP/SSE 内部链路 mTLS

## 基本信息

| 项 | 值 |
|---|---|
| 时间 | 2026-09 |
| Runtime 合入提交 | `bf82a24c` |
| Runtime PR | [openJiuwen/agent-runtime#523](https://gitcode.com/openJiuwen/agent-runtime/pull/523) |
| JiuwenSwarm PR | [openJiuwen/jiuwenswarm#6599](https://gitcode.com/openJiuwen/jiuwenswarm/pull/6599) |
| 涉及组件 | Foundation、Agent Runtime、Manager Server、Gateway、AgentServer、企业部署 |

## 背景

Gateway、Agent Runtime、AgentServer 和 Manager 的内部通信已经采用 HTTP/SSE。链路需要在保留现有业务路由、Manager 多实例管理和 Runtime 多副本能力的同时，为调用方与服务端建立可验证的服务身份。

## 实现

### 四角色材料

Foundation 为 `manager`、`gateway`、`runtime`、`agentserver` 签发独立私钥与叶证书，使用同一部署 CA，证书有效期为十年。每个角色通过 `profile.json` 取得当前部署标识、绑定标识、绑定版本、TLS 文件、角色证书指纹和受管端点。

### 双层校验

HTTPS 握手校验 CA、SAN 和客户端证书；应用层继续校验证书角色指纹以及 `X-Jiuwenswarm-Mtls-Binding-Id`、`X-Jiuwenswarm-Mtls-Binding-Epoch`。Runtime、Gateway 和 AgentServer 按路径限制可调用角色。

### Runtime 与 AgentServer

Agent Runtime 在 `enforce` 模式以 HTTPS/mTLS 提供接口，并通过 headless Service Pod DNS 生成 AgentServer HTTPS 地址。创建 AgentServer Pod 时，只向主容器注入 AgentServer 角色 Secret；非 root 容器使用内存卷和受限 helper 完成材料复制。

`route` 响应携带当前 mTLS 部署、绑定和 epoch 元数据，使 Gateway 获得的 AgentServer 地址与当前部署绑定保持一致。

### 数据库持久化

Gateway 数据库的 `link_binding_state` 保存可恢复的四角色材料和 Gateway 公开状态；Runtime 数据库的同名表保存 Runtime 公开状态及 AgentServer Secret 引用。部署过程从数据库读取并校验当前材料。

### Manager 多实例

Manager 使用既有 `jiuwenclaw_id` 管理业务实例，并在 `instance_link_binding` 中记录每个实例的 mTLS 绑定、Gateway/Runtime authority、四角色证书指纹和信任包。同一 Gateway 或 Runtime authority 只能处于一个生效绑定中。

Manager 的配置请求按 `jiuwenclaw_id` 选择绑定记录，再使用该记录的目标地址、绑定 Header、CA 和目标证书指纹建立请求。共同部署实例可在配置地址与部署 profile 精确一致时自动登记该绑定。

### 兼容模式

`JIUWENSWARM_LINK_MTLS_MODE` 默认 `off`。`observe` 校验本地材料并保持原有 HTTP 传输；`enforce` 启用完整 HTTPS/mTLS、角色和绑定校验。

## 代码落点

- Foundation：`foundation/openjiuwen_runtime/foundation/security/`；
- Runtime：`applications/agent_runtime/src/agent_runtime/link_mtls.py`、`link_binding_state.py`、`resource_manager/k8s.py`；
- Manager：`applications/manager/manager_server/src/manager_server/security/link_mtls.py`、`core/instance/link_binding_service.py`；
- Gateway、AgentServer 与企业部署：JiuwenSwarm PR #6599。

## 验证结果

### 自动化验收

Kubernetes 自动验收覆盖安装身份、数据库状态、Secret 挂载、TLS 正负例、角色隔离、绑定 Header、HTTP/SSE 聊天、落库故障回归、生命周期行为和三种模式，共得到：

```text
PASS_IN_DECLARED_SCOPE
PASS 157
FAIL 0
ERROR 0
SKIP 0
```

### Manager 交付形态

在 `APPLY_PATCH=false`、`LOGIN_AUTH_SIMULATE=false` 的 Manager/Identity 形态完成：

- Manager 使用 HTTPS/mTLS 探活 Gateway、Agent Runtime；
- Manager 创建独立 `jiuwenclaw_id`，Gateway、Runtime 状态为 online；
- 模型模板、Agent 模板、Agent 资源、服务模板和服务资源完成配置下发；
- Agent Runtime 接收 `config_sync` 并创建 AgentServer；
- User Web 经 Gateway、Agent Runtime、AgentServer 到达 mock 模型，SSE 返回 `MOCK_MODEL_OK`；
- Manager 业务实例 ID 与 mTLS 内部部署标识独立工作。

### 补丁交付形态

在 `APPLY_PATCH=true`、`LOGIN_AUTH_SIMULATE=true` 的无内置 Manager 形态完成同一 HTTP/SSE 业务链路，mock 模型返回 `MOCK_MODEL_OK`，流以 `chat.processing_status(is_processing=false, is_complete=true)` 正常结束。

## 文档

- 设计：[`../design/http-sse-link-mtls-design.md`](../design/http-sse-link-mtls-design.md)
- 实现规格：[`../spec/link-mtls.md`](../spec/link-mtls.md)
- 接口契约：[`../api/link-mtls-contract.md`](../api/link-mtls-contract.md)
- 使用说明：[`../../../../docs/zh/内部链路mTLS接入.md`](../../../../docs/zh/内部链路mTLS接入.md)
