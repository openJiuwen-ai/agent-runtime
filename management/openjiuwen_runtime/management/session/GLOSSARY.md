# 术语表（Session SDK）

本文件是 Session SDK 各名词的**权威定义**，消除"session"一词的多义混淆。

---

## 0. 核心澄清：三种 "session"

历史上 "session" 在本项目中指代三种**完全不同**的东西，是所有命名混淆的根源：

| 含义 | 实际是什么 | 标识 | 归属 |
|------|-----------|------|------|
| **Page session** | 用户的一次浏览器会话（对话） | `sess_*`（如 `sess_19f02e1...`） | **gateway** 侧，SDK 不感知 |
| **ServiceScope** | (group, bot) 资源分配域——同 group 同 bot 的所有用户共享 | `service_id` = md5(group+bot) | **SDK** 核心概念 |
| **会话状态** | 对话上下文（记忆/历史） | AgentServer 进程内或外置存储 | **AgentServer**，SDK 不持有 |

> **关键**：SDK 层**没有"用户会话"概念**。SDK 管理的是 ServiceScope（Pod 资源域），不是用户对话。对话连续性由 AgentServer 外置状态保证。

---

## 1. ServiceScope 体系（SDK 核心）

### ServiceScope
以 **(group_id, bot_id)** 为键的**资源分配与路由域**。同一 group 内所有用户与同一 bot 的交互共享一个 ServiceScope。用于：查找 Pod 模版（`service_config_template`）、分配/管理一组 Pod、在这一组 Pod 间路由请求。

- **不是**：某个用户的某次对话
- **是**：一个 bot 在一个 group 里的 Pod 资源单元

### `service_id`
ServiceScope 的唯一标识 = `md5(group_id + bot_id)`。在整个 SDK 中用作 registry 键、计时器键、路由标识。

> **历史**：曾叫 `session_id`，因与 page session 同名造成混淆，已改名。gateway 的 `_SessionRequest` 内部早已用 `self._service_id` 存储。

### ServiceScopeHandler
一个 ServiceScope 的运行时处理器（`service_scope_handler.py`）。持有多个 Pod endpoint，做：
- 并发限流（semaphore，上限 = `scope_concurrency`）
- 容量感知路由（用户亲和 → 最少负载）
- 弹性扩缩 endpoint（multi-pod）

> **历史**：曾叫 `SessionHandler`。

### ServiceScopeRegistry
`service_id → ServiceScopeHandler` 的归属表（在 `SessionRuntimeManager` 内）。管理 ServiceScopeHandler 的创建、查找、移除。

> **历史**：曾叫 `SessionRegistry`。

---

## 2. 三层架构

```
Access (入口)
  ↓
SessionRuntimeManager (编排层)     ← 1 per 进程
  ├── ServiceScopeRegistry         ← service_id → ServiceScopeHandler
  ├── handle_user_request          ← 消息入口、TTL、pending 过期
  └── 借/还 Pod ←→ ServiceManager
        ↓
ServiceScopeHandler (域层)         ← 1 per (group,bot)
  ├── semaphore(scope_concurrency)
  ├── _pick_endpoint (用户亲和→最少负载)
  └── List[ISendEndpoint]
        ↓ send_message
ServiceHandler (Pod 层)            ← 1 per Pod
  ├── quota (_session_reserved)
  ├── inflight / WebSocket channel
  └── deploy / delete / evict_session
```

### ServiceManager
**Pod 池管理者**（`service_manager.py`）。管理 Pod 生命周期：deploy/delete、autoscale（维护 min_idle 热备）、idle/reclaim（老化回收）、监控失效 Pod。向编排层暴露 `pick_or_create_pod` / `find_service_handler` / `reconsider_idle_transition`。

### SessionRuntimeManager
**Session 编排层**（`session_runtime_manager.py`）。职责：消息入口（handle_user_request）、ServiceScope TTL/pending 过期、向 ServiceManager 借还 Pod。内含 `ServiceScopeRegistry`。

> **命名说明**：叫 "SessionRuntime" 而非 "ServiceScopeRuntime" 是因为它编排的是**运行时生命周期**（TTL/路由/pending），与 gateway 侧已有的 `SessionManager`（任务队列）同名但不同层。

### ServiceHandler
**单个 Pod 的管理者**（`service_handler.py`）。同时实现 `IServiceHandler`（Pod 池视角：quota/lifecycle）和 `ISendEndpoint`（发送视角：send_message/inflight/endpoint_id）。每个 ServiceHandler 对应一个 AgentServer Pod，持有一条 WebSocket 连接。

### ISendEndpoint
**发送端点接口**（`interfaces.py`）。ServiceScopeHandler 通过此接口向 Pod 发请求，不绑定 ServiceHandler 类型。属性：`endpoint_id`、`inflight`、`send_message`。由 ServiceHandler 实现。

---

## 3. 并发与 TTL（DB 列名 ↔ 概念名对照）

> **重要**：以下四个值同时是 Python 字段名和 `service_config_template` **DB 列名**。为兼容现网 DB，DB 列名保留旧称，概念名对称改名仅在文档/讨论中使用。

| 概念名（本文档） | DB 列名（代码实际字段） | 含义 | 默认 |
|----------------|---------------------|------|------|
| **pod_concurrency** | `service_concurrency` | 单 Pod 最大并发容量 | 10 |
| **scope_concurrency** | `session_concurrency` | 单 ServiceScope 总并发预算（跨所有 Pod） | 100 |
| **pod_ttl** | `service_ttl` | Pod 无业务后转 idle 池的等待秒数 | 180 |
| **scope_ttl** | `session_ttl` | ServiceScope 保活窗口（idle 后多久回收 Pod） | 60 |

**代码中统一用 DB 列名**（`service_concurrency` / `session_concurrency` / `service_ttl` / `session_ttl`），本文档用概念名便于理解层级关系。

### min_idle / max_services
- `min_idle`（`min_idle_services`）：全局 idle 池至少保持的**热备** Pod 数（autoscale 自动补位）。新请求到达时唤醒热备，避免冷启动。
- `max_services`：Pod 总数上限。

### reserve_per_pod
每个业务 Pod 从 ServiceScope 抽取的容量 = `min(scope_concurrency, pod_concurrency)`。multi-pod 弹性扩缩的计量单位。

### max_business
一个 ServiceScope 的最大业务 Pod 数 = `ceil(scope_concurrency / reserve_per_pod)`。

---

## 4. 请求与消息类型

### IRequest
**Gateway 入口请求接口**（`interfaces.py`）。携带 page session 信息：`request_id`、`chat_id`、`bot_id`、`user_id`、`session_id`（**page session**）。

### ISessionRequest
**SDK 内部的 scope 请求接口**（`interfaces.py`）。由策略从 `IRequest` 聚合而成，携带：`service_id`（scope 键）、`session_concurrency`、`session_ttl`、`request_id`、`raw_msg`（原始 IRequest）、`service_template`。

> **关键区分**：`IRequest.session_id`（page session）≠ `ISessionRequest.service_id`（scope 键）。

### ScopeRequestWrapper
封装 `ISessionRequest` + `response_queue` + `cancel future` 的传输包装，贯穿 SDK 管道。

> **历史**：曾叫 `SessionRequestWrapper`。

### SessionConfig
Gateway 传入的 scope 配置（`models.py`）：`concurrency`（→ scope_concurrency）和 `ttl`（→ scope_ttl）。`Access.init` 的参数，属 gateway↔SDK 契约。

---

## 5. Pod 生命周期

| 术语 | 含义 |
|------|------|
| **in_use 池** | 正在服务请求的 Pod（有 session 预留额度） |
| **idle 池** | 空闲热备 Pod（无 session，可被唤醒） |
| **deploy** | 创建新 Pod（K8s 创建 Pod + WebSocket 建链） |
| **pick_or_create_pod** | 编排层借 Pod：找有容量的 in_use → 唤醒 idle → 新 deploy |
| **try_reserve_session_quota** | 为 ServiceScope 在某 Pod 上预留额度（隔离） |
| **evict_session** | 释放某 Pod 上该 ServiceScope 的额度 + 取消 pod-local 在途请求 |
| **reconsider_idle_transition** | evict 后推动 Pod 重新评估 in_use→idle（老化推进） |
| **autoscale** | 周期维护 min_idle 热备 + 回收多余 idle |
| **scope TTL** | ServiceScope idle 后回收 Pod 的计时器 |

---

## 6. Gateway 侧概念（不属于 SDK，但易混淆）

| 术语 | 含义 |
|------|------|
| **page session** (`sess_*`) | 用户的一次浏览器会话，gateway 的 SessionMap 管理 |
| **SessionMapScope** | 会话映射策略（`per_chat_bot` / `per_chat_bot_user`） |
| **service_config_template** | DB 表，按 (group, bot) 配置 Pod 模版（镜像、资源、并发、TTL） |
| **AgentServer** | AI Agent 运行时进程（每个 Pod 一个），执行 LLM/Skill/Tool |
| **Gateway** | 消息路由层，连接 IM 平台与 AgentServer |

---

## 7. 名称变更历史

| 旧名 | 新名 | 改名原因 |
|------|------|---------|
| `SessionHandler` | `ServiceScopeHandler` | 它管的是 (group,bot) 资源域，不是用户会话 |
| `SessionRegistry` | `ServiceScopeRegistry` | 同上 |
| `ISessionHandler` | `IServiceScopeHandler` | 接口对齐 |
| `SessionRequestWrapper` | `ScopeRequestWrapper` | 同上 |
| `session_id`（SDK 内） | `service_id` | 与 page session 同名混淆；值本就是 service_id |
| `_arm_session_timer` | `_arm_scope_timer` | 方法名对齐 |
| `_on_session_expired` | `_on_scope_expired` | 同上 |

**未改名（保留）**：
- `ISessionRequest` / `SessionConfig` / `SessionRequest`：gateway 面向契约，改名需跨模块同步。
- `service_concurrency` / `session_concurrency` / `service_ttl` / `session_ttl`：`service_config_template` DB 列名 + 配置协议，改名需 DB 迁移。
- `IRequest.session_id`：page session，gateway 概念，不属 SDK。

---

*本文档随代码演进同步更新。如有疑问，以代码实际命名为准。*
