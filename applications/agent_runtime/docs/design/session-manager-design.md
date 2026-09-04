# Session Manager 详细设计

- 日期:2026-08-05
- 范围:`agent-runtime/` 内Session Manager 模块(合并服务内,含并入的原 Config Manager 职责)
- 状态:**draft,待评审**
- 关系:实现 bypass 设计 `2026-07-29-agent-runtime-microservices-bypass-design.md` 的 §3(Session Manager);**并入其 §2(Config Manager)**;依赖其 §4(Resource Manager)——二者为同一服务内的两个模块(同进程、同 App)。与 bypass 的偏差见 §0.3。

### 术语(全称,不缩写)

- **`scope_id`** = `md5(group_id + bot_id)`,即 **Scope 标识**(对应 SDK 的 `ServiceScope`,其字段名为 `service_id`;见 SDK `GLOSSARY.md` §1):一个 (group_id, bot_id) 资源域的唯一键。本文档统一用 `scope_id`。
- **`pod_id`** = Pod 实例标识。Resource Manager `acquire` 返回(其契约字段名为 `endpoint_id`);对应 SDK 的 `ServiceHandler.id`。本文档统一用 `pod_id`。
- **`session_id`** = chat_session 标识(路由/亲和/TTL 最小单位,来自 `Envelope.metadata.session_id`)。
- **`template_id`** = `service_config_template` 主键之一,resolve 匹配得出。
- **`max_pods`** = `ceil(scope_concurrency / pod_concurrency)`(一个 scope 最多需要的 Pod 数;pod 满载即 `pod_concurrency`,scope 超过则扩 Pod)。

---

## 0. 背景与定位

### 0.1 现状

`agent-runtime/management/openjiuwen_runtime/management/session/` 当前是**进程内 Python SDK**(`Access`):gateway `import` 后调 `Access.send_message()`,消息**走直路**——用户 → gateway →(WebSocket)→ AgentServer Pod,响应沿原路流式回传。SDK 同时持有数据面(WSS 通道、response_queue)与控制面(路由 / 准入 / TTL / Pod 池),重度依赖进程内可变状态(`asyncio.Semaphore`、进程内 Timer、idle/in_use 池)。SDK 跑在 gateway 主进程(主备单活)。

### 0.2 目标

把「会话运行时编排」做成**合并服务内的分布式模块**(与 Resource Manager 同进程、共享 Redis;基于已实现的 `openjiuwen_runtime.service` 框架),并满足 bypass:

- **RESTful 接口**,unary `POST`。
- **不在数据通路上**:拉起 Pod 后把 SSE 端点交给 gateway,gateway 与 Pod 直连;Session Manager 仅在**旁路**感知消息首发/收发(`route` / `touch`),据此做资源管理(准入 / 老化 / 回收)。
- **多实例无状态**:所有 runtime 编排态在**共享 Redis**(与 Resource Manager **同一实例**,仅各自 `key_prefix`=`session_manager`/`resource_manager` 不同;部署见 §11);水平扩展=加副本。

### 0.3 与 bypass 设计的偏差(经评审确认)

| 项 | bypass 原案 | 本设计 | 理由 |
|---|---|---|---|
| Config Manager | 独立服务(§2,端口 8093,prefix `/api/config`) | **并入 Session Manager** | 用户决策:少一个服务、resolve 少一跳 |
| config 存储 | 共享 Redis | **共享 DB**(foundation `DBHandler`) | 复用 EE 已验证的 `service_config_template.py` 持久化逻辑;config 是持久 system-of-record,落 DB 更稳 |
| config_sync 接口 | `POST /api/config/config_sync` | `POST /api/session/config_sync`(并入 `/api/session` 前缀) | 单 App 单前缀(框架 `App` 一次一个 prefix) |

> Resource Manager 已与 Session Manager **合并为同一服务**(同进程内部模块),二者经**进程内 Facade** 互调;交互契约见 §13.1。

### 0.4 框架扩展前提(经评审确认)

服务框架的 `RequestContext` 原本不暴露原始 redis client(仅 `kv/lock/pubsub/idempotency/queue/db/transaction` 原语,且 `KVStore` 只支持 string)。本设计的 runtime 状态模型需要 ZSET / HASH / SET / Lua,原语不支持。**结论(用户决策):扩框架**——给 `RequestContext` 增加 `ctx.redis`(返回原始 `redis.asyncio` client),作为 sanctioned 访问点;简单 KV 需求仍走 `ctx.kv`。框架改动仅此一处,sweeper / peer client 的生命周期经 `SystemContext` 子类注入,不改 `App`。

### 0.5 移植策略

**新建 + 移植逻辑(非类)**:在框架 + Redis 上新建 handler,从现有 SDK 移植**逻辑与常量**(`max_pods`、first-fit 选 Pod、TTL 规则、错误码),从 EE 移植 **template 持久化 + config_sync op 语义**。

**弃用**(与无状态模型 / 旁路定位不兼容):
- 数据面:`WSServiceMessageChannel`、`ServiceHandler` 的通道部分、`response_queue`、`ScopeRequestWrapper`、`dual_queue`、`IResponseParser`、`dispatch_inbound_chunk`。
- 进程内并发/计时:`asyncio.Semaphore`、进程内 `asyncio.Timer`、`ServiceHandler`/`SessionHandler` 的进程内信号量。

---

## 1. 架构与拓扑

### 1.1 合并服务拓扑(单服务两模块同进程)

```
配置面:  Claw Manager ──POST config_sync──► ┌──── 合并服务(一个 App,prefix=/api/session)────┐
                                             │  session_manager 模块(HTTP handler + SM Facade)  │
控制面:  Gateway ──POST route / touch──►     │  resource_manager 模块(无 App,纯 RM Facade)    │
         运维/HA ──POST cleanup──►          │                                                   │
                                             │  SM↔RM 全走 Facade 异步方法(进程内,无网络):     │
                                             │    SM.route ──rm_facade.acquire/idle_consider──► RM │
                                             │    RM 死Pod/reclaim ──sm_facade.notify_pod_dead──► SM│
                                             │    RM 孤儿对账 sweeper ──sm_facade.reconcile_pods──► SM│
                                             │  共享 Redis(key_prefix: session_manager / resource_manager)│
                                             │  + 共享 DB(service_config_template 表)            │
                                             └──────────────┬──────────────────┘
                                                            ▼ deploy
                                                           Pod

数据面(合并服务全程旁路,gateway ↔ AgentServer Pod 直连):
         Gateway ──POST 消息体──► Pod(pod_sse_url 由 route 返回)
         Gateway ◄──text/event-stream──┘

旁路老化:session_manager 后台 sweeper ──扫 Redis──► evict ──(Pod 空)──► rm_facade.idle_consider
```

### 1.2 服务画像(合并后)

| | 合并服务 |
|---|---|
| 进程/App | **一个**进程、**一个** App、**一个**端口(默认 8091)、prefix `/api/session` |
| 模块 | `session_manager/`(注册 HTTP handler + `SessionManagerFacade`)+ `resource_manager/`(**无 App**,纯内部 + `ResourceManagerFacade`) |
| SystemContext | **两个**:`sm_sysctx`(`key_prefix=session_manager`)、`rm_sysctx`(`key_prefix=resource_manager`),指向**同一 Redis + 同一 DB** |
| 形态 | 无状态多副本 + 共享 Redis + 共享 DB |
| 职责 | 控制面:准入、路由亲和、TTL 老化、config 存储与解析(session_manager)+ Pod 生命周期:deploy / idle→reclaim / min_idle 热备 / per-`scope_id` 独立 Pod 池 / 死Pod探测 / cleanup(resource_manager) |
| 关键路径 | `route`(每请求,内部走 `rm_facade.acquire` 偶发扩容) |

---

## 2. 对外接口

这是**合并服务的唯一 App**(同一进程同时承载 session_manager 与 resource_manager 两模块;resource_manager 无 App、不注册 HTTP handler)。`App(ctx_factory, prefix="/api/session")`,默认端口 **8091**。全部 unary `POST /api/session/{type}`,body = 框架 `Envelope`,`metadata.request_id` 兼作幂等键。路由上下文 `session_id` / `user_id` / `bot_id` 复用 `Envelope.metadata` 标准字段;**`group_id` 走 `metadata.extra["group_id"]`**(框架 `Metadata` 无此字段,见 `envelope.py`,最小改动不扩框架)。

### 2.1 `POST /api/session/route` —— 同步路由 + 占额度(关键路径)
- **in**:路由上下文 `(session_id, user_id, group_id, bot_id)` 经 `metadata`/`metadata.extra`;rawdata 无业务字段
- **out**:`{ pod_sse_url:str, pod_id:str }`
- **错**:`SCOPE_FULL`(503,容量满快失败,2026-09 起)、`NO_POD_AVAILABLE`(503)、`CONFIG_NOT_FOUND`(503)、`VALIDATION`(400)

### 2.2 `POST /api/session/touch` —— 保活 / EOS(刷新 TTL)
- **in**:`session_id` 经 `metadata`
- **out**:`{ touched:bool }`(`false`=会话已过期/不存在,gateway 应回退重新 `route`)
- **用途**:① 保活:gateway 对仍打开的页面会话周期性调用,无新消息时阻止老化;② EOS:response 末帧时调用,作为旁路感知会话活动的信号。**不改任何计数**。

### 2.3 notify_pod_dead —— RM 模块 → SM 模块(进程内 Facade)【进程内 Facade 方法,不再对外 HTTP】

> ⚠️ 此接口现为 `SessionManagerFacade.notify_pod_dead(pod_id)`,**不再对外 HTTP**,仅由 resource_manager 模块(死Pod / reclaim 路径)经进程内 Facade 调用;入参 / 出参 / 清注册语义不变。

- **in**:`{ pod_id:str }`(rawdata)
- **out**:`{ invalidated:[session_id,...] }`

### 2.4 `POST /api/session/config_sync` —— Claw Manager → Session(配置下发)
- **in**:`{ op:"create"|"update"|"delete"|"sync", kind:"template"|"routing_rule", template_id?|rule_id?, template?|updates?|templates? }`
  - `kind=template`:沿用 EE `apply_service_config_template` 的 op 语义(create/update/delete/sync)。
  - `kind=routing_rule`:resolve 匹配用(template_ref / service_policy),**部分新增**,精确 schema 留实现计划。
- **out**:`{ ok:bool, synced?:int, deleted?:int }`
- **副作用**(完整链路,详见 §4.3):写 DB → 与 DB 老行**逐字段 diff** → **A 类**(`deploy_ver` 变,deploy 子集变更):反查受影响 scope,把 `deploy_ver` 不匹配的 Pod `ZREM` 出 `scope:{scope_id}:pods` 候选集(软摘除,不接新流量;存量会话不受影响,继续老化),并推 RM 刷新 deploy 字段缓存;**B 类**(池参数/策略类):`DEL scope:{scope_id}:config` 失效(下次 `route` 重新 resolve)。**两类都经 `rm_facade.update_pool_config(scope_id, pool_config)` 推新池参数 → RM 立即生效**。语义权威定义见 HLD §6.2 场景 M「配置热更新」。

> `resolve` 不对外暴露,为 `route` 的进程内调用。

### 2.5 reconcile_pods —— RM 模块 → SM 模块(进程内 Facade)【进程内 Facade 方法,不再对外 HTTP】

> ⚠️ 此接口现为 `SessionManagerFacade.reconcile_pods(view)`,**不再对外 HTTP**,由 resource_manager 孤儿对账 sweeper 每 30s 经进程内 Facade 调用;只读 / 幂等 / 单向语义不变。

- **in**:`{ pods:[{ pod_id:str, scopes:[scope_id,...] }] }`(rawdata;resource_manager 当前持有的全部 Pod 视图,按 scope 分组)
- **out**:`{ stale:[{ pod_id:str, scope_id:str }] }`
- **语义**:resource_manager 模块周期性(默认 30s,复用其 `lock:rm:reconcile` 选主)经 Facade 调用,消除「RM 侧仍持有 Pod、SM 早已不用」的孤儿 Pod(成因:`idle_consider` 丢失 / SM 重启漂移)。SM 对入参每个 (pod_id, scope_id) 查 `scope:{scope_id}:pods` 成员资格(可只读 Lua 批量 `ZSCORE` 或 pipeline):**非成员 → stale**(SM 已 `idle_consider`/`notify_pod_dead`,不再 route 该 Pod)。RM 据 `stale` 逐个将 Pod 移入 `scope:idle` → 按 `pod_ttl` 回收。**只读、幂等、单向**(仅返回 stale,不反向重建)。详见 RM spec §5.4.2。

### 2.6 `POST /api/session/cleanup` —— 运维批删 AgentServer Pod
- **in**:`{ namespace?:str, label_selector?:str }`
- **out**:`{ cleaned:int }`
- **语义**:运维批量删除 K8s 上的 AgentServer Pod(灾难恢复 / 重新部署 / 清孤儿)。handler 注册在 session_manager,**委托** `ResourceManagerFacade.cleanup(namespace, label_selector)`(逻辑沿用 RM spec §2.3,handler 用 `rm_facade` 自持 `rm_sysctx`)。**仅运维可调**(mTLS + 调用方校验,见 §9.1)。

---

## 3. Redis 状态模型(runtime 态)

前缀取 `SystemContext.key_prefix`(Session Manager 建议设为 `session_manager`;框架默认 `service` 仅为字面量,与 scope 概念无关)。`scope_id = md5(group_id + bot_id)`。**所有计数派生自集合(SCARD),不另设计数器 → 无漂移、崩溃安全。**

| 键 | 类型 | 内容 | 作用 |
|---|---|---|---|
| `session_expiry` | ZSET | member=`session_id`,score=`expiry_ts` | 全局到期集合,sweeper 扫它 |
| `session:{session_id}` | HASH | `scope_id`,`pod_id`,`expiry`,`session_ttl` | 单会话亲和绑定(route 写入 `session_ttl`,TOUCH 就地读取,不依赖 scope:config) |
| `scope:{scope_id}:sessions` | SET | 该 scope 活跃 session_id | **SCARD = scope 活跃数 = scope_concurrency 闸门** |
| `scope:{scope_id}:pods` | **ZSET** | member=`pod_id`,score=`INCR scope:{scope_id}:pod_seq` | 该 scope 的 Pod 候选集;**ZSET 保接入顺序(first-fit 按序)**。sweeper 发 `idle_consider` 前**原子 `ZREM`**(`LUA_SWEEP_IDLE_NOTIFY`):该 Pod 即刻退出 first-fit 候选,堵 reclaim 窗口内 route 直选复用(§5.4 / 不变量 5) |
| `scope:{scope_id}:config` | HASH | `scope_concurrency`,`session_ttl`,`pod_concurrency`,`max_pods`,`template_id`,`ver`,`min_idle_pods`,`pod_ttl` | resolve 的 Redis 缓存(**唯一读者= resolve**,handler 侧);config_sync 主动 `DEL` 失效;无 TTL。`ver`=template 版本戳(观测用,不参与逻辑)。`min_idle_pods`/`pod_ttl` 为 template 池参数;`max_pods` = `max_pods`(派生)——三者随 acquire 的 `pool_config` 下发 RM;`autoscale_interval` 全局默认(不入 scope:config) |
| `scope:{scope_id}:pod_seq` | STRING | 单调递增计数 | ZSET score 来源 |
| `pod:{scope_id}:{pod_id}:sessions` | SET | 该(scope, Pod)上的 session_id | **SCARD < pod_concurrency = Pod 容量闸门** |
| `pod:{scope_id}:{pod_id}:info` | HASH | `sse_url`, `deploy_ver` | Pod 元信息(route 返回 sse_url);`deploy_ver` = 注册时记录的 deploy 子集 hash 指纹,config_sync 日落判定用(§4.3) |
| `pod:{scope_id}:{pod_id}:idle_notified` | STRING(`SET NX EX 60`) | 去重标记 | 空 Pod 通知 Resource 的 60s 去重;placement 时 `DEL` |
| `pods:registered` | SET | `"{scope_id}:{pod_id}"` 全量 | sweeper 空 Pod pass 枚举(全局) |
| `pods:{pod_id}:scopes` | SET | 该 Pod 被哪些 scope 引用 | notify_pod_dead 反查受影响 scope |
| `lock:sweep` | STRING(`SET NX EX 2`) | 选主标记 | sweeper tick 级选主(全局单副本扫描) |

**不变量(由 Lua 原子维护;崩溃/重启不破坏,因状态全在 Redis)**:
1. **session 四处一致**:一个活跃 session 同时存在于 `scope:{scope_id}:sessions`(SET)、`pod:{scope_id}:{pod_id}:sessions`(SET)、`session:{session_id}`(HASH)、`session_expiry`(ZSET)四处。`LUA_ROUTE_PLACE` 提交步骤同写四处,`LUA_EVICT` 同删四处。
2. `SCARD(scope:{scope_id}:sessions) == Σ SCARD(pod:{scope_id}:{pod_id}:sessions)`(每个 session 恰在一个 Pod 上)。
3. 计数派生自集合(SCARD),无独立计数器。
4. `ZCARD(scope:{scope_id}:pods) ≤ max_pods`;达上限后不再 `acquire`。
5. **`scope:{scope_id}:pods` ⊆ `pods:registered`(按 scope 切片)**:`register_pod` 同写三处(`pods:registered` / `scope:pods` / `pods:{pod_id}:scopes`),`notify_pod_dead` 同删三处;但 sweeper `LUA_SWEEP_IDLE_NOTIFY` 只 `ZREM scope:pods`(不删另两处)。故"在 `pods:registered` / `pods:{pod_id}:scopes` 但不在 `scope:pods`" = **已 `idle_consider`、待 Resource 回收的合法中间态**,非漂移。`scope:pods` 中的 Pod 必在 `pods:registered`(注册是入 `scope:pods` 的唯一路径)。

**键设计说明(为什么是这些结构)**:
- **计数用 SCARD 派生,不另设计数器**:独立计数器会在崩溃/部分失败时与集合漂移、需对账;SCARD 永远等于集合真实大小,无漂移、崩溃安全。代价是每次闸门判断做一次 O(1) SCARD,可接受。
- **`scope:{scope_id}:pods` 用 ZSET 而非 SET**:first-fit 要求"按 Pod 接入顺序"遍历取首个命中;SET 的 SMEMBERS 顺序不确定,会破坏 first-fit 的确定性缩容语义。ZSET 以 `pod_seq`(单调 INCR)为 score 保接入顺序。
- **`session_expiry` 是全局 ZSET(非 per-scope)**:sweeper 一次 `ZRANGEBYSCORE` 扫全库到期项,配合 tick 级选主锁,全局只有一个副本执行,无需按 scope 分片扫描。
- **`idle_notified` 是独立 STRING 键(非 `pod:info` 的 HASH 字段)**:去重需要 `SET NX EX 60` 的原子语义,HASH 字段无 NX+EX;独立键还能自然过期重试。
- **`pods:{pod_id}:scopes` 反查表**:**一 Pod 恰属一 scope**(per-`scope_id` 独立 Pod 池,无跨 scope 共享);`notify_pod_dead` 到来时据此反查受影响 scope 逐个清洗,故需 pod→scopes 反向索引。
- **不变量 3 的含义**:`max_pods = ceil(scope_concurrency / pod_concurrency)` 是单个 scope 的 Pod 数上限,防一个 scope 无限扩 Pod;达上限后总容量已 ≥ scope 预算,新请求立即 503 `SCOPE_FULL` 快失败(scope_full),不再 acquire。

**键的生命周期(谁建 / 谁读 / 谁删 / TTL)**:

| 键 | 创建 | 读取 | 删除 | TTL |
|---|---|---|---|---|
| `session_expiry` | ROUTE_PLACE 提交 `ZADD` | sweeper 到期 pass `ZRANGEBYSCORE` | EVICT `ZREM` | 无(随 session 删) |
| `session:{session_id}` | ROUTE_PLACE 提交 `HSET` | ROUTE_PLACE / TOUCH `HGETALL` | EVICT `DEL` | 无 |
| `scope:{scope_id}:sessions` | ROUTE_PLACE `SADD`(首次自动建) | ROUTE_PLACE 闸门 `SCARD` | EVICT `SREM` | 无 |
| `scope:{scope_id}:pods` | register_pod `ZADD` | ROUTE_PLACE first-fit `ZRANGE` / reconcile_pods `ZSCORE`(成员判定) | sweeper `LUA_SWEEP_IDLE_NOTIFY` 原子 `ZREM`(idle_consider 时);notify_pod_dead `ZREM` | 无 |
| `scope:{scope_id}:config` | resolve miss 时 `HSET` | resolve(route handler) | config_sync `DEL` | **无**(显式失效) |
| `scope:{scope_id}:pod_seq` | 首次 `INCR` 自动建 | register_pod `INCR` 取 score | 不删 | 无 |
| `pod:{scope_id}:{pod_id}:sessions` | ROUTE_PLACE `SADD` | ROUTE_PLACE `SCARD` | EVICT `SREM`;notify `DEL pod:*` | 无 |
| `pod:{scope_id}:{pod_id}:info` | register_pod `HSET` | route 读 `sse_url` | notify_pod_dead `DEL pod:*` | 无 |
| `pod:{scope_id}:{pod_id}:idle_notified` | sweeper `SET NX EX 60` | sweeper `SET NX` 判定 | ROUTE_PLACE 复用 `DEL`;notify `DEL pod:*` | **60s** |
| `pods:registered` | register_pod `SADD` | sweeper 空 Pod pass `SMEMBERS` | notify_pod_dead `SREM` | 无 |
| `pods:{pod_id}:scopes` | register_pod `SADD` | notify_pod_dead `SMEMBERS` | notify_pod_dead `SREM` | 无 |
| `lock:sweep` | sweeper `SET NX EX 2` | sweeper 判定 | 自动过期 / 下 tick 覆盖 | **2s** |
| route 幂等缓存 | `ctx.idempotency.put` | route `ctx.idempotency.get` | 框架按 window 过期 | **框架默认 60s** |

**无进程内状态(重要)**:Session Manager **不持有任何进程内可变状态**(框架硬约束 `system_context.py:11`)。所有上述键**懒创建**(首次 route / register_pod / resolve 时建,**无启动 bootstrap 预填**);**进程重启不丢状态**(全在 Redis+DB),重启后 sweeper 从 `session_expiry` 续扫、route 从 `scope:config` 或 DB 续接。进程级资源仅 `rm_facade`(`ResourceManagerFacade` 引用,进程内调 acquire/idle_consider)与 `sweeper_task`(asyncio task),由 `SystemContext` 子类在 `start()` 重建、`stop()` 释放,**不跨副本共享**(见 §0.4)。

> config 实体(template 行 + 路由规则)**不在 Redis**,在共享 DB(§4)。

---

## 4. Config 层(共享 DB + Redis 缓存)

### 4.1 存储(shared DB = config system-of-record)
- **template 实体**:共享 DB 表 `service_config_template`(沿用 EE `SERVICE_CONFIG_TEMPLATE_TABLE_DEF`)。
  - **主键**:`(jiuwenclaw_id, template_id)`(jiuwenclaw_id 保留为租户键)。
  - **关键列**(DB 列名与概念名同名,2026-09 起 wire 术语统一;曾用 EE 兼容名见 `docs/feature/2026-09-template-table-runtime-terms.md`):`scope_concurrency`、`pod_concurrency`、`session_ttl`、`pod_ttl`、`max_pods`(派生,即 `max_pods`)、`min_idle_pods`、`message_timeout`;deploy 子集 `pod_spec`:`agent_image`/`namespace`/`container_name`/`container_port`/`kubeconfig`/`readiness_*`/`nfs_*`/资源限额 + `pod_ttl`。完整列定义见 `_COLUMN_OF`(`config_store.py`)。
  - 近原样复用 `packages/jiuwenclaw-ee/.../template/service_config_template.py` 的 `_build_row_from_template` / `_upsert_service_config_template_from_sync` / `_sync_service_config_templates_records` / `delete_service_config_template`。
  - 改动点:原代码按 `(jiuwenclaw_id, template_id)` 存**每实例本地库**;改为**所有 Session Manager 副本 + Claw Manager 共享同一 DB**。
- **routing_rule(路由规则)**:resolve 的 `(group_id, bot_id, user_id) → template_id` 匹配依据。表结构完整定义留 follow-up(§13.3);**v1 最小实现** = 一张 `(jiuwenclaw_id, group_id, bot_id, template_id)` 映射表(忽略 user_id 精细策略),`config_sync kind=routing_rule` 增删改它。
- 访问:经框架 `SystemContext.db`(foundation `DBHandler` / `SQLAlchemyHandler`);`config_sync` 写、`resolve` 读,均走共享 DB。

### 4.2 resolve(进程内)
- `resolve(group_id, bot_id, user_id) -> template`:
  1. 查 `scope:{scope_id}:config` 缓存(Redis);命中 → 直接派生参数。
  2. miss → 读 DB:按路由规则(template_ref / service_policy)做 `(group_id, bot_id, user_id) → template_id` 策略匹配(逻辑从现有 gateway 路由移植,精确匹配算法留实现计划),取 template 行,派生 `scope_concurrency`/`session_ttl`/`pod_concurrency`/`max_pods`,写回 `scope:{scope_id}:config`。
  - 无匹配 → `CONFIG_NOT_FOUND`(503)。

### 4.3 config_sync

- 收到下发 → 按 op 写 DB → 找出引用该 template 的 scope(由 `scope:{scope_id}:config.template_id` 反查或 routing_rule 反查)→ 按下述流程失效/推送 → 下次 `route` 重新 resolve 拿新值。取代 bypass 的"短 TTL 最终一致",实现**立即一致**。

**完整处理流程**(总原则:**新值对增量生效;存量不驱逐、自然过渡**;权威定义见 HLD §6.2 场景 M「配置热更新」):

1. **按 op 写 DB**(create/update/delete/sync,op 语义沿用 EE `apply_service_config_template`)。
2. **diff 变更检测**:SM 手里同时有老值(DB 现行 template 行)与新值(下发 payload),**进程内逐字段 diff**(不在热路径上)。`deploy_ver` = A 类字段的 hash 指纹——新旧 `deploy_ver` 不等即 **A 类变更**(deploy 子集变),否则按字段归入 **B 类**(池参数/策略类);字段级 diff 供审计/决策明细。
3. **A 类处理(deploy 子集变更 → 日落老 Pod)**:反查受影响 scope → 把 `deploy_ver` 不匹配的 Pod **`ZREM` 出 `scope:{scope_id}:pods` 候选集**(软摘除,与 `idle_consider` 同款机制)——即刻退出 first-fit,**不再接任何新 session**;存量会话不受影响(亲和续期直读 session HASH,不查候选集),继续粘在老 Pod 直至老化 → 经 `update_pool_config` 附带 `pod_spec` deploy 字段推 RM 刷新缓存 → RM `acquire` 取暖 Pod 跳过 `deploy_ver` 不匹配的,autoscale 补位的新暖 Pod 用新 deploy 字段。
4. **B 类处理(池参数/策略变更,不日落)**:`DEL scope:{scope_id}:config`(下次 `route` 重新 resolve);老 Pod 原地继续服务。
5. **两类都推池参数**:经 `rm_facade.update_pool_config(scope_id, pool_config)` 把新池参数(`min_idle_pods`/`max_pods`/`pod_ttl`)主动推 RM(HSET 覆盖,幂等)→ RM 的 autoscale/reclaim **立即**用新值(§13.1 / RM spec §2.2.1)。
6. **生效语义表**(精简引用,权威见 HLD §6.2 场景 M):

| 配置类 | 例子 | 生效方式 |
|---|---|---|
| **A 类**(deploy 子集,除 `kubeconfig`) | `agent_image` / `namespace` / `container_name` / `container_port` / `sse_port` / `sse_path` / `readiness_*` / `nfs_*` / 资源限额 | 日落老 Pod(SM `ZREM` 软摘除 + RM acquire 版本过滤);存量不驱逐——镜像升级 = 自然滚动(老 Pod 排空老化回收)或运维 `cleanup` 批删重建 |
| **B 类** | `scope_concurrency` / `pod_concurrency` / `session_ttl` / `pod_ttl` / `min_idle_pods` / `max_pods` / `kubeconfig` | 不日落:`DEL scope:config` + `update_pool_config` 推 RM 立即生效;调小 → 存量不驱逐,自然回落 |

**串行化与并发控制**(多副本 + LB 下两次 config_sync 可能并发):①全程持分布式锁 `lock:config_sync`(SET NX EX);抢不到 → **409 `CONFIG_SYNC_BUSY`**(可重试,带 `Retry-After`),不排队。**`config_refresh` 与之共用此锁**(双向互斥)。②**上一次未完全完成前拒绝下一次**——完成 = 处理流程结束 + 受影响 scope 无"已日落待回收"的 Pod(判定:`pods:registered` 中属于该 scope、但已不在 `scope:pods` 候选集的 Pod 即中间态残留;全部回收后 `notify_pod_dead` 清注册)。③**写 DB 失败 → 立即中止,不得 DEL cache、不得推送**(防 cache 脏刷新)。详见 HLD 场景 M。

### 4.3b config_refresh 处理流程(强制刷新,场景 M-R)

**入口**:`POST /api/session/config_refresh`,**无载荷**(rawdata 非空 → 400);与 config_sync 共用 `lock:config_sync`(忙 → 409 `CONFIG_SYNC_BUSY`)。效果 = **不改任何配置**,全部存活 scope 的现有 Pod 优雅日落并按存量配置重建:

1. 枚举 DB 存活 scope(幻影 scope 归扩散③ drain 路径;模板缺失的悬挂 scope 跳过 + WARNING);
2. 每 scope 三步,**顺序红线 bump → ZREM**("ZREM 而未 bump"会造出永久蹲占 max_pods 的搁浅态):① `rm_facade.bump_generation(scope_id)`(严格,失败上抛);② `update_pool_config` 重推池参数 + pod_spec(值未变,确保 RM 缓存就绪;失败仅告警);③ 候选集全量 `ZREM` 软摘除(严格);
3. 不写 DB、不动路由快照;日落收敛与重建全部复用既有后台任务(存量会话亲和保持 → 空 Pod 转 idle → reclaim 代次感知回收 → autoscale 按缓存 pod_spec 重建,新 Pod 烙新代次);
4. **非幂等但收敛**(每次调用 = 一轮全量日落重建);config_sync 的日落中间态守卫**不扩展**看 generation(老代 Pod 版本与当前配置相等 → 不可见 → B 类下发不 409;A 类照旧 409 到排空完成)。

详见 HLD §6.2 场景 M-R(含运营注意:容量挤压 / 长会话上限 / 滚动升级混布)。

### 4.4 pod_spec 提取
- `route` 触发 `acquire` 时,从 template 行提取 **deploy 子集**(镜像 / namespace / kubeconfig / readiness / NFS / 资源限额),与 **池参数**(`min_idle_pods`/`pod_ttl`)+ 派生的 `max_pods`打包成 `pool_config`,随 `scope_id` 下发 Resource Manager。

---

## 5. 核心流程

### 5.1 Lua 脚本(承担所有 runtime 状态变更,原子)

> 脚本全集(6 个,实现与本文对齐):`LUA_ROUTE_PLACE` / `LUA_EVICT` / `LUA_TOUCH` / `LUA_SWEEP_IDLE_NOTIFY`(核心 4 个,下文全文)+ `LUA_REGISTER_POD`(acquire 成功登记,§5.2)+ `LUA_CLEANUP_POD`(notify_pod_dead 清注册,§2.3)。(`LUA_WAITER_GATE` 等待队列闸门已随 2026-09 场景 F 快失败拆除,历史见 §8.2/feature 文档。)
>
> **调用约定(2026-08-29 cluster 兼容)**:键名在脚本内由 `ARGV[1]`(键前缀
> `{session_manager}:`,hash tag)拼出;调用侧把前缀同时声明为 `KEYS[1]` 作路由锚
> (集群客户端据此把 EVAL 路由到 tag 归属节点;单实例无影响)。下文伪码从简省略。

**`LUA_ROUTE_PLACE(session_id, scope_id, expiry_ts, session_ttl, scope_concurrency, pod_concurrency, max_pods, now)`** —— route 的原子核心:一次脚本内完成"亲和续期 / 惰性回收 / scope 闸门 / first-fit 选 Pod / 提交",中途无其它请求插入(无 race)。`session_ttl` 随亲和写入 session HASH,供 TOUCH 就地读取(不依赖 scope:config)。
```
# 1. 读现有亲和绑定(若该 session_id 之前 route 过)
1. existing = HGETALL(session:{session_id})

# 1b. 残骸自卫(2026-09,同 LUA_EVICT):哈希存在但缺 scope_id/pod_id/expiry
#     (外部直改键的半成品)→ 自清两处后**落穿走全新放置**(亲和信息已不可信,
#     不 return rubble)。绝不能上抛或拿 nil 做比较——Lua runtime error 会让
#     该会话 route 永久 500。
1b. if existing 且 existing.scope_id/pod_id/expiry 任一为 nil:
      ZREM session_expiry session_id; DEL session:{session_id}; 落穿到 4

# 2. 亲和命中且未过期 → 仅续期,返回原 Pod。
#    不重新抢额度:chat_session 粒度模型下,额度在首次 route 已占用,持续到 TTL 老化;
#    同一 chat_session 的后续请求只刷新老化计时,不重复扣 scope/Pod 容量。
2. if existing.scope_id == scope_id and existing.expiry > now:
      HSET session:{session_id} expiry expiry_ts session_ttl {session_ttl}   # 续期 + 刷新 ttl(config 改了随 route 生效)
      ZADD session_expiry expiry_ts session_id
      return {action:"refresh", pod_id: existing.pod_id}

# 3. 惰性兜底:绑定存在但已过期,或 group/bot 变了导致 scope_id 不同 → 先回收旧绑定。
#    "惰性"= 在访问当场发现 session 已死就立即清理,不等 sweeper 下一 tick。
#    空在此处不触发 idle_consider(统一交 sweeper 空 Pod pass,见 §5.4)。
3. if existing:
      内部走 EVICT 逻辑(用 existing.scope_id/pod_id)

# 4. scope 闸门:scope_concurrency 限的是"活跃 chat_session 数",而非"在途请求数"
#    (gateway 保证每 chat_session ≤1 在途,故活跃数 == 在途数上界)。SCARD 即活跃数。
4. if SCARD(scope:{scope_id}:sessions) >= scope_concurrency:
      return {action:"scope_full"}

# 5. first-fit 按序:遍历该 scope 的 Pod(接入顺序),取首个有余量者即停。
#    不选最少负载:first-fit 把负载往早期 Pod 塞满,后加 Pod 在流量下降时先空出 → 先被回收,利于缩容
#    (对齐 HANDOFF_MULTI_POD 路由规约:"按序遍历,取第一个有余量的")。
5. for pod_id in ZRANGE(scope:{scope_id}:pods, 0, -1):
      if SCARD(pod:{scope_id}:{pod_id}:sessions) < pod_concurrency:
          chosen_pod = pod_id; break

# 6. 现有 Pod 都满:若已达 max_pods(Pod 数上限),总容量已 ≥ scope 预算,无 Pod 可用必因 scope 满 → scope_full;
#    否则 → need_acquire,由 handler 调 Resource Manager 扩 +1 Pod。max_pods 防一个 scope 无限扩 Pod。
#    注:`ZRANGE`/`ZCARD` 天然排除已被 sweeper `LUA_SWEEP_IDLE_NOTIFY` `ZREM` 的 Pod(已 idle_consider、待 RM 回收),
#    故 first-fit 不会选中 reclaim 窗口内的 Pod(竞态 A 由 SM 侧 ZREM 堵,见 §5.4);ZCARD 随 ZREM 下降也使 need_acquire 判定更正确。
6. if chosen_pod is None:
      if ZCARD(scope:{scope_id}:pods) >= max_pods: return {action:"scope_full"}
      return {action:"need_acquire"}

# 7. 提交:一次性原子写入所有结构(Lua 单脚本保证无 race)。
#    SET×2 + HASH + ZSET 同写"四处"(不变量 1);HASH 记亲和 + session_ttl(供 TOUCH 就地读)。
#    DEL idle_notified:该 Pod 被复用——若之前 sweeper 标过"已通知空",现在又有 session,清掉以便下次空时重新通知。
7. SADD scope:{scope_id}:sessions session_id
   SADD pod:{scope_id}:{chosen_pod}:sessions session_id
   HSET session:{session_id} scope_id {scope_id} pod_id {chosen_pod} expiry {expiry_ts} session_ttl {session_ttl}
   ZADD session_expiry expiry_ts session_id
   DEL pod:{scope_id}:{chosen_pod}:idle_notified
   return {action:"placed", pod_id: chosen_pod}
```

**`LUA_EVICT(session_id)`** —— session 移除的唯一原语:sweeper 到期 pass、ROUTE_PLACE 惰性(步骤 3)、TOUCH 惰性、notify_pod_dead 都经它。原子,幂等,自带唤醒。
```
existing = HGETALL(session:{session_id})
if not existing: return nil                            # 已被清理(并发 evict / 双重调用),幂等返回 nil
scope_id, pod_id = existing.scope_id, existing.pod_id

# 残骸自卫:哈希存在但缺 scope_id/pod_id(外部直改键造出的半成品)→ 只能自清
# 自身两处(无法定位 scope/pod 集合),返回 rubble 标记(调用侧 WARNING)。绝不能
# 上抛——单坏键会让到期 pass 每 tick 死在同一 sid 上(崩溃循环 = 会话永不过期)。
if scope_id == nil or pod_id == nil:
    ZREM session_expiry session_id; DEL session:{session_id}; return {rubble}

# 从两个集合移除(维护不变量 1:每 session 恰在一个 Pod)+ 清亲和 HASH + 清全局到期记录
SREM scope:{scope_id}:sessions session_id
SREM pod:{scope_id}:{pod_id}:sessions session_id
ZREM session_expiry session_id
DEL session:{session_id}

remaining = SCARD(pod:{scope_id}:{pod_id}:sessions)   # 该 Pod 剩余 session 数(观测/调试用)
# 注意:不触发 idle_consider——空 Pod 回收统一由 sweeper 空 Pod pass 驱动(§5.4),
# 避免 evict 的每条调用路径都重复 idle_consider 逻辑与去重判断。
return {scope_id, pod_id, remaining}
```

**`LUA_TOUCH(session_id, now)`** —— 保活/续期。touch 入参只有 session_id;`session_ttl` 直接从 session HASH 的 `existing.session_ttl` 读(route 写入),**不读 scope:config**(避免 config_sync `DEL` 后的 default fallback 不一致)。不改任何计数(额度不变)。
```
existing = HGETALL(session:{session_id})
if not existing: return {touched:false}                # 会话不存在 → gateway 应回退重新 route

# 残骸自卫(2026-09,同 LUA_EVICT):缺 scope_id/pod_id/expiry 的半成品哈希 →
# 自清两处返回不存在。绝不能上抛或拿 nil 比较——Lua runtime error 会让该会话
# touch 永久 500。
if existing.scope_id/pod_id/expiry 任一为 nil:
      ZREM session_expiry session_id; DEL session:{session_id}; return {touched:false}

# 惰性兜底:访问即校验 liveness;已过期则当场 evict,不等 sweeper 下一 tick。
if existing.expiry <= now:
      (内部走 EVICT); return {touched:false}

# session_ttl 取自 session HASH(route 放置/续期时写入)。新部署 session 必有此字段;
# 仅理论上(老数据/字段缺失)用 default_session_ttl 兜底。
session_ttl = existing.session_ttl or default_session_ttl
new_expiry_ts = now + session_ttl

# 仅续期:HASH 的 expiry + 全局到期集合的 score;不动 scope/Pod 集合(计数不变)。
HSET session:{session_id} expiry new_expiry_ts
ZADD session_expiry new_expiry_ts session_id
return {touched:true}
```

**`LUA_SWEEP_IDLE_NOTIFY(scope_id, pod_id, now)`** —— sweeper 空 Pod pass 的原子核心:单个脚本内完成"SCARD==0 判定 + SET idle_notified NX EX 60 去重 + ZREM scope:pods"。三步合并的必要性:`ZREM` 若与 `LUA_ROUTE_PLACE`(任意副本)分两次执行,存在微秒窗口——route 先提交使 SCARD=1,sweeper 仍 ZREM 一个已有 session 的 Pod → reclaim 误杀;合并后两者经 Redis 单线程串行执行,无交集。返回 `{notified:bool}`;handler 仅当 `notified:true` 才 `fire_and_forget idle_consider(pod_id, scope_id)`。
```
# 1. 非空 Pod 直接跳过(不通知、不 ZREM)。
1. if SCARD(pod:{scope_id}:{pod_id}:sessions) != 0:
      return {notified:false}

# 2. 60s 去重:同一空 Pod 60s 内只通知一次;过期后若仍空(RM 未回收)可重试。
2. if not SET pod:{scope_id}:{pod_id}:idle_notified 1 EX 60 NX:
      return {notified:false}

# 3. 关键:该 Pod 即刻退出 first-fit 候选——堵 reclaim 窗口内 route 直选复用(竞态 A)。
#    之后 LUA_ROUTE_PLACE 的 ZRANGE/ZCARD 天然看不到它;不删 pods:registered / pods:{pod_id}:scopes(留待 notify_pod_dead,见不变量 5)。
3. ZREM scope:{scope_id}:pods pod_id
   return {notified:true}
```
> 竞态 A(route 直选复用)由本脚本的原子 `ZREM scope:pods` 堵——该 Pod 即刻退出 first-fit 候选,reclaim 窗口内 route 不再选。完整论证见 §13.1 / RM spec §5.4.1。

### 5.2 route 编排(handler)
```
# scope_id 派生(SDK 字段名为 service_id):md5(group_id+bot_id);同一 (group_id,bot_id) 的所有请求落同一 scope。
scope_id = md5(metadata.extra.group_id + metadata.bot_id)

# 幂等回放优先:gateway 因超时/断连重试同一 request_id 时,直接返回上次结果,不重复抢额度、不重复扩 Pod。
cached = ctx.idempotency.get(metadata.request_id); if cached: return cached

# resolve:热路径先查 scope:{scope_id}:config 缓存(命中不查库);miss 或被 config_sync 失效才读共享 DB。
scope_config = resolve(scope_id, group_id, bot_id, user_id)

# 容量数学(见术语表):pod_concurrency = 单 Pod 满载容量(per-scope 独占);max_pods = 本 scope 的 Pod 数上限。
max_pods = ceil(scope_config.scope_concurrency / scope_config.pod_concurrency)

pod_spec = extract_pod_spec(scope_config.template)                    # acquire 时下发给 Resource Manager 的部署子集

loop:
    # 原子核心:一次 Lua 完成 亲和续期/惰性回收/闸门/选 Pod/提交。
    # 传 expiry_ts = now + session_ttl,并把 session_ttl 传入(写入 session HASH 供 TOUCH 就地读)。
    result = redis.eval(LUA_ROUTE_PLACE, session_id, scope_id,
                        now + scope_config.session_ttl, scope_config.session_ttl,
                        scope_config.scope_concurrency, scope_config.pod_concurrency, max_pods, now)

    if result.action in ("refresh","placed"):
        # 成功:取 Pod 的 sse_url 返回 gateway;gateway 据此直连 Pod(数据面绕过 Session Manager)。
        pod_sse_url = HGET pod:{scope_id}:{result.pod_id}:info sse_url
        out = {pod_sse_url: pod_sse_url, pod_id: result.pod_id}
        ctx.idempotency.put(metadata.request_id, out)                 # 缓存结果供幂等回放
        return out

    if result.action == "scope_full":
        raise SCOPE_FULL(503, retry_after=1)                          # 场景 F 快失败(2026-09):不排队不订阅,背压交 gateway 退避

    if result.action == "need_acquire":
        # 现有 Pod 都满且未达 max_pods:调 resource_manager 扩 +1 Pod(经进程内 rm_facade)。
        acquired = await rm_facade.acquire(scope_id=scope_id, pod_spec=pod_spec, pool_config=scope_config.pool_config, request_id=metadata.request_id)
        if not acquired: raise NO_POD_AVAILABLE(503)                 # NO_POD_AVAILABLE 由 rm_facade.acquire 抛 MaxPodsReached / DeployFailed 异常映射而来(原 Resource 拒绝 MAX_SERVICES_REACHED / DEPLOY_FAILED)
        # 登记新 Pod 到本 scope 候选集,供下一轮 ROUTE_PLACE 的 first-fit 选中。
        register_pod(scope_id, acquired.pod_id, acquired.pod_sse_url, deploy_ver)
        # register_pod = ZADD scope:pods(pod_seq) + HSET pod:info{sse_url, deploy_ver} + SADD pods:{pod_id}:scopes + SADD pods:registered + DEL idle_notified
        # deploy_ver = 本次 acquire 所用 deploy 子集的 hash 指纹,注册时记入 pod:info;config_sync A 类变更据此判定日落(§4.3)。
        continue                                                      # 重跑 ROUTE_PLACE(新 Pod 必被 first-fit 选中)
```
**快失败语义**(2026-09 起):`scope_full` 时 Lua 闸门即唯一仲裁,被拒者毫秒级 503 返回——无等待队列、无 pubsub 订阅、拒绝路径零 Redis 写入。历史的有界等待(pubsub 唤醒 + 安全轮询双保险)已整体拆除,见 docs/feature/2026-09-scope-full-fastfail.md。

### 5.3 touch / notify_pod_dead
- **touch**:直接 `redis.eval(LUA_TOUCH, session_id, now)`(惰性 evict 由脚本内处理;空 Pod 回收交 sweeper)。
- **notify_pod_dead(pod_id)**(= "pod 已不可用",含**死亡 + 回收**):现位于 `SessionManagerFacade.notify_pod_dead(pod_id)`,由 resource_manager 模块(死Pod / reclaim 路径)经进程内 Facade 触发(**非 HTTP**,见 §2.3)。其清注册逻辑不变:对 `SMEMBERS pods:{pod_id}:scopes` 每个 scope_id,`SMEMBERS pod:{scope_id}:{pod_id}:sessions` 逐 session 走 `LUA_EVICT` 收集 `invalidated`;清 `ZREM scope:{scope_id}:pods pod_id` / `DEL pod:{scope_id}:{pod_id}:*` / `SREM pods:registered "{scope_id}:{pod_id}"` / `SREM pods:{pod_id}:scopes scope_id`。per-(scope_id, pod_id) 用 Lua 保证原子。

### 5.4 sweeper(每实例一个后台 task)
```
每 tick(默认 1s):

  # 1. tick 级选主:多副本同时只有一个执行扫描,避免重复 evict / 重复 idle_consider。
  #    抢不到说明已有副本在本 tick 扫,直接跳过;锁 2s 自动过期(tick=1s,留余量)。
  1. SET lock:sweep NX EX 2            # 失败 → 跳过本 tick

  # 2. 到期 pass:扫全局 session_expiry 找已过期 session 并 evict。
  #    这是"再也不会被访问的废弃 session"的唯一回收路径——不扫的话,用户断连后其额度永久占用(scope 满 + Pod 不缩)。
  2. 到期 pass: due = ZRANGEBYSCORE session_expiry -inf now; 逐个 LUA_EVICT

  # 3. 空 Pod pass:idle_consider 的唯一触发点。枚举所有已注册 Pod,找 SCARD==0(无任何 session)者。
  #    统一覆盖三种空 Pod 成因:① 到期 evict 后变空 ② ROUTE_PLACE/TOUCH 惰性 evict 后变空
  #    ③ acquire 后 handler 崩溃/超时、从未放置 session 的孤儿 Pod。任一成因都在 ≤1 tick 内被考虑回收。
  #    关键:判定 + 去重 + ZREM 必须在单个 Lua(LUA_SWEEP_IDLE_NOTIFY)内原子完成——否则 ZREM 与
  #    LUA_ROUTE_PLACE(任意副本)间有微秒窗口,route 先提交使 SCARD=1,sweeper 仍 ZREM 有 session 的 Pod → reclaim 误杀。
  3. 空 Pod pass: for key in SMEMBERS pods:registered:
        (scope_id, pod_id) = split(key)
        res = redis.eval(LUA_SWEEP_IDLE_NOTIFY, scope_id, pod_id, now)
        if res.notified:
            fire_and_forget(rm_facade.idle_consider(pod_id=pod_id, scope_id=scope_id))  # 不 await:失败不阻塞,60s idle_notified 过期重发自愈(§13.1)
```
- **空 Pod 回收(idle_consider)只在 sweeper 一处**,扫 `pods:registered` 统一驱动——无论 Pod 因何种路径变空(到期 evict / 惰性 evict / acquire 后从未放置的孤儿 Pod),都在 ≤1 tick 内被考虑回收,无 leak。
- 选主锁保证全局同时只有一个副本扫描;崩溃安全(锁自然过期)。
- `idle_consider` fire-and-forget:失败不阻塞一致性,60s 后 `idle_notified` 过期可重试。
- **ZREM 堵竞态 A**:`LUA_SWEEP_IDLE_NOTIFY` 原子 `ZREM scope:pods` 使该 Pod 即刻退出 first-fit 候选(reclaim 窗口内 route 不再直选)。`idle_consider` 丢失由 SM 60s `idle_notified` 过期重发 + RM 侧 `reconcile_pods` 孤儿对账兜底。详见 §13.1 / RM spec §5.4.1、§5.4.2。
- 惰性兜底(§5.1 步骤 3、§5.3 touch)保证热路径正确性不依赖 sweeper 时序;sweeper 到期 pass 负责回收**再也不会被访问**的废弃 session。

---

## 6. 正确性与原子性论证

| 性质 | 保证方式 |
|---|---|
| 并发抢额度无 race | `LUA_ROUTE_PLACE` 单脚本内完成 scope 闸门 + first-fit + commit |
| 计数无漂移 | SCARD 派生,无独立计数器 |
| 崩溃安全 | 崩溃只可能"已 commit 已返回"或"未 commit";无 detached 占位;未清理 session 由 sweeper 老化回收 |
| TTL 正确 | 惰性(访问校验)+ 主动(sweeper)双层,互相兜底盲区 |
| 幂等 | `route` 包在框架 `ctx.idempotency`(key=request_id)内,重放返回缓存结果不重复抢额度 |
| 无空 Pod 泄漏 | 空 Pod 回收只在 sweeper 扫 `pods:registered` 统一驱动,覆盖 evict / 惰性 / 孤儿 Pod 全部成因,≤1 tick 内考虑回收 |
| reclaim 窗口内无新 session | `LUA_SWEEP_IDLE_NOTIFY` 发 `idle_consider` 前**原子 `ZREM scope:pods`**,该 Pod 即刻退出 first-fit 候选;route 的 `LUA_ROUTE_PLACE` 不再选中 → RM 自治 reclaim 期间 SM 不会 route 新 session 上去(竞态 A) |

---

## 7. 错误码与边界

- `SCOPE_FULL`(503):scope 闸门(活跃会话达 scope_concurrency,或 Pod 全满且达 max_pods),**立即快失败不阻塞**(2026-09 起替代原 504/队列满两码)。
- `NO_POD_AVAILABLE`(503):Resource `acquire` 失败(`MAX_SERVICES_REACHED` / `DEPLOY_FAILED`)。
- `CONFIG_NOT_FOUND`(503):resolve 无匹配 template。
- `VALIDATION`(400):缺 `session_id` / `group_id` / `bot_id`。
- 边界:`scope_concurrency=0` → 全部 `scope_full` → 504。

> **过载信号契约**:过载类响应(`SCOPE_FULL` / `NO_POD_AVAILABLE`)均带 `Retry-After`(秒)+ `error_code`;gateway 据此判"可重试 vs 不可重试"与退避节奏(`CONFIG_NOT_FOUND` / `VALIDATION` 不可重试)。详见 §8。
- single-pod 模式(`scope_concurrency ≤ pod_concurrency`,`max_pods=1`):一个 Pod 即可容纳全部额度,`need_acquire` 至多触发一次,行为与改动前一致。

**可调参数默认值**(均在 `applications/session_manager/config.py`,可按 template 覆盖):

| 参数 | 默认 | 含义 |
|---|---|---|
| `sweeper_tick` | 1s | sweeper 扫描周期 |
| `lock:sweep` TTL | 2s | sweeper tick 选主锁(tick=1s 留余量) |
| `idle_notified` TTL | 60s | 空 Pod 通知去重窗口,过期可重试 |
| `default_session_ttl` | 60s | session HASH 缺 `session_ttl` 字段时的兜底(正常不触发;与 template `session_ttl` 默认一致) |
| `scope:{scope_id}:config` TTL | 无 | 显式失效(config_sync `DEL`),不走 TTL |
| idempotency window | 60s | 框架 `ctx.idempotency` 默认(route 结果缓存) |

---

## 8. 过载与突发保护

突发流量下,**SM 是状态权威、负责拒**(它知道 scope 占用);**gateway 看不到状态、负责退**(退避纪律)。两者靠**信号契约**解耦。

### 8.1 SM 信号契约
所有过载响应必须带 `error_code`(gateway 据此判可重试)+ `Retry-After`(秒,SM 据 `session_expiry` 最近到期估算,保守取整):

| `error_code` | HTTP | 可重试 | 含义 |
|---|---|---|---|
| `SCOPE_FULL` | 503 | ✅ | scope 满/达总容量,立即快失败(2026-09 起) |
| `NO_POD_AVAILABLE` | 503 | ✅ | Resource 部署失败(infra 暂时) |
| `CONFIG_NOT_FOUND` | 503 | ❌ | 无匹配配置(永久,重试无意义) |
| `VALIDATION` | 400 | ❌ | 参数错 |

> gateway 必须**读 `error_code`,不能只看状态码**:`CONFIG_NOT_FOUND` 与 `SCOPE_FULL` 都是 503,语义相反。

### 8.2 SM 侧:容量满立即快失败(2026-09 起)
- `route` 收到 Lua 返回 `scope_full` → **立即 503 `SCOPE_FULL` + `Retry-After`**:不排队、不订阅、不占连接,拒绝路径零额外 Redis 资源——资源占用天然封顶在"每请求一条短命令"。
- 历史:v1 为「有界等待队列 + 快失败」(等待者 ZSET + `LUA_WAITER_GATE` 原子闸门 + pubsub 唤醒),因 redis-py asyncio `RedisCluster` 无 pubsub 实现且等待资源占用与控制面定位不匹配而整体拆除;拆除前该机制的竞态修复(M6 超收/C7 丢唤醒/C9 幽灵名额)记录于 docs/feature/2026-08-27-audit-e2e-repro-fixes.md,缺陷面随机制消失。

### 8.3 gateway 侧(本设计仅定义契约;实现属 gateway EE,不在本服务范围)
- 读 `error_code`:仅可重试类才重试。
- **指数退避 + full jitter**(反重试风暴核心):`delay = random.uniform(0, min(cap, base * 2 ** attempt))`(默认 base=0.5s、cap=8s);SM 给 `Retry-After` 则用它收窄窗口。
- **按 `scope_id` 记重试预算**,总预算 < channel 超时(IM ~5–15s);耗尽则放弃、给用户"服务繁忙,稍后重试"或入队。
- **幂等重试**:重试复用同一 `request_id` → SM `ctx.idempotency` 命中缓存则直接回原结果(不双抢额度、不重复扩 Pod),故 gateway 可放心重试。

### 8.4 为什么抖动是关键(反风暴)
固定延迟重试会让被拒的 N 个请求在**同一时刻**再次打到 SM → 周期性尖峰 → SM 反复过载、无法恢复(重试风暴)。`random(0, window)` 把重试摊开;attempt 增大 → 窗口变宽 → 摊得更散 → 每秒到达 SM 的重试 ≤ 准入率(`scope_concurrency / session_ttl`),系统必然收敛。

### 8.5 突发示例
1000 个新会话同时到来、`scope_concurrency=100`:`scope:sessions` 1→100(100 过闸,部分触发扩 Pod);其余 900 命中 `SCOPE_FULL` → SM ms 级回 503 + `Retry-After`;gateway full-jitter 退避 → 重试被打散 → SM 每秒只受 ~准入率个新请求,平缓收敛;预算耗尽者给"稍后再试"。

### 8.6 follow-up(非 v1)
- `acquire` 异步化(不阻塞请求至 deploy 完成);
- gateway 盲粗粒度限流(防病态洪峰打满连接 accept)。

---

## 9. 安全性与其他生产特性

与 §8 过载保护同属横切的生产特性。本节固化 **3 项设计级安全特性**;审计/指标/租户/停机/配额见 §13 follow-up。

### 9.1 认证与授权
合并后对外 endpoint 为 `route` / `touch` / `config_sync` / `cleanup` 四个(`notify_pod_dead` / `reconcile_pods` 已降级为进程内 Facade,见 §2.3 / §2.5)。其中 `config_sync` 与 `cleanup` 是高危写接口:推恶意 `config` 可把用户路由到攻击者的 Pod,`cleanup` 可批删 Pod。
- gateway ↔ SM、Claw Manager ↔ SM、运维 ↔ `cleanup` 一律 **mTLS 或 token**(复用框架 foundation `link_auth`)。
- **SM ↔ RM 为同进程 Facade 调用,无需 mTLS**(同进程同信任域)。
- `config_sync` 仅 Claw Manager 可调;`cleanup` 仅运维可调。
- v1:内网 mTLS + endpoint 级 caller 校验。

### 9.2 输入校验与键命名安全
`session_id` / `group_id` / `bot_id` 由外部可控,且**直接拼进 Redis 键**(`session:{session_id}`、`scope:{scope_id}`、`pods:registered` 的 `"{scope_id}:{pod_id}"`)。未校验会:
- **键注入/解析错乱**:含 `:` / `\n` / `\r` / 超长 / 空值 → 命名空间串台或撑爆 Redis。
- **`scope_id` 撞号**:`md5(group_id + bot_id)` 若无分隔符,`(g='ab',b='c')` 与 `(g='a',b='bc')` 算出同一 scope(不同域混到一起)。
- **数值越界**:`session_ttl=0` → 秒过期;`scope_concurrency` / `pod_concurrency` 极值。
- **缓解**:handler 入参白名单(字符集 + 长度上限 + 非空);`scope_id` 派生加安全分隔符 `md5(group_id + '\x00' + bot_id)`(确认 SDK 是否已带,若无则补);数值字段校验上下限。

### 9.3 Redis 持久化与冷启动恢复
runtime 态全在内存态 Redis;**Redis flush / 重启(无 AOF/RDB)→ 会话与 Pod 注册全丢,而 K8s 侧 Pod 仍在** → route 会重新 acquire 部署新 Pod,**旧 Pod 成孤儿泄漏**。
- Redis **必须开 AOF/RDB 持久化**(部署硬要求,见 §11)。开持久化后跨重启编排态不丢,**SM 无需冷启重建 pod 注册**。
- Redis flush 属灾难性丢数据(会话/TTL 态同时丢失),不在恢复目标内——不提供 `list_pods` / 枚举 Pod 端点;flush 后旧 Pod 由 RM 侧孤儿对账 sweeper(经 Facade `sm_facade.reconcile_pods`,§13.1)发现并按 `pod_ttl` 回收。
- 或部署上保证 Redis 持久化 + 禁止清库。

> **未在本节(留 §13 follow-up / 归属他处)**:`config_sync` 审计日志(归 Claw Manager / 合规)、指标告警(归 OTel)、租户隔离(`jiuwenclaw_id` 入 Redis key 前缀或单租户部署)、优雅停机 drain、per-调用方配额(归 gateway)。**Pod 探活暂不实现**——Pod 不可用依赖 Resource 的 `notify_pod_dead` 契约(见 §13);**RM↔SM 周期对账 `reconcile_pods`(§13.1)消除「RM 持有 Pod、SM 已不用」的孤儿 Pod**;不变量漂移靠 Lua 原子 + SCARD 派生计数兜底。

---

## 10. 模块文件结构

**核心包**(业务逻辑,peer of 现有 `session/` SDK):
```
agent-runtime/management/openjiuwen_runtime/management/session_manager/
  __init__.py
  app.py              # App 构造 + 注册 handler(route/touch/config_sync/config_refresh/cleanup;notify_pod_dead/reconcile_pods 已降级为 Facade)
  handlers.py         # route / touch / config_sync / config_refresh / cleanup 五个 handler(cleanup 委托 rm_facade.cleanup)
  orchestrator.py     # route 编排(resolve→闸门→选 Pod→rm_facade.acquire)
  state.py            # Redis 键 schema + 不变量(封装 ctx.redis 唯一出口)
  lua_scripts.py      # LUA_ROUTE_PLACE / LUA_EVICT / LUA_TOUCH / LUA_SWEEP_IDLE_NOTIFY 文本
  sweeper.py          # 后台老化扫描 + 选主锁(调 rm_facade.idle_consider)
  config_store.py     # template/路由规则 DB 存储 + resolve + config_sync op(移植 EE service_config_template.py)
  facade.py           # SessionManagerFacade:notify_pod_dead / reconcile_pods(供 RM 模块进程内调用)
  models.py           # 入参/出参 dataclass、错误码常量
```
> session_manager 不再持有 `peer_client.py`(httpx 调 Resource Manager)——改为持有 `ResourceManagerFacade` 引用(进程内调 acquire / idle_consider)。

**可运行壳**(peer of `applications/echo`):
```
agent-runtime/applications/orchestrator/   # 【合并壳】合并服务的唯一可运行壳
  main.py             # 构造 sm_sysctx + rm_sysctx + 两 Facade(互持引用)+ 一个 App(prefix=/api/session)+ 注册 SM handler + 起 SM/RM 两套后台任务
  config.py           # 8091 / 共享 redis URL / 共享 DB / K8s kubeconfig / 各 task 周期默认
  pyproject.toml
```
> session_manager 与 resource_manager 均不再有各自独立的壳;合并为 `applications/orchestrator/` 一个壳。框架扩展(§0.4)落在 `agent-runtime/service/openjiuwen_runtime/service/context/system_context.py`(加 `ctx.redis`)。改动均在**嵌套仓库** `agent-runtime/`(branch `develop`),单独提交,与外层 jiuwenclaw(`dev/enterprise_kub`)分开。

---

## 11. 部署与 HA

- 多副本无状态,前置 LB;gateway 调任意副本。
- runtime 态在**共享 Redis**(与 Resource Manager **同一实例**,仅 `key_prefix`(`session_manager` / `resource_manager`)不同;须高可用:sentinel/cluster;**必须开 AOF/RDB 持久化**,否则重启/flush 丢失会话与 Pod 注册致孤儿 Pod 泄漏,见 §9.3),config 在共享 DB。两者均为关键状态。**作废**原"SM/RM 须配不同 Redis 进程或不同 DB 号"硬要求——合并后同进程同信任域,模块边界由 Facade 强制,不靠 Redis 实例隔离。
- sweeper 每 tick 选主,多副本安全。
- Redis / DB 不可用 = 服务不可用(fail-fast,不降级到内存)。

---

## 12. 测试策略

- **单元(fakeredis)**:`LUA_ROUTE_PLACE`(affinity-refresh / 惰性 evict / `scope_full` / first-fit 顺序 / `need_acquire` / `max_pods` 封顶 / 复用 Pod 清 idle_notified / **`ZREM scope:pods` 后该 Pod 不被 first-fit 选中**)、`LUA_EVICT`、`LUA_TOUCH`、`LUA_SWEEP_IDLE_NOTIFY`(**空 Pod pass 原子性:SCARD==0 + SET NX + ZREM 单脚本** / **非空 Pod 不通知不 ZREM** / **60s 去重**)、sweeper(到期 pass + 空 Pod pass,含孤儿 Pod)、错误码映射、config_sync op。
  - ⚠️ 已知陷阱(memory):fakeredis 消费组 id=`"0"` bug;ZSET/EVAL/Lua 在 fakeredis 可用;不支持则相关用例移到真 Redis 层。
  - ⚠️ `ctx.redis`(M0 框架扩展)需配 fakeredis 验证 handler 经它读写跨"副本"可见。
- **组件(直接调 Facade + stub 对方 Facade)**:`route` 全链路(resolve→闸门→选 Pod→`rm_facade.acquire`)、`touch` 续期、`notify_pod_dead` 反查清洗、`config_sync` 写库 + 失效;RM 孤儿对账 sweeper 调真实 `sm_facade`。sysctx 惰性建(进程内构造不跑 lifespan,需手动 start/stop)。
- **并发/容量**:突发新 session 测 `scope_full`→504 + 经 acquire 扩 Pod;多 Pod first-fit 分布;亲和粘性;老化回收 + `idle_consider`;single-pod 模式行为不变。**reclaim 窗口安全**:ZREM 后 route 不选该 Pod;**`idle_consider` 丢失自愈**:经 60s `idle_notified` 过期重发,且 RM 侧孤儿对账 sweeper `reconcile_pods` 兜底将 stale Pod 移入 idle。
- **集成(真 Redis,CI)**:Lua 原子性、sweeper 跨副本选主(多 worker)、共享 DB 多副本 resolve 一致。

---

## 13. 待确认假设 / 开放项

> **已解决(完备性修订)**:`scope:{scope_id}:config` 归属——保留作 resolve 的 Redis 缓存(框架禁进程内缓存,Redis 是合规缓存位,唯一读者= resolve);TOUCH 改读 `session:{session_id}.session_ttl`(route 写入),不再依赖 scope:config,消除 config_sync `DEL` 后的 default fallback 不一致。见 §3 键表 / §5.1 LUA_TOUCH。

1. **Resource Manager 契约**(✅ 已与 RM 设计 `resource-manager-design.md` §0.3 对齐确认;二者为同进程模块,经**进程内 Facade 方法签名**互调(见本节下文);**不共享 Redis 修订**:删 RM 读 SM 的前置对账,改 SM→RM 单向 ZREM + 周期 Facade 对账兜底;入参 / 出参 / 幂等 / 防 deploy 超配 / 自治 reclaim 语义均不变):
   - **`ResourceManagerFacade.acquire`** 入参 `{ scope_id, pod_spec, pool_config, request_id }`;出参 `{pod_id, pod_sse_url}`。失败抛 `MaxPodsReached` / `DeployFailed` / `ValidationError` 异常,由 `route` handler 捕获映射为对外 HTTP `NO_POD_AVAILABLE`(§2.1)。
   - **单 scope 占 Pod**:RM 按 `scope_id` 独立建池;容量由 SM 的 `SCARD < pod_concurrency` 闸门保证,RM 不强制、无 reserves HASH。
   - **`ResourceManagerFacade.idle_consider` 为 scope 级**:入参 `{pod_id, scope_id}`,出参 `{transitioned_to_idle:bool}`,幂等(`HDEL` 天然幂等)。单 scope 占 Pod,释放即 idle。
   - **`ResourceManagerFacade.update_pool_config`**(config_sync 触发,见 §4.3):入参 `{ scope_id, pool_config }`(A 类变更时附带 `pod_spec` deploy 字段);出参 `{ updated:bool }`。HSET 覆盖 RM 侧 `resource:scope:{scope_id}:config`(**幂等**;mapping 永不含 `generation`,代次只经 `bump_generation` 单调递增);RM 的 autoscale/reclaim **立即**用新池参数,A 类变更同时刷新 RM 缓存的 deploy 字段(后续 deploy 用新值)。详见 RM spec §2.2.1。
   - **`ResourceManagerFacade.bump_generation`**(config_refresh 触发,场景 M-R,见 §4.3b):入参 `{ scope_id }`,出参 `generation:int`。`HINCRBY` RM 侧 `resource:scope:{scope_id}:config` 的 `generation`(原子自增,唯一写点)→ 现有 Pod 的代次全部落后 → RM acquire 过滤 / reclaim 判 stale / autoscale 重建。详见 RM spec §2.2.2 与 HLD §6.2 场景 M-R。
   - **pod 不可用信号(含死亡 + 回收)+ 自治 reclaim**:✅ `SessionManagerFacade.notify_pod_dead` 覆盖回收(idle→reclaim)与死亡两种。**RM reclaim 自治(不读 SM 模块 key,经 Facade 边界)**:RM 按 `idle_since` 自治 `if now - idle_since >= pod_ttl: k8s.delete + LUA_PURGE + sm_facade.notify_pod_dead`。安全性靠 SM→RM 单向契约:SM `LUA_SWEEP_IDLE_NOTIFY` 发 `idle_consider` 前原子 `ZREM scope:pods`,该 Pod 即刻退出 first-fit 候选,reclaim 窗口内 route 不再直选新 session 上去(堵竞态 A)。**`idle_consider` 丢失 / SM 重启漂移** 由 RM 侧孤儿对账 sweeper 经 Facade `sm_facade.reconcile_pods` 兜底(见下条)。详见 RM spec §5.4.1 / §5.4.2。SM 侧 `notify_pod_dead` 清注册逻辑不变(§5.3)。
   - **冷恢复**:合并服务共享 Redis 开 AOF/RDB,跨重启编排态不丢,SM 无需从 RM 重建 pod 注册;**不提供 `list_pods` / 枚举 Pod 端点**。Redis flush 不在恢复目标。详见 §9.3 / RM spec §5.5。
   - **周期对账 Facade `SessionManagerFacade.reconcile_pods`(2026-08-10 新增,合并后从 REST 改进程内 Facade)**:SM 暴露 `reconcile_pods(view)`(§2.5),RM 每 30s 经 Facade 调用,消除「RM 持有 Pod、SM 已不用」的孤儿 Pod(`idle_consider` 丢失 / SM 重启漂移)。SM 对入参每个 (pod,scope) 查 `scope:{scope_id}:pods` 成员资格,非成员返回 `stale`;RM 将 stale Pod 移入 idle → 按 `pod_ttl` 回收。**只读、单向**(RM 不直读 SM Redis,跨模块只走 Facade)。
2. **resolve 匹配算法**:`(group_id, bot_id, user_id) → template_id` 的 template_ref / service_policy 精确匹配逻辑,从现有 gateway 路由定位并移植——留实现计划。
3. **routing_rule schema**:`config_sync` 的 `kind=routing_rule` 数据结构——留实现计划(部分新增)。
4. ~~**scope_full_timeout**~~:已随 2026-09 场景 F 快失败移除(等待机制不复存在)。
5. **idempotency in-flight 语义**:依赖框架 `ctx.idempotency` 对在途 request_id 的处理,实现阶段验证。
6. **并发 acquire 可能轻微超配**:同一 scope 多个 route 同时 `need_acquire` → 各自调 Resource `acquire` → 部署多个 Pod(first-fit 只用一个,余者空置待 sweeper idle_consider 回收)。自愈但浪费一次 deploy。若需避免,可加 per-scope acquire 分布式锁合流——留 follow-up,非正确性问题。
7. **routing_rule 完整 service_policy**:v1 最小映射表已定(§4.1);user_id 精细策略 / template_ref 规则留 follow-up。

---

## 14. 与现有代码的对应

| 现有(将移植/参考) | 用途 | 处置 |
|---|---|---|
| `session/` SDK:`ServiceScopeHandler` 选 Pod 逻辑、`max_pods` 数学、TTL 规则、错误码 | 控制面逻辑 | **移植为 Redis + Lua**,弃用进程内类 |
| `session/` SDK:`WSServiceMessageChannel`、`ServiceHandler` 通道、`dual_queue`、`response_queue` | 数据面 | **弃用**(旁路定位) |
| EE `service_config_template.py`:`apply_service_config_template` op、`_build_row_from_template` | config 持久化 | **移植**,本地库→共享 DB |
| `openjiuwen_runtime.service`(App/Envelope/RequestContext/Redis 原语 + 新 `ctx.redis`) | 服务框架 | **直接使用**(框架加 `ctx.redis` 见 §0.4) |
