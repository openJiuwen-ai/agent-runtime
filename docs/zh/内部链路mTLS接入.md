# Runtime 内部链路 mTLS 接入说明

## 使用方式

功能默认关闭。未设置变量或设置 `off` 均不启用；只有显式设置 `enforce` 才启用内部 HTTPS、双向证书和角色/绑定认证。

```ini
# 默认值
JIUWENSWARM_LINK_MTLS_MODE=off
```

使用配套 JiuwenSwarm 部署工具时，在已有 `.env.custom` 中改为：

```ini
JIUWENSWARM_LINK_MTLS_MODE=enforce
```

`observe` 只预检本地材料，不切换协议、不代表已经开启认证；不能用 `true`、`false` 或 `on` 代替枚举值。

由部署工具为各组件生成并注入角色材料，无须另填 CA/CERT/KEY 路径、绑定 ID、epoch 或手写 profile。详细安装步骤、客户管理服务示例、数据结构与运维限制见配套 JiuwenSwarm 仓库的 `docs/zh/HTTP-SSE链路mTLS部署配置.md`。

无内置 Manager 交付采用 `APPLY_PATCH=true` 和 `up gateway web runtime`；部署工具使用 manager 角色证书下发初始配置。客户自己的管理服务也是 manager 角色，不要求部署 Manager Server、Manager Web 或 Identity Center。

如明确使用内置管理参考实现，则按配套工具显式部署 `manager` 模块；`APPLY_PATCH=false` 本身不会自动添加该模块。mTLS 开关不代替用户登录和业务准入设置。

Manager 的服务证书不绑定某一个 `instance_info.jiuwenclaw_id`。Manager 保持原有多实例 ID 生成逻辑，通过 `instance_link_binding` 为每个实例选择独立的 `mtls_binding_id`、`mtls_binding_epoch`、目标证书指纹和信任包。同一工具内共同部署的 Gateway/Runtime 可在配置地址严格匹配时自动登记；其他实例使用显式绑定登记。

自动登记和显式绑定均只接受无凭据、无路径、无查询参数的精确 Gateway/Runtime 地址，并要求地址与该 `instance_info` 记录一致，避免把相似 URL 或其他实例的地址误认成当前部署。多实例请求先按业务 `jiuwenclaw_id` 选择绑定记录，再把该记录的 mTLS 绑定 Header、目标地址、目标 CA 和目标叶证书指纹作为同一个目标对象使用；不能把一个实例的 Header 与另一个实例的 TLS 目标混用。业务 ID 不放入 mTLS Header。

既有 ID 和 mTLS ID 不混用：`jiuwenclaw_id` 仍是 Manager 业务实例主键，`GATEWAY_INSTANCE_ID` 仍服务于 Gateway 主备/Redis，Runtime `instance_id` 仍是进程或副本身份。新机制只新增内部 `mtls_deployment_id`、`mtls_binding_id` 和 `mtls_binding_epoch`。`mtls_deployment_id` 由部署工具首次随机生成并持久化，不作为客户配置或请求 Header。

## 运行与数据原理

- Runtime 的内部 API 在 enforce 下要求 HTTPS/mTLS；会话 route、touch、cleanup、config_refresh 接受绑定的 gateway 角色，管理配置接口接受 manager 角色。
- 除 TLS CA/SAN 验证外，还核验对端角色指纹、`mtls_binding_id` 和 `mtls_binding_epoch`。Manager 的 `jiuwenclaw_id` 保留在原有业务 API 中，不作为证书身份；请求头不能替代实际 TLS 对端证书。
- Runtime 创建 AgentServer 时，向其主容器注入 AgentServer 专用 Secret，使用 headless Service 下的 Pod DNS；不向 JiuwenBox 注入证书。不要求为动态 Pod IP 逐个签发证书。
- 同一绑定的 AgentServer 可共享 AgentServer 角色证书，不代表每个 Pod 具有独立证书身份。Gateway、Runtime、manager 使用各自独立的私钥。
- Gateway 数据库的 `link_binding_state` 保存可恢复的完整角色材料，Runtime 同名表只保存公开状态及 AgentServer Secret 引用。实际运行依赖角色专属的 Secret/文件，不是直接把整套数据库材料交给每个服务。
- 首次随机签发十年证书，普通重部署复用已入库材料，不重签、不延长有效期；不一致、已撤销、过期或材料残缺时停止，不回退明文。

## 交付与安全约束

1. Gateway、Runtime、AgentServer、部署工具及 Runtime foundation 需要使用配套版本。镜像构建应包含 `foundation[link-mtls]` 所声明的 HTTP/TLS 依赖；不能仅给旧镜像增加环境变量即视为功能可用。
2. 不新增独立证书管理 Pod、在线 CA 或 Runtime 续期任务；非 root/权限适配场景可在现有 Pod 中使用仅复制材料的初始化/同步辅助容器。
3. 私钥材料可从 Gateway 数据库恢复，需保护数据库账号、备份、部署权限和 Secret RBAC；应用未额外加密材料列，Secret Base64 也不是加密。
4. 没有自动续期、跨组件一键换证/撤销/重绑定或旧材料自动导入。修改本地 profile、单个 Secret、某一数据库字段不构成完整集群维护流程。
5. 受管地址在 enforce 下解析为 HTTPS；外部地址须显式 HTTPS 且通过验证。此机制不会改写模型 API、用户登录或外部 A2A 的协议。
6. 从已启用的部署回退到 off 需协调全部相关组件及存量 AgentServer；仅修改 `.env` 不会立即改变运行中的服务。
7. 多实例 Manager 发起配置写请求时必须按 `jiuwenclaw_id` 同时选择绑定 Header、信任包和目标叶证书指纹；只信任全局 CA、却不核对该实例登记的目标指纹，不属于完整绑定校验。
