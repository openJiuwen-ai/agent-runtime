# Manager 管理面

`applications/manager` 是 JiuwenClaw 的管理平面，包含三个子模块：

| 目录 | 说明 | 默认端口 |
|------|------|----------|
| `identity_center/` | 认证中心（OAuth2 / JWT、用户与组织） | `8770` |
| `manager_server/` | 管理 API（实例、模板等）+ Manager WebSocket | REST `8765`、WS `8766` |
| `manager_web/` | 管理面 Web 前端（React + Vite） | `5273` |

本地开发时，**先启动身份中心，再启动管理 API，最后启动前端**。

## 部署推荐运行环境

普通配置流程为：准备模型和 Agent 模板 → 创建集群 → 在「运行资源」中绑定部署推荐运行时模板 → 检查连接并验证对话。容器模板描述镜像、环境变量与挂载；运行时模板引用这些容器并设置调度参数；集群绑定决定适用范围与优先级。部署生成的模板已包含前两层配置，通常无需手动填写。

新版部署工具将最终渲染的 `conf/agentserver.json` 通过只读 Secret 交给 Manager Server。Manager 启动时将它转换为真实的容器模板和运行时模板，生成的模板直接显示在普通模板列表中，名称旁标注“默认”，不需要从浏览器选择 Linux 服务器上的文件。需同步更新 **Manager Server、Manager Web 和部署工具**，仅更新前端不能启用自动准备。具体命令见 [OCP 部署说明](../../deploy/openshift/README.md#manager-默认运行时模板) 和 [EE 部署说明](../../deploy/jiuwenswarm-ee/README.md)。

服务器进程可通过环境变量 `AGENTSERVER_JSON_PATH` 指定可读的最终渲染文件；旧的 `MANAGER_DEPLOY_TOOL_DIR/conf/agentserver.json` 路径仍兼容。`MANAGER_RUNTIME_DEFAULTS_ENABLED=false` 只关闭启动时自动初始化，管理员仍可通过下面的初始化接口显式生成。文件缺失或非法不会阻止 Manager 启动，可通过服务端日志或状态接口检查原因；模板列表不会生成占位数据。

管理员接口（使用现有管理认证）：

| 接口 | 行为 |
| --- | --- |
| `GET /api/v1/service-config-templates/deployment-defaults` | 只读检查文件与已生成模板，返回状态、配置指纹和模板摘要 |
| `POST /api/v1/service-config-templates/deployment-defaults/initialize` | 幂等生成或补齐本配置版本的模板，不创建集群绑定 |
| `GET /api/v1/service-config-templates/deployment-defaults/preview` | 读取部署文件中的原始配置与容器摘要，仅填充表单，不写数据库 |
| `POST /api/v1/service-config-templates/deployment-defaults/restore` | 提交预览指纹、源模板标识与表单，事务创建新的运行时模板和所需容器副本 |
| `GET /api/v1/container-templates/deployment-defaults/preview` | 只读获取原始默认容器配置，预分配新容器 ID，供表单选择及修改 |
| `POST /api/v1/container-templates/deployment-defaults/restore` | 校验配置指纹后保存独立容器副本；ID 已存在时返回 409，不覆盖原容器 |

相同配置重复初始化不会新增副本；配置变化生成新快照，已有模板、人工修改及集群绑定均保留。部署文件中的 `scopes` 不会自动成为集群授权。模板依赖原部署的镜像、存储、Secret/ConfigMap 和节点，跨部署复用时需核对这些依赖。模板生成成功不代表已验证实际 Agent 对话。

默认模板被误删或改错时，可在「新建运行时模板」中点击「读取默认模板」。该操作始终读取服务端部署文件，用户检查或调整表单后再保存；已有填写内容会先提示替换。保存只复制仍被选中的默认容器，也支持改选目录中的现有容器，不覆盖原模板或集群绑定。若部署文件在读取后发生变化，保存返回 409 并要求重新读取；文件未接入、缺失或非法时明确报错，不生成替代配置。此功能需要同时更新 Manager Server 和 Manager Web。

「新建容器模板」也支持读取默认模板，有多个容器时可选择其中一项。读取和取消不会写库，保存后再由运行时模板引用新容器。表单保留环境变量引用、启动命令、安全上下文、探针及扩展资源字段；高级分组可查看完整 JSON 配置。

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

首次启动身份中心前，复制 `applications/manager/.env.example` 为同目录的 `.env`。
其中的 `IDENTITY_ADMIN_PASSWORD` 和 `IDENTITY_USER1_PASSWORD` 分别设置预置账号密码；
直接运行身份中心且未设置密码时，新账号不会以固定密码创建。

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

首次启动 `identity-center` 后，会按 `.env` 中的密码创建以下账号（仅用于本地开发；已有账号不会被重置）：

| 用户名 | 密码 | 角色 | 用途 |
|--------|------|------|------|
| `admin` | `admin` | 管理员 | 登录管理面（`/manager`），可进行实例、模板等配置 |
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
| `federated_identity` | 保存外部 Subject 到本地 `identity_user.user_id` 的唯一映射；使用三个外部身份字段的稳定 SHA-256 摘要作为唯一键 |
| `federation_role_mapping` | 保存受信 Claim 精确值到本地角色的映射规则 |
| `federation_login_state` | 保存有效期内的浏览器联合登录状态 |
| `federation_login_code` | 保存一次性换码的 SHA-256，不保存换码明文 |

`federation_connection` 中的组织绑定不允许通过普通组织删除接口破坏。虚拟用户仍是
标准 `identity_user`，因此可直接使用现有 `/me`、`/me/orgs`、前端角色分流及已挂载的
Manager 权限守卫。

身份库各类数据按职责分离：`identity_user` 是本地用户和最终权限的唯一业务主体；
`auth_identity` 只保存本地用户名/口令等认证凭据；`federated_identity` 只保存稳定的
外部身份绑定和最近一次经 Provider 验证的属性；`identity_org` 与 `identity_user_org_membership` 管理本地
组织目录；`auth_session` 管理可撤销的 refresh token；access JWT 自包含且不落库。
外部身份摘要由 `connection_id`、`issuer`、`external_subject` 的原始 UTF-8 内容计算，
原字段仍完整保存并在读取时复核，因此身份匹配不依赖数据库字符集或大小写排序规则。
联合认证不会把企业内部组织直接等同于平台任意 `group_id`，而是由
`federation_connection` 明确绑定到一个受控的本地虚拟组织，避免企业目录命名与平台
业务组织发生碰撞。

> **存量库升级**：框架只 `create_all` / 补列，不改主键与表名。涉及身份表时请按需手工执行：
>
> ```sql
> -- 1) 旧表名迁移（若仍是 app_user / org / iam_*）
> ALTER TABLE app_user RENAME TO identity_user;
> ALTER TABLE org RENAME TO identity_org;
> ALTER TABLE iam_user RENAME TO identity_user;
> ALTER TABLE iam_org RENAME TO identity_org;
> ALTER TABLE iam_user_org_membership RENAME TO identity_user_org_membership;
>
> -- 2) 自增主键 id（SQLite 需重建表；MySQL / PostgreSQL 示例）
> -- MySQL:
> ALTER TABLE identity_user ADD COLUMN id BIGINT NOT NULL AUTO_INCREMENT UNIQUE FIRST;
> ALTER TABLE identity_user DROP PRIMARY KEY, ADD PRIMARY KEY (id), ADD UNIQUE KEY uq_identity_user_user_id (user_id);
> ALTER TABLE identity_org ADD COLUMN id BIGINT NOT NULL AUTO_INCREMENT UNIQUE FIRST;
> ALTER TABLE identity_org DROP PRIMARY KEY, ADD PRIMARY KEY (id), ADD UNIQUE KEY uq_identity_org_group_id (group_id);
> -- PostgreSQL:
> ALTER TABLE identity_user ADD COLUMN id BIGSERIAL;
> ALTER TABLE identity_user DROP CONSTRAINT identity_user_pkey;
> ALTER TABLE identity_user ADD PRIMARY KEY (id);
> ALTER TABLE identity_user ADD CONSTRAINT uq_identity_user_user_id UNIQUE (user_id);
> ALTER TABLE identity_org ADD COLUMN id BIGSERIAL;
> ALTER TABLE identity_org DROP CONSTRAINT identity_org_pkey;
> ALTER TABLE identity_org ADD PRIMARY KEY (id);
> ALTER TABLE identity_org ADD CONSTRAINT uq_identity_org_group_id UNIQUE (group_id);
> ```
>
> -- 3) identity_org.name → display_name（若列仍叫 name）
> ALTER TABLE identity_org RENAME COLUMN name TO display_name;

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
MANAGER_WEB_RESOLVER=kube-dns.kube-system.svc.cluster.local
```

生产 nginx 将 `/chat`、`/ws`、`/gateway-api`、`/file-api`、`/share-api` 按 Cookie `jiuwenclaw_id` 动态反代到各实例 `instance_info.user_web_host` 与 `instance_info.data` 中的 `gateway_web_*_host`（空则回退上述 `MANAGER_WEB_*_TARGET`）。`MANAGER_WEB_RESOLVER` 供变量 `proxy_pass` 做 DNS 解析（集群DNS的ClusterIP或完整服务名）。

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
