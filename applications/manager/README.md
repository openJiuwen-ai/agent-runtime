# Manager 管理面

`applications/manager` 是 JiuwenClaw 的管理平面，包含三个子模块：

| 目录 | 说明 | 默认端口 |
|------|------|----------|
| `identity_center/` | 认证中心（OAuth2 / JWT、用户与组织） | `8770` |
| `manager_server/` | 管理 API（实例、模板、策略等）+ Manager WebSocket | REST `8765`、WS `8766` |
| `manager_web/` | 管理面 Web 前端（React + Vite） | `5273` |

本地开发时，**先启动身份中心，再启动管理 API，最后启动前端**。

---

## 环境要求

- Python `>=3.11.4,<3.14`
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- Node.js `>=18`（前端开发 / 构建）
- 仓库根目录下的 `foundation` 包（Python 依赖）

---

## 首次安装

### 方式一：使用 uv（推荐）

```bash
# 创建虚拟环境（如果尚未创建）
uv venv

# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

# 安装 Python 依赖（一次性装齐，避免 uv sync --project 互相覆盖）
uv pip install -e foundation -e applications/manager/identity_center -e applications/manager/manager_server

# 安装前端依赖
cd applications/manager/manager_web && npm install && cd ../../..
```

> **注意**：不要用 `uv sync --project ... --active` 分别安装两个子项目——每次 sync 只保留当前项目的依赖，后执行的会把先安装的服务卸掉。应使用上面的 `uv pip install -e` 一次性安装。
>
> 激活后请确认 Python 来自本仓库 `.venv`（Windows：`where python` 应指向 `.venv\Scripts\python.exe`）。若当前是 conda 等其他环境，先 `deactivate` 再激活 `.venv`。

### 方式二：使用 pip + venv

```bash
# 创建虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

# 安装 Python 依赖
pip install -e foundation
pip install -e applications/manager/identity_center
pip install -e applications/manager/manager_server

# 安装前端依赖
cd applications/manager/manager_web && npm install && cd ../../..
```

默认使用 SQLite，无需额外配置数据库：

- 身份库：`identity.db`
- 管理库：`manager.db`

默认登录账号见下方「本地开发启动」章节。

---

## 本地开发启动（推荐）

开 **3 个终端**，均在仓库根目录、**已激活虚拟环境**的前提下执行。

### 终端 1：身份中心

```bash
identity-center
# 或: python -m identity_center.main
```

### 终端 2：管理 API

```bash
manager-server
# 或: python -m manager_server.main
```

### 终端 3：前端开发服务器

```bash
cd applications/manager/manager_web
npm run dev
```

浏览器访问：**http://127.0.0.1:5273**

### 默认登录账号

首次启动 `identity-center` 后会自动创建以下账号（仅用于本地开发）：

| 用户名 | 密码 | 角色 | 用途 |
|--------|------|------|------|
| `admin` | `admin` | 管理员 | 登录管理面（`/manager`），可进行实例、模板、策略等配置 |
| `user1` | `user1` | 普通用户 | 登录用户端（`/user` 跳转至同源 `/chat/`），无管理权限 |

Vite 开发服务器已配置代理，`/api` → `8765`、`/idp` → `8770`（会自动去掉 `/idp` 前缀）。

> 若登录报 `/idp/v1/auth/token` 404，请确认 `identity-center` 已在 `8770` 端口运行，并重启 `npm run dev` 使代理配置生效。  
`/chat`、`/ws`、`/gateway-api`、`/file-api` 和 `/share-api` 依赖 User Web / Gateway，未启动时用户面功能不可用，管理面主体功能可正常使用。

### 联合认证本地联调

Manager 保留原有 OAuth2 密码登录和本地 JWT，同时支持通过可替换的联合
认证 Provider 接入企业身份。仓库当前没有真实企业 IdP 配置，因此只提供一个
默认关闭、显式标注为本地模拟的 Demo Provider。它用于验证完整应用链路，
不接收或验证 SAML XML，不能作为生产 SAML 实现。

启动身份中心前设置：

```env
IDENTITY_FEDERATION_DEMO_ENABLED=true
# 通过 Vite 或 manager-web 的 /idp 同源代理访问时保持默认值
IDENTITY_FEDERATION_PUBLIC_PATH_PREFIX=/idp
# Demo 中映射为本地管理员的模拟企业用户组
IDENTITY_FEDERATION_DEMO_ADMIN_GROUP=enterprise-admins
```

重启 `identity-center` 后，访问 `http://127.0.0.1:5273/auth`，登录框下方会出现
`Enterprise Demo SSO` 入口。从模拟企业页面登录后，身份中心会：

1. 根据受信的 `connection_id + issuer + external_subject` 查找外部身份；
2. 首次登录时在一个数据库事务中创建本地虚拟组织、虚拟用户、外部身份映射和成员关系；
3. 根据本地受信规则将 Provider 已验证的 Claim 映射为本地角色，并同步 `is_admin`；
4. 重复登录复用同一个本地 `user_id`，更新展示名、已验证属性和当前权限；
5. 若用户已不属于企业管理员组，下次登录会撤销其本地管理员权限；
6. 向浏览器返回短时、一次性换码，再由前端换取与本地登录完全相同的 access JWT 和 refresh token。

Demo 登录页的 `Groups` 输入 `employees` 会得到普通用户，输入
`employees,enterprise-admins` 会得到管理员。这里模拟的是“Provider 已经验证过的企业
用户组 Claim”；回调中任意附加 `is_admin=true` 或 `role=admin` 都不会被信任。生产
SAML/OIDC Provider 必须先完成协议校验，再把验证后的 Claim 交给映射层。

业务侧始终只消费身份中心签发的本地 JWT，不需要解析 SAML 或依赖具体企业
协议。接入真实 SAML 时，应实现 Service Framework 提供的异步
`FederationProvider` 接口，并完成签名、Issuer/Audience、`InResponseTo`、时间窗口和
重放防护等验证；Manager 的本地身份映射、JWT 和前端业务页无需更换。

联合认证新增的身份库表如下：

| 表 | 职责 |
|------|------|
| `federation_connection` | 保存受信连接与本地组织的稳定绑定 |
| `federated_identity` | 保存外部 Subject 到本地 `app_user.user_id` 的唯一映射；使用三个外部身份字段的稳定 SHA-256 摘要作为唯一键 |
| `federation_role_mapping` | 保存受信 Claim 精确值到本地角色的映射规则 |
| `federation_login_state` | 保存有效期内的浏览器联合登录状态 |
| `federation_login_code` | 保存一次性换码的 SHA-256，不保存换码明文 |

`federation_connection` 中的组织绑定不允许通过普通组织删除接口破坏。虚拟用户仍是
标准 `app_user`，因此可直接使用现有 `/me`、`/me/orgs`、前端角色分流及已挂载的
Manager 权限守卫。

身份库各类数据按职责分离：`app_user` 是本地用户和最终权限的唯一业务主体；
`auth_identity` 只保存本地用户名/口令等认证凭据；`federated_identity` 只保存稳定的
外部身份绑定和最近一次经 Provider 验证的属性；`org` 与 `user_org_membership` 管理本地
组织目录；`auth_session` 管理可撤销的 refresh token；access JWT 自包含且不落库。
外部身份摘要由 `connection_id`、`issuer`、`external_subject` 的原始 UTF-8 内容计算，
原字段仍完整保存并在读取时复核，因此身份匹配不依赖数据库字符集或大小写排序规则。
联合认证不会把企业内部组织直接等同于平台任意 `group_id`，而是由
`federation_connection` 明确绑定到一个受控的本地虚拟组织，避免企业目录命名与平台
业务组织发生碰撞。

---

## 生产 / 集成模式（统一入口）

构建静态资源后，由 `manager-web` 提供单一 HTTP 入口（默认 `5273`）。User Web 保持独立进程，通过同源 `/chat/` 直接呈现，不使用 iframe。

```bash
# 构建 Manager 前端
cd applications/manager/manager_web
npm install
npm run build
cd ../../..

# 启动后端（需先激活虚拟环境）
identity-center
manager-server

# 启动统一 Web 入口
manager-web \
  --host 127.0.0.1 --port 5273 \
  --dist applications/manager/manager_web/dist \
  --user-web-target http://127.0.0.1:5173 \
  --gateway-ws-target http://127.0.0.1:19000 \
  --gateway-http-target http://127.0.0.1:19002
```

`5273` 为外部唯一入口：

- `/auth`、`/user`、`/manager` — Manager SPA；普通用户访问 `/user` 后跳转到 `/chat/`
- `/chat/` — 经身份校验的同源 User Web 页面
- `/api` — 转发至 Manager API（`8765`）
- `/idp` — 转发至身份中心（`8770`）
- `/ws` — 转发至 Gateway WebSocket（默认 `19000`）
- `/gateway-api`、`/file-api`、`/share-api` — 转发至 Gateway Web HTTP（默认 `19002`）

Gateway HTTP 使用独立的 `/gateway-api` 前缀，避免与 Manager API 的 `/api/v1/*` 发生路由冲突。

也可一键启动 API + 已构建的 Web（需先 `npm run build`）：

```bash
manager-start        # API + Web
manager-start manager  # 仅 API
manager-start web      # 仅 Web
```

---

## 常用环境变量

可在 `applications/manager/` 创建 `.env`（可复制 `.env.example`），各服务启动时自动读取：

```env
# 产品主展示名（manager_web）
VITE_PRODUCT_NAME=JiuwenSwarm

# 身份中心
IDENTITY_REST_HOST=0.0.0.0
IDENTITY_REST_PORT=8770
IDENTITY_DB_TYPE=sqlite
IDENTITY_SQLITE_PATH=identity.db
IDENTITY_FEDERATION_DEMO_ENABLED=false
IDENTITY_FEDERATION_PUBLIC_PATH_PREFIX=/idp
IDENTITY_FEDERATION_DEMO_ADMIN_GROUP=enterprise-admins

# 管理 API
MANAGER_REST_HOST=0.0.0.0
MANAGER_REST_PORT=8765
MANAGER_DB_TYPE=sqlite
MANAGER_SQLITE_PATH=manager.db
IDENTITY_PUBLIC_KEY_URL=http://127.0.0.1:8770/v1/auth/public_key

# 统一 Web 入口（manager-web）
MANAGER_WEB_HOST=127.0.0.1
MANAGER_WEB_PORT=5273
MANAGER_WEB_PROXY_TARGET=http://127.0.0.1:8765
MANAGER_WEB_IDP_TARGET=http://127.0.0.1:8770
MANAGER_WEB_USER_WEB_TARGET=http://127.0.0.1:5173
MANAGER_WEB_GATEWAY_HTTP_TARGET=http://127.0.0.1:19002
MANAGER_WEB_GATEWAY_WS_TARGET=http://127.0.0.1:19000
```

完整变量列表见 `applications/manager/.env.example`。
---

## 健康检查

| 服务 | 地址 |
|------|------|
| 身份中心 API 文档 | http://127.0.0.1:8770/docs |
| 管理 API 健康检查 | http://127.0.0.1:8765/api/health |
| 前端 | http://127.0.0.1:5273 |

---

## 命令速查

| 命令 | 说明 |
|------|------|
| `identity-center` | 启动身份中心 |
| `manager-server` | 启动管理 API |
| `manager-web` | 启动统一 Web 入口（静态资源 + 反向代理） |
| `manager-start` | 一键启动 API + Web |
| `npm run dev` | 前端开发模式（在 `manager_web/` 目录下） |
| `npm run build` | 构建前端静态资源到 `manager_web/dist/` |

> 以上 Python 命令均需在已激活的虚拟环境中执行（uv 或 pip 安装后均可直接使用）。
