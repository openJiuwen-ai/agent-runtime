# Federated identity contracts

本包提供 Service Framework 的联合身份正式契约和传输无关编排。它负责把已经由外部
身份协议验证的用户映射为稳定的本地 Principal，但不绑定 FastAPI、数据库实现、
OAuth2 Server 或某一种企业身份协议。

## 1. 适用范围

典型链路如下：

```text
Browser / Client
  -> host authorization flow
  -> FederationProvider
  -> enterprise IdP
  -> validated ExternalIdentity
  -> FederationCoordinator
  -> FederatedIdentityStore
  -> LocalPrincipal
  -> host token issuer
  -> protected service Handler
```

外部 SAML、OIDC 或其他协议负责证明外部用户是谁；宿主认证中心负责签发内部 OAuth2
Token；Service Handler 只消费经过映射的本地 Principal。

## 2. 正式类型

| 类型 | 职责 |
| --- | --- |
| `FederationConnection` | 保存受信任 issuer 与本地组织的稳定绑定 |
| `ExternalIdentity` | 表示 Provider 完成协议验证后输出的标准化外部身份 |
| `LocalPrincipal` | 表示业务授权和 Handler 使用的本地身份 |
| `FederationProvider` | 定义开始外部登录和验证回调的异步协议接口 |
| `FederatedIdentityStore` | 定义外部身份到本地 Principal 的异步持久化接口 |
| `FederationCoordinator` | 校验连接边界并编排 Provider 与 Store |

`FederationCoordinator` 将回调验证和本地身份写入拆成两个步骤：

```python
authentication = await coordinator.consume_callback(connection_id, parameters)

# 宿主必须先验证自己的 OAuth2/SAML 关联状态、一次性请求和有效期。
await authorization_flow.require_request(
    authentication.authorization_request_id
)

principal = await coordinator.resolve_or_create(
    connection_id,
    authentication.identity,
)
```

这种顺序可以防止无效或已经过期的授权请求提前创建本地用户。

## 3. 异步约束

Provider 的 `begin_login()`、`consume_callback()`，Store 的
`resolve_or_create()`、`find()`、`close()` 必须使用 `async def`。编排器在构造阶段
检查这些方法，避免同步网络、数据库或文件操作进入事件循环。

具体实现还应使用真正的异步驱动。仅把同步函数声明为 `async def` 不能消除阻塞。

## 4. 宿主应用职责

正式联合身份包不负责：

- 创建 OAuth2 Authorization Request、Authorization Code 或 Token；
- 解析 HTTP Query、Form、Cookie 或返回 Redirect；
- 保存 SAML `AuthnRequest ID`、`InResponseTo` 或防重放状态；
- 创建用户、组织和成员关系的具体数据库表；
- 将企业部门自动映射为 Runtime `group_id`；
- 提供真实 SAML/OIDC SDK 配置。

这些能力由认证中心或应用适配器实现。正式包只保证不同实现共享相同的身份边界。

## 5. 安全要求

Provider 只能在完整验证外部协议后创建 `ExternalIdentity`。真实 SAML 实现至少需要
验证签名和证书、Issuer、Audience、Destination、Recipient、`InResponseTo`、时间窗口
和 Response/Assertion ID 防重放。

`(connection_id, issuer, external_subject)` 是稳定外部身份键。邮箱和展示名默认不能
作为唯一身份键。`FederatedIdentityStore.validate_binding()` 会拒绝连接或 issuer
漂移；`FederationCoordinator` 还会拒绝不属于连接绑定组织的 `LocalPrincipal`。具体
Store 必须用唯一约束或事务保证并发首次登录只创建一个本地用户。

## 6. 当前验证方式

仓库没有真实企业 IdP。正式接口通过 Fake Provider/Store 单元测试验证；
`service/examples/federated_auth` 使用 Demo Provider、Demo IdP 和 SQLite Store 验证完整
浏览器、OAuth2 Authorization Code、PKCE 和 Principal 注入链路。

Demo IdP 不解析或验证 SAML XML，不能作为真实企业认证实现。接入企业环境时应新增严格
验证协议的 Provider，并复用本包的 Coordinator、Store 契约和本地授权模型。
