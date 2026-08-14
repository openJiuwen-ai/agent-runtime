# Service 简单能力 Demo

该应用通过八个 HTTP 接口展示数据库读写、Redis 读写、Envelope 解析和 Kubernetes Pod 生命周期
能力。本地配置使用 SQLite、FakeRedis 和进程内 Fake Kubernetes。服务器配置使用 MySQL、Redis 和
`kubernetes_asyncio`。两种配置共用 Handler、请求契约和接口路径。

## 接口

| 方法与路径 | Envelope `type` | 功能 |
| --- | --- | --- |
| `POST /api/db/write` | `db/write` | 按 ID 创建或更新数据库记录 |
| `POST /api/db/read` | `db/read` | 按 ID 读取数据库记录 |
| `POST /api/redis/write` | `redis/write` | 写入带 TTL 的 Redis 字符串 |
| `POST /api/redis/read` | `redis/read` | 读取 Redis 字符串 |
| `POST /api/envelope/inspect` | `envelope/inspect` | 返回 Envelope、Metadata 和 RequestContext 解析结果 |
| `POST /api/k8s/pod/read` | `k8s/pod/read` | 查询 Demo 管理的 Pod |
| `POST /api/k8s/pod/create` | `k8s/pod/create` | 使用固定模板创建 Pod |
| `POST /api/k8s/pod/delete` | `k8s/pod/delete` | 删除 Demo 管理的 Pod |

Swagger UI 位于 `/docs`。OpenAPI 文档位于 `/openapi.json`。

## 本地启动

在 `service` 目录执行：

```powershell
uv sync
Copy-Item `
    -LiteralPath ".\examples\simple_capabilities.local.env.example" `
    -Destination ".\examples\.env.development.local"
uv run uvicorn examples.simple_capabilities_app:asgi `
    --host 127.0.0.1 `
    --port 8090 `
    --workers 1 `
    --env-file examples/.env.development.local
```

浏览器访问 <http://127.0.0.1:8090/docs>。本地 SQLite 数据保存在
`service/simple_capabilities_demo.db`。FakeRedis 和 Fake Kubernetes 数据保存在当前 Python 进程中，
服务重启后清空。

## Swagger 调试

所有接口接收完整 Envelope。`metadata.request_id` 为必填字段。请求路径与 `type` 保持一致。

### 写数据库

```json
{
  "type": "db/write",
  "metadata": {"request_id": "db-write-1"},
  "rawdata": {
    "id": "record-1",
    "value": "hello database"
  },
  "version": "1"
}
```

首次写入返回 `operation=created`。使用新 `request_id` 再次写入同一 ID 返回
`operation=updated`。

### 读数据库

```json
{
  "type": "db/read",
  "metadata": {"request_id": "db-read-1"},
  "rawdata": {"id": "record-1"},
  "version": "1"
}
```

缺失记录返回 HTTP `404` 和 `error_code=not_found`。

### 写 Redis

```json
{
  "type": "redis/write",
  "metadata": {"request_id": "redis-write-1"},
  "rawdata": {
    "key": "message-1",
    "value": "hello redis",
    "ttl_seconds": 300
  },
  "version": "1"
}
```

省略 `ttl_seconds` 时使用 `DEMO_REDIS_DEFAULT_TTL_SECONDS`。实际 Redis key 为
`{OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX}:kv:{key}`。

### 读 Redis

```json
{
  "type": "redis/read",
  "metadata": {"request_id": "redis-read-1"},
  "rawdata": {"key": "message-1"},
  "version": "1"
}
```

缺失 key 返回 HTTP `200`、`found=false` 和 `value=null`。

### 解析 Envelope

```json
{
  "type": "envelope/inspect",
  "metadata": {
    "request_id": "inspect-1",
    "user_id": "demo-user",
    "chat_id": "demo-chat",
    "session_id": "demo-session",
    "bot_id": "demo-bot",
    "channel": "swagger",
    "timestamp": 1786500000,
    "trace_id": "trace-1",
    "instance_id": "demo-instance",
    "extra": {"tenant": "demo"}
  },
  "rawdata": {
    "message": "inspect this envelope",
    "attributes": {"source": "swagger"}
  },
  "version": "1"
}
```

响应中的 `envelope` 来自框架解析后的 Envelope，`context` 来自当前 RequestContext，`service`
包含当前 Demo 环境标识、服务标签和 Redis 模式。

### 创建 Pod

```json
{
  "type": "k8s/pod/create",
  "metadata": {"request_id": "pod-create-1"},
  "rawdata": {"name": "capability-pod-1"},
  "version": "1"
}
```

服务使用 `DEMO_KUBERNETES_POD_IMAGE` 和固定安全模板创建 Pod。请求体只接收符合 Kubernetes
DNS-1123 subdomain 规则的名称。同名 Pod 返回 HTTP `409` 和 `error_code=conflict`。

### 查询 Pod

```json
{
  "type": "k8s/pod/read",
  "metadata": {"request_id": "pod-read-1"},
  "rawdata": {"name": "capability-pod-1"},
  "version": "1"
}
```

响应包含 `name`、`namespace`、`phase`、`ready` 和 `image`。Pod 缺失或管理标签不匹配时返回 HTTP
`404` 和 `error_code=not_found`。

### 删除 Pod

```json
{
  "type": "k8s/pod/delete",
  "metadata": {"request_id": "pod-delete-1"},
  "rawdata": {"name": "capability-pod-1"},
  "version": "1"
}
```

删除结果的 `state` 为 `delete_requested`、`deletion_in_progress` 或 `already_absent`。Real 模式使用
读取结果中的 Pod UID 提交删除前置条件。

## 配置

Demo 自有变量：

| 变量 | 说明 |
| --- | --- |
| `DEMO_ENVIRONMENT` | 运行环境标识 |
| `DEMO_SERVICE_LABEL` | 服务实例标签 |
| `DEMO_REDIS_MODE` | `fake` 或 `real`，必须显式配置 |
| `DEMO_REDIS_DEFAULT_TTL_SECONDS` | Redis 默认 TTL，取正整数 |
| `DEMO_KUBERNETES_MODE` | `fake` 或 `real`，必须显式配置 |
| `DEMO_KUBERNETES_NAMESPACE` | Pod 操作使用的固定 namespace，符合 DNS-1123 label 规则 |
| `DEMO_KUBERNETES_POD_IMAGE` | Pod 固定模板使用的镜像 |

`fake` 模式要求 `OPENJIUWEN_SERVICE_REDIS_URL=disabled` 或 `none`。`real` 模式要求有效的
`redis://`、`rediss://` 或 `unix://` URL。数据库、锁、缓存和 Redis 命名空间沿用
`OPENJIUWEN_SERVICE_*` 配置。Real Kubernetes 模式优先读取 in-cluster 配置，随后读取 `KUBECONFIG`
或默认 kubeconfig。

## 服务器部署

从模板生成实际配置文件：

```powershell
Copy-Item `
    -LiteralPath ".\examples\simple_capabilities.server.env.example" `
    -Destination ".\examples\.env.production.local"
```

编辑实际配置文件中的 MySQL 地址、数据库名、账号、密码、Redis URL、Kubernetes namespace 和镜像。
MySQL 账号需要具备连接、建表和 CRUD 权限。安装 Real Kubernetes 后端并启动服务：

```powershell
uv sync --extra kubernetes
uv run --extra kubernetes uvicorn examples.simple_capabilities_app:asgi `
    --host 0.0.0.0 `
    --port 8090 `
    --workers 1 `
    --env-file examples/.env.production.local
```

Nginx 或 API Gateway 提供 HTTPS、访问来源限制和请求日志。应用启动时连接数据库与 Redis、创建
`simple_capability_records` 表并执行资源探活。Kubernetes 探活通过 namespace Pod list 验证连接和
权限。资源连接或配置校验失败会终止启动。

Kubernetes ServiceAccount 在目标 namespace 中需要以下 Pod 权限：

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: simple-capabilities-demo-pods
  namespace: simple-capabilities-demo
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "create", "delete"]
```

RoleBinding 将该 Role 绑定到运行 Demo 服务的 ServiceAccount。查询和删除操作要求 Pod 包含
`app.kubernetes.io/managed-by=openjiuwen-service-demo` 标签。

## 测试

在 `service` 目录执行专项测试：

```powershell
uv run pytest `
    tests/unit_tests/test_kubernetes_operations.py `
    tests/unit_tests/test_system_context.py `
    tests/unit_tests/test_config_bootstrap.py `
    tests/unit_tests/test_simple_capabilities_example.py `
    -q -p no:cacheprovider
```

执行包含 Real 适配器的专项测试：

```powershell
uv run --extra kubernetes pytest `
    tests/unit_tests/test_kubernetes_operations.py `
    -q -p no:cacheprovider
```

执行 Service 全量测试：

```powershell
uv run pytest tests -q -p no:cacheprovider
```
