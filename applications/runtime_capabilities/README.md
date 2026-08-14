# Runtime Capabilities

`runtime_capabilities` 是基于 `openjiuwen_runtime.service` 构建的可部署应用，用于通过一组明确的
REST API 验证运行时框架的数据库、Redis、Envelope 解析和 Kubernetes Pod 管理能力。

它位于 `applications/`，属于可独立安装、启动和部署的应用；`service/examples/` 中的框架示例仍然
保持原样。应用复用 `service` 提供的 `App`、`SystemContext`、请求上下文、数据库抽象、Redis
原语和 Kubernetes 抽象，不在应用目录中重复实现这些通用能力。

## 1. 功能范围

应用启动后提供 `/docs`、`/openapi.json`、`/health`，以及以下 8 个业务接口：

| 分组 | 方法与路径 | 作用 |
|---|---|---|
| Database | `POST /api/db/write` | 创建或更新一条记录 |
| Database | `POST /api/db/read` | 按 ID 查询记录 |
| Redis | `POST /api/redis/write` | 写入带 TTL 的字符串值 |
| Redis | `POST /api/redis/read` | 查询字符串值 |
| Envelope | `POST /api/envelope/inspect` | 解析并返回 Envelope、Metadata 和请求上下文 |
| Kubernetes | `POST /api/k8s/pod/create` | 按受限模板创建一个 Pod |
| Kubernetes | `POST /api/k8s/pod/read` | 查询应用管理的 Pod |
| Kubernetes | `POST /api/k8s/pod/delete` | 删除应用管理的 Pod |

所有业务接口都接收完整的 OpenJiuwen Envelope。REST 路径和请求体中的 `type` 是同一个处理器
标识的两种表达：例如 `/api/db/write` 只接受 `type: "db/write"`。路径与 `type` 不一致时会被
FastAPI/OpenAPI 请求模型拒绝，未知路径返回 HTTP 404。

## 2. 运行模式

启动时必须显式指定一个模式：

| 模式 | 数据库 | Redis | Kubernetes | 适用场景 |
|---|---|---|---|---|
| `local` | SQLite 文件 | FakeRedis | 内存中的 Fake Kubernetes | 本地开发、接口联调、自动化测试 |
| `server` | 真实 MySQL | 真实 Redis | 真实 Kubernetes API | 服务器和集群能力验证 |

模式由命令行 `--mode` 或部署脚本的第一个参数选择。应用会把它同步到
`RUNTIME_CAPABILITIES_MODE`。一个进程内不会混用真假资源：`local` 始终使用全部本地资源，
`server` 始终要求真实 MySQL、Redis 和 Kubernetes。

`local` 模式中的 FakeRedis 和 Fake Kubernetes 数据仅在当前进程生命周期内存在；SQLite 数据
保存在 `RUNTIME_CAPABILITIES_SQLITE_PATH` 指向的文件中。`server` 模式启动时会校验数据库类型
必须为 `mysql`，并校验 Redis URL 已配置且合法，防止误以为正在验证真实资源而实际落到本地
替代实现。

## 3. 目录结构

```text
applications/runtime_capabilities/
├── README.md
├── pyproject.toml
├── runtime_capabilities.local.env.example
├── runtime_capabilities.server.env.example
├── scripts/
│   └── deploy.sh
├── src/runtime_capabilities/
│   ├── __init__.py
│   ├── __main__.py
│   ├── application.py
│   └── cli.py
└── tests/
    └── test_application.py
```

- `application.py`：配置策略、请求/响应模型、8 个 Handler 和应用工厂。
- `cli.py`：统一命令行入口，负责加载 dotenv、选择模式并启动 Uvicorn。
- `scripts/deploy.sh`：面向开发者的安装与启动辅助脚本。
- 两个 `*.env.example`：可提交的配置模板；实际 `.env.*.local` 文件不会进入 Git。
- `tests/`：配置单元测试和经 ASGI 发起真实 HTTP 请求的系统测试。

## 4. 本地启动

要求 Python `>=3.11.4`，并已安装 `uv`。在仓库根目录执行：

```bash
cp applications/runtime_capabilities/runtime_capabilities.local.env.example \
  applications/runtime_capabilities/.env.development.local

./applications/runtime_capabilities/scripts/deploy.sh local
```

首次执行时，即使不手动复制模板，脚本也会创建默认的
`applications/runtime_capabilities/.env.development.local`。随后脚本会：

1. 通过 `uv sync --extra local` 同步应用及本仓 `foundation`、`service` 依赖；
2. 加载本地配置文件；
3. 以单 worker 启动应用。

默认访问地址：

- Swagger UI：`http://127.0.0.1:8090/docs`
- OpenAPI：`http://127.0.0.1:8090/openapi.json`
- 健康检查：`http://127.0.0.1:8090/health`

也可以绕过脚本直接启动：

```bash
cd applications/runtime_capabilities
uv sync --extra local
uv run runtime-capabilities \
  --mode local \
  --env-file .env.development.local
```

## 5. 服务器模式

先复制服务器模板，并按目标环境填写真实资源：

```bash
cp applications/runtime_capabilities/runtime_capabilities.server.env.example \
  applications/runtime_capabilities/.env.production.local
```

至少需要确认以下配置：

```dotenv
RUNTIME_CAPABILITIES_MODE=server
RUNTIME_CAPABILITIES_KUBERNETES_NAMESPACE=runtime-capabilities
RUNTIME_CAPABILITIES_KUBERNETES_POD_IMAGE=redis:7-alpine
RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_USER=999
RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_GROUP=1000

OPENJIUWEN_SERVICE_DB_TYPE=mysql
OPENJIUWEN_SERVICE_DB_HOST=mysql.example.internal
OPENJIUWEN_SERVICE_DB_PORT=3306
OPENJIUWEN_SERVICE_DB_NAME=runtime_capabilities
OPENJIUWEN_SERVICE_DB_USER=runtime_capabilities
OPENJIUWEN_SERVICE_DB_PASSWORD=change-me

OPENJIUWEN_SERVICE_REDIS_URL=redis://redis.example.internal:6379/0
OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX=runtime-capabilities
```

然后在仓库根目录执行：

```bash
./applications/runtime_capabilities/scripts/deploy.sh server
```

辅助脚本只同步依赖、加载配置并启动应用，不会创建 MySQL、Redis、Kubernetes namespace、
ServiceAccount 或 RBAC。外部基础设施应由环境管理员预先准备。

### 5.1 MySQL 要求

配置的 MySQL 账户需要具备目标数据库的连接权限，以及应用表的建表和 CRUD 权限。框架启动时会
确保数据库及 `runtime_capability_records` 表存在；若目标环境禁止应用创建数据库，应由管理员
预先创建 `OPENJIUWEN_SERVICE_DB_NAME` 指定的数据库，并授予必要的表级权限。

### 5.2 Kubernetes 连接与权限

应用只操作 `RUNTIME_CAPABILITIES_KUBERNETES_NAMESPACE` 指定 namespace 中、带有本应用管理标签
的 Pod。

- 应用运行在 Kubernetes 集群内时，客户端优先使用 Pod 的 ServiceAccount。
- 应用作为服务器本地进程运行时，通过 `KUBECONFIG` 指向可访问目标集群的 kubeconfig；未显式
  设置时，Kubernetes 客户端使用默认 kubeconfig 查找规则。
- 身份至少需要目标 namespace 中 Pod 的 `get`、`list`、`create`、`delete` 权限。

生产或共享测试环境中应按 namespace 最小授权，不要授予集群管理员权限。一个最小 Role 的规则
部分如下，具体 ServiceAccount 和 RoleBinding 名称由部署环境决定：

```yaml
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "create", "delete"]
```

Pod 模板固定启用 `runAsNonRoot`、禁止提权并丢弃 Linux capabilities。如果所选镜像的镜像元数据
没有声明非 root 用户，可以同时配置：

```dotenv
RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_USER=<镜像内有效的非 root UID>
RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_GROUP=<镜像内有效的非 root GID>
```

UID 和 GID 必须成对配置，且应从所选镜像本身确认，不能把一个镜像的数值直接套用到另一个镜像。

### 5.3 对外访问

服务器模板默认监听 `0.0.0.0:8090`，方便通过受控网络、反向代理或端口转发访问。当前应用用于
能力验证，没有内置业务身份认证；共享环境应通过防火墙、内网策略、反向代理认证等方式限制
访问，尤其是 Kubernetes 写接口。

## 6. 在 Swagger UI 中验证接口

打开 `/docs`，展开接口，点击 **Try it out**，粘贴对应请求体并点击 **Execute**。以下请求均为
完整、可直接执行的 Envelope 示例。

### 6.1 写数据库：`POST /api/db/write`

```json
{
  "type": "db/write",
  "metadata": {
    "request_id": "db-write-1"
  },
  "rawdata": {
    "id": "record-1",
    "value": "hello database"
  },
  "version": "1"
}
```

首次写入返回 `operation: "created"`；用相同 `id` 再次写入会更新记录并返回
`operation: "updated"`。

### 6.2 读数据库：`POST /api/db/read`

```json
{
  "type": "db/read",
  "metadata": {
    "request_id": "db-read-1"
  },
  "rawdata": {
    "id": "record-1"
  },
  "version": "1"
}
```

存在时返回 `id`、`value`、`updated_at`；记录不存在时返回 HTTP 404 和统一错误信封。

### 6.3 写 Redis：`POST /api/redis/write`

```json
{
  "type": "redis/write",
  "metadata": {
    "request_id": "redis-write-1"
  },
  "rawdata": {
    "key": "message-1",
    "value": "hello redis",
    "ttl_seconds": 300
  },
  "version": "1"
}
```

`ttl_seconds` 可省略；省略时使用
`RUNTIME_CAPABILITIES_REDIS_DEFAULT_TTL_SECONDS`。返回中的 `backend_mode` 可用于确认当前访问的
是 `fake` 还是真实 `real` 后端。

### 6.4 读 Redis：`POST /api/redis/read`

```json
{
  "type": "redis/read",
  "metadata": {
    "request_id": "redis-read-1"
  },
  "rawdata": {
    "key": "message-1"
  },
  "version": "1"
}
```

存在时返回 `found: true` 和 `value`；不存在时仍返回 HTTP 200，但 `found` 为 `false`、`value`
为 `null`。

### 6.5 解析 Envelope：`POST /api/envelope/inspect`

```json
{
  "type": "envelope/inspect",
  "metadata": {
    "request_id": "inspect-1",
    "user_id": "user-001",
    "chat_id": "chat-001",
    "session_id": "session-001",
    "bot_id": "bot-001",
    "channel": "swagger",
    "trace_id": "trace-001",
    "instance_id": "instance-001",
    "extra": {
      "tenant": "example-tenant",
      "source": "manual-test"
    }
  },
  "rawdata": {
    "message": "inspect this envelope",
    "attributes": {
      "environment": "server"
    }
  },
  "version": "1"
}
```

响应分为三部分：

- `envelope`：框架解析后的完整 Envelope；
- `context`：框架从 Metadata 建立的请求上下文字段，以及当前副本 `replica_id`；
- `service`：应用模式和 Redis/Kubernetes 后端模式。

### 6.6 创建 Pod：`POST /api/k8s/pod/create`

```json
{
  "type": "k8s/pod/create",
  "metadata": {
    "request_id": "pod-create-1"
  },
  "rawdata": {
    "name": "capability-pod-1"
  },
  "version": "1"
}
```

镜像、namespace 和安全上下文由服务端配置决定，调用方只能指定符合 DNS-1123 的 Pod 名称。
同名且由本应用管理的 Pod 已存在时返回 HTTP 409。

### 6.7 查询 Pod：`POST /api/k8s/pod/read`

```json
{
  "type": "k8s/pod/read",
  "metadata": {
    "request_id": "pod-read-1"
  },
  "rawdata": {
    "name": "capability-pod-1"
  },
  "version": "1"
}
```

返回 `name`、`namespace`、`phase`、`ready` 和 `image`。真实集群中，创建接口刚返回时 Pod
可能处于 `Pending`，镜像拉取并通过就绪检查后再查询会看到 `Running` 和 `ready: true`。

### 6.8 删除 Pod：`POST /api/k8s/pod/delete`

```json
{
  "type": "k8s/pod/delete",
  "metadata": {
    "request_id": "pod-delete-1"
  },
  "rawdata": {
    "name": "capability-pod-1"
  },
  "version": "1"
}
```

`state` 可能为：

- `delete_requested`：已向 Kubernetes 提交删除；
- `deletion_in_progress`：Pod 已处于终止过程；
- `already_absent`：Pod 已不存在。

## 7. 自动化测试

在仓库根目录执行：

```bash
uv sync --project applications/runtime_capabilities --all-extras
uv run --project applications/runtime_capabilities pytest -q \
  applications/runtime_capabilities/tests
```

只运行配置等单元测试：

```bash
uv run --project applications/runtime_capabilities pytest -q -m unit \
  applications/runtime_capabilities/tests
```

只运行通过 ASGI/HTTP 验证全部接口、错误码和 OpenAPI 的系统测试：

```bash
uv run --project applications/runtime_capabilities pytest -q -m system \
  applications/runtime_capabilities/tests
```

自动化测试不会连接真实 MySQL、Redis 或 Kubernetes。真实资源验证应使用 `server` 模式，并在
受控 namespace 和专用测试数据范围内按第 6 节顺序执行。

## 8. 配置参考

### 应用配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RUNTIME_CAPABILITIES_MODE` | `local` | `local` 或 `server`；脚本会按启动参数设置 |
| `RUNTIME_CAPABILITIES_SERVICE_LABEL` | `runtime-capabilities` | Envelope 检查响应中的服务标识 |
| `RUNTIME_CAPABILITIES_REDIS_DEFAULT_TTL_SECONDS` | `300` | Redis 写接口的默认 TTL，必须为正整数 |
| `RUNTIME_CAPABILITIES_KUBERNETES_NAMESPACE` | `runtime-capabilities` | 允许管理 Pod 的 namespace |
| `RUNTIME_CAPABILITIES_KUBERNETES_POD_IMAGE` | 见模板 | 创建 Pod 使用的固定镜像 |
| `RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_USER` | 未设置 | 可选非 root UID，必须与 GID 成对配置 |
| `RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_GROUP` | 未设置 | 可选非 root GID，必须与 UID 成对配置 |
| `RUNTIME_CAPABILITIES_SQLITE_PATH` | `./runtime_capabilities.db` | 仅本地模式使用的 SQLite 文件 |

### 框架配置

网络、数据库、Redis、锁和缓存使用 `OPENJIUWEN_SERVICE_*` 配置。完整定义以
`service/openjiuwen_runtime/service/config.py` 为准；本应用最常用的配置已列在两个环境模板中。

实际密码、token、证书和 kubeconfig 不应写入 README、模板或 Git，应只保存在受控的本地环境
文件或部署平台 Secret 中。
