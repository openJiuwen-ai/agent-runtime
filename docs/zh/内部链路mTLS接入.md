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

由部署工具为各组件生成并注入角色材料，无须另填 CA/CERT/KEY 路径、绑定 ID、epoch 或手写 profile。详细安装步骤、客户管理服务示例和数据结构见配套 JiuwenSwarm 仓库的 `docs/zh/HTTP-SSE链路mTLS部署配置.md`。

无内置 Manager 交付采用 `APPLY_PATCH=true` 和 `up gateway web runtime`；部署工具使用 manager 角色证书下发初始配置。客户自己的管理服务也是 manager 角色，不要求部署 Manager Server、Manager Web 或 Identity Center。

如明确使用内置管理参考实现，则按配套工具显式部署 `manager` 模块；`APPLY_PATCH=false` 本身不会自动添加该模块。mTLS 开关不代替用户登录和业务准入设置。

Manager 的服务证书不绑定某一个 `instance_info.jiuwenclaw_id`。Manager 保持原有多实例 ID 生成逻辑，通过 `instance_link_binding` 为每个实例选择独立的 `mtls_binding_id`、`mtls_binding_epoch`、目标证书指纹和信任包。同一工具内共同部署的 Gateway/Runtime 可在配置地址严格匹配时自动登记；其他实例使用显式绑定登记。

自动登记和显式绑定均只接受无凭据、无路径、无查询参数的精确 Gateway/Runtime 地址，并要求地址与该 `instance_info` 记录一致，避免把相似 URL 或其他实例的地址误认成当前部署。多实例请求先按业务 `jiuwenclaw_id` 选择绑定记录，再把该记录的 mTLS 绑定 Header、目标地址、目标 CA 和目标叶证书指纹作为同一个目标对象使用；不能把一个实例的 Header 与另一个实例的 TLS 目标混用。业务 ID 不放入 mTLS Header。

`jiuwenclaw_id` 是 Manager 业务实例主键，`GATEWAY_INSTANCE_ID` 服务于 Gateway 主备和 Redis，Runtime `instance_id` 表示进程或副本。mTLS 使用内部的 `mtls_deployment_id`、`mtls_binding_id` 和 `mtls_binding_epoch`；`mtls_deployment_id` 由部署工具首次随机生成并持久化。

## 运行与数据原理

- Runtime 的内部 API 在 enforce 下要求 HTTPS/mTLS；会话 route、touch、cleanup、config_refresh 接受绑定的 gateway 角色，管理配置接口接受 manager 角色。
- 除 TLS CA/SAN 验证外，还核验实际 TLS 对端证书的角色指纹、`mtls_binding_id` 和 `mtls_binding_epoch`。Manager 的 `jiuwenclaw_id` 保留在原有业务 API 中，用于选择当前实例绑定。
- Runtime 创建 AgentServer 时，向其主容器注入 AgentServer 专用 Secret，使用 headless Service 下的 Pod DNS；不向 JiuwenBox 注入证书。不要求为动态 Pod IP 逐个签发证书。
- 同一绑定的 AgentServer 使用 AgentServer 角色证书；Gateway、Runtime、manager 使用各自独立的私钥。
- Gateway 数据库的 `link_binding_state` 保存可恢复的完整角色材料，Runtime 同名表只保存公开状态及 AgentServer Secret 引用。实际运行依赖角色专属的 Secret/文件，不是直接把整套数据库材料交给每个服务。
- 首次随机签发十年证书并写入数据库；部署时校验数据库、Secret 和 profile 的材料及绑定版本一致性。

## 部署与运行要求

1. Gateway、Runtime、AgentServer、部署工具及 Runtime foundation 需要使用配套版本。镜像构建应包含 `foundation[link-mtls]` 所声明的 HTTP/TLS 依赖；不能仅给旧镜像增加环境变量即视为功能可用。
2. 数据库保存部署证书材料，Kubernetes Secret 保存各工作负载的角色材料；需按私钥数据保护数据库账号、备份、部署权限和 Secret RBAC。
3. 受管地址在 `enforce` 下解析为 HTTPS；外部地址使用显式 HTTPS 并通过 CA、SAN 和目标指纹验证。
4. 多实例 Manager 发起配置请求时，按 `jiuwenclaw_id` 同时选择绑定 Header、信任包、目标地址和目标叶证书指纹。
5. 服务在启动时读取 mTLS 模式和 profile；部署配置更新通过对应工作负载的重新部署生效。
