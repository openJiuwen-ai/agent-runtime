# Resource Manager 详细设计

- 日期:2026-08-07
  - 范围:`agent-runtime/` 内Resource Manager 模块(合并服务内,Pod 生命周期所有者)
  - 状态:**draft,待评审**
  - 关系:实现 bypass 设计 `2026-07-29-agent-runtime-microservices-bypass-design.md` 的 §4(Resource Manager);与 Session Manager 设计 `session-manager-design.md` 对接(其 §13 假设本文拍板)。HLD 见 `Agent-Runtime-HLD.md` §3(对外接口)。

### 术语(全称,不缩写)

- **`scope_id`** = `md5(group_id + bot_id)`,Scope 标识(对应 SDK `ServiceScope`,字段名 `service_id`;见 SDK `GLOSSARY.md` §1)。本文统一用 `scope_id`。**Pod 池按 `scope_id` 分组**,每个 scope 一个独立 Pod 池,一个 Pod 只服务一个 scope。
  - **`pod_id`** = Pod 实例标识(= SDK `ServiceHandler.id`;**Resource Manager 对外契约历史字段名 `endpoint_id`,本文统一用 `pod_id`,实现时 `endpoint_id`↔`pod_id` 做别名**)。
  - **`pod_concurrency`** = 单 Pod 总并发上限(DB legacy `service_concurrency`);Session Manager 的 template 输入(per-scope 独占 Pod,SM 以 `SCARD < pod_concurrency` 闸门保证容量,Resource Manager 不强制)。
  - **`pod_ttl`** = idle Pod 至 reclaim 的等待秒数(DB legacy `service_ttl`)。
  - **`min_idle_pods`** = per-`scope_id` 的最小热备 Pod 数(DB legacy `min_idle_services`)。
  - **`max_pods`** = 单个 scope 的最大 Pod 数,**派生** = `⌈scope_concurrency / pod_concurrency⌉`,SM 经 `pool_config` 传入。

---

## 0. 背景与定位

### 0.1 现状

`agent-runtime/management/openjiuwen_runtime/management/session/` 当前是**进程内 Python SDK**(`Access` + `ServiceManager`):Pod 的 idle/in_use 双池、容量预留、scope 引用、亲和路由全部在**进程内内存**(`_in_use`/`_idle` dict、`ServiceHandler._session_reserved`、`ServiceScopeHandler._affinity`)。SDK 跑在 gateway 主进程(主备单活),与 K8s 直连做 deploy/delete/监控。

### 0.2 目标

把「Pod 资源管理」做成**合并服务内的分布式模块**(基于已实现的 `openjiuwen_runtime.service` 框架),满足 bypass:

- **RESTful 接口**,unary `POST`。
  - **不在数据通路上**:deploy 后把 Pod 的 SSE 端点交给 Session Manager → gateway 直连;Resource Manager 仅在旁路做 Pod 生命周期管理。
  - **同进程内部模块**:Resource Manager 与 Session Manager 合并为同一服务,**无独立 App**;编排语义态在**共享 Redis**(同一实例,`key_prefix=resource_manager`,与 SM 前缀隔离),Pod 物理态以 K8s 为唯一真相源。
  - **config-agnostic**:不读 config DB、不感知 template 语义,所有部署配置由 Session Manager 经 `acquire` 的 `pod_spec` 传入。

### 0.3 与 Session Manager 契约的对齐(含修订)

本设计对 Session Manager spec §13 / HLD §3 的契约做如下**确认与修订**:

| 项 | SM spec §13 假设 | 本设计(拍板) |
|---|---|---|
| 单 Pod 容量上限 | 由 Resource Manager 负责 | ✅ **per-scope 独立 Pod 池**:一个 Pod 只服务一个 scope(单 scope 占 Pod),无跨 scope 共享;容量由 Session Manager 的 `SCARD(pod:{scope_id}:{pod_id}:sessions) < pod_concurrency` 闸门保证,Resource Manager **不维护 `reserves` HASH**。 |
| `idle_consider` 语义 | `{pod_id}` → `{transitioned_to_idle:bool}` | ⚠️**修订为 scope 级**:`{pod_id, scope_id}` → `{transitioned_to_idle:bool}`。单 scope 占 Pod,Pod 上该 scope 0 session 即转入 `scope:idle` 暖池。 |
| `notify_pod_dead` 覆盖范围 | 待确认是否覆盖回收 | ✅ **确认覆盖死亡 + reclaim 两种**:Pod 物理死亡(K8s Watch/轮询探测)与 idle→reclaim(主动回收)均经 `notify_pod_dead` 通知 Session Manager 清注册。 |
| 状态真相源 | 未定(分水岭) | ✅ **拍板**:Pod 编排语义态(idle/info)在**共享 Redis**(同一实例,前缀 `resource_manager:`,与 SM 前缀隔离);Pod 物理态(存在/健康/pod_ip/sse_url)以 **K8s 为唯一真相源**,K8s Watch 驱动 Redis 对账。 |
| Pod idle 转换一致性 | 未定 | ✅ **拍板**:主路径 = Session Manager scope 级 `idle_consider`(单 scope 占 Pod,Pod 释放即转 idle);**周期对账兜底**(RM 经 Facade `reconcile_pods` 每 30s 查 SM,SM 不再 route 的 stale Pod → 按 `pod_ttl` 回收;不读 SM 模块 key)。Session Manager `idle_consider` 时已**原子 `ZREM scope:{scope_id}:pods`**,保证 reclaim 窗口内 SM 不再 route 新 session 到该 Pod。 |

> **模块协调契约(Facade 边界)**:两模块共享同一 Redis 实例,但**跨模块数据交换只走进程内 Facade 方法**,不直接读对方 Redis key(模块硬边界)。原四条 REST(`acquire`/`idle_consider`/`notify_pod_dead`/`reconcile_pods`)改为 Facade 方法。**作废**原"SM/RM 须配不同 Redis 进程或不同 DB 号"硬要求(同进程同信任域)。
>
> **冷恢复**:两模块共享 Redis(各自前缀)开 AOF/RDB 持久化,跨重启编排态不丢,**无需互相重建**(不提供 `list_pods` / 枚举 Pod 端点)。Redis flush 属灾难性丢数据(会话/TTL 态同时丢失),不在恢复目标内。

### 0.4 框架前提(复用 Session Manager 的扩展)

复用 Session Manager M0 对服务框架的扩展(见 `session-manager-design.md` §0.4):`RequestContext` 已增加 `ctx.redis`(返回原始 `redis.asyncio` client)。本设计的 Pod 池状态(idle/info/per-scope 池键)需 HASH/SET/ZSET/Lua,经 `ctx.redis` 访问;简单 KV 走 `ctx.kv`。后台任务(sweeper/K8s Watch/autoscale)的生命周期经 `SystemContext` 子类 `start()`/`stop()` 注入,**不改 `App`**(同 Session Manager 模式)。

### 0.5 移植策略

**新建 + 移植逻辑(非类)**:在框架 + Redis 上新建 handler,从现有 SDK 移植**逻辑与机制**:

- **移植**(控制面/物理面逻辑,从进程内迁到 Redis + K8s Watch):
  - `ServiceManager`:autoscale(`min_idle` 补位)、idle→reclaim 计时、`_bootstrap_min_idle`、失效 Pod 监控 —— 移植为 Redis 状态 + 选主后台任务。
  - `K8sServiceHandler`:`deploy`(create + `_wait_running_ready`)、`delete`、`monitor_pods_status`(轮询)、`cleanup_all_agentserver_pods`(按 label 批删)、Pod Watch —— 近原样复用 K8s 交互层。
  - `runtime.py`:`IDeployController` / `K8sDeployController` / `NoOpDeployController` —— 抽象复用。
  - `models.py`:`AccessConfig` 字段拆分:per-scope 池参数(`min_idle_pods`/`pod_ttl`;`max_pods` SM 派生)经 `pool_config` 传入 RM,deploy 字段经 `pod_spec` 传入;`autoscale_interval` 全局默认(RM 服务级配置,不入 `scope:config`、不入 `pool_config`/`pod_spec`);`pod_concurrency` SM 自用作 per-Pod 容量闸门,不入 RM。
  - **新增**(微服务化必需):per-`scope_id` Pod 池(`scope:{scope_id}:pods/idle/config/deploying`,原进程内 `_in_use`/`_idle` dict 的外置化)+ scope 级 `idle_consider` + RM↔K8s 孤儿对账 sweeper(**不读 SM**)。
  - **弃用**(与无状态/旁路定位不兼容):
    - 数据面:`WSServiceMessageChannel`、`ServiceHandler` 的通道部分、`response_queue`、`dual_queue`、`IResponseParser`(旁路定位,Resource Manager 不在数据通路)。
    - 进程内并发/计时:`asyncio.Semaphore`、进程内 `asyncio.Timer`、`ServiceHandler`/`SessionHandler` 进程内信号量、进程内 `_in_use`/`_idle` dict。
    - ⚠️ **`pod_sse_url` 的端点模型变化**:现 SDK 用 WebSocket(`WSServiceMessageChannel` 以 `pod_ip:port + invoke_path` 拼 `ws://`)。bypass 下 Pod 暴露 **SSE** 端点供 gateway 直连;`pod_sse_url` 由 `pod_ip` + `pod_spec` 的 SSE 端口/路径构造。SSE 字段名与 Session Manager template 对齐留实现计划(§13.1)。

---

## 1. 架构与拓扑

### 1.1 与 Session Manager 协作的拓扑(合并服务内部,同进程两模块)

```
合并服务:一个 App(prefix=/api/session),同进程两模块 + 共享 Redis + 共享 DB
  ┌──────────────────────────────────────────────────────────────────────┐
  │  session_manager 模块(持唯一 App、注册对外 HTTP handler)             │
  │  resource_manager 模块(无 App、无 prefix、无端口,纯内部 Facade)     │
  │                                                                       │
  │  SM ──(无可用 Pod)──► rm_facade.acquire {scope_id, pod_spec,          │
  │                          pool_config}                                 │
  │                          └── 取 scope 暖 Pod 或 选主 deploy +1         │
  │  SM ◄── {pod_id, pod_sse_url} ──┘                                     │
  │  SM ──(Pod 上该 scope 无会话)──► rm_facade.idle_consider              │
  │                                    {pod_id, scope_id}                 │
  │  RM ──(Pod 死亡 / reclaim)──► sm_facade.notify_pod_dead {pod_id}      │
  │  RM ──(周期对账,每 30s)──► sm_facade.reconcile_pods                 │
  │        └─► {stale:[(pod,scope)...]} ──► RM 转 stale Pod idle(消除孤儿)│
  │                                                                       │
  │  (SM↔RM 均为进程内 Facade 异步方法调用,无 HTTP、无序列化、无需 mTLS)  │
  └──────────────────────────────────────────────────────────────────────┘
              ▼ 共享 Redis(key_prefix: session_manager / resource_manager)  ▼ 共享 DB

物理面(resource_manager 模块 ↔ K8s):
  RM ──deploy/delete/watch──► K8s API ──► AgentServer Pod
  运维 ──POST /api/session/cleanup {label_selector}──► SM handler ──委托 rm_facade.cleanup()──► K8s(灾难恢复 / 重新部署)

旁路安全契约(跨模块不直读对方 Redis key,RM 自治 reclaim):
  session_manager idle_consider 时 ──原子 ZREM scope:{scope_id}:pods──► 该 Pod 移出 route 候选,reclaim 窗口内 SM 不再 route 新 session 到它

数据面(合并服务全程旁路,gateway ↔ Pod 直连):
  Gateway ──POST 消息体──► Pod(pod_sse_url 由 SM route 返回)
  Gateway ◄──text/event-stream──┘
```

### 1.2 服务画像

| | Resource Manager | Session Manager(同进程另一模块) |
|---|---|---|
| 形态 | **合并服务内的内部模块(无 App、无 prefix、无端口)** | 持唯一 App(`/api/session`)、注册对外 HTTP handler |
| 端口 / prefix | 无(进程内 Facade 调用,无对外 HTTP) | 唯一 App,prefix `/api/session` |
| 部署形态 | 编排态在共享 Redis(`resource_manager` 前缀),物理态以 K8s 为准 | 无状态 + 共享 Redis(`session_manager` 前缀)+ 共享 DB |
| 职责 | Pod 生命周期:deploy / idle→reclaim / min_idle 热备 / 死 Pod 探测 / cleanup(单 scope 占 Pod,容量由 SM `SCARD` 闸门保证) | session 级编排:准入 / 路由亲和 / TTL 老化 / config / 容量闸门 |
| 关键路径 | `acquire`(偶发扩容,非每请求;经 `rm_facade`) | `route`(每请求) |

---

## 2. 对外接口

> Resource Manager **无独立 App、无对外 HTTP prefix**:`acquire` / `idle_consider` 为 `ResourceManagerFacade` 进程内方法(签名见下文 §2.1 / §2.2),仅 session_manager 模块内部调用;`cleanup` 迁移到合并服务唯一 App 的 `POST /api/session/cleanup`(handler 在 session_manager,委托 `ResourceManagerFacade.cleanup()`,对应原 §2.3)。三个接口的入参/出参/语义逐字保留,仅传输形态变化;另有新增 Facade 方法 `update_pool_config`(config_sync 主动刷新,见 §2.2.1)。`metadata.request_id` 兼作幂等键,经 `rm_sysctx`(`resource_manager` 前缀)查;`scope_id` / `pool_config` 经方法参数传递,`pod_spec` 经参数传递。

### 2.1 `ResourceManagerFacade.acquire(...)` —— 请求 Pod(取 scope 暖 Pod 或 deploy +1)
- **in**:`{ scope_id:str, pod_spec:dict, pool_config:dict, request_id:str }`
  - `pod_spec` = deploy 字段子集(`agent_image`/`namespace`/`container_name`/`kubeconfig`/`readiness_*`/`nfs_*`/资源限额)+ **SSE 端口/路径**(⚠️ §13.1)。
  - `pool_config` = per-scope 池参数(`min_idle_pods`/`max_pods`(SM 派生)/`pod_ttl`/`pod_concurrency`),RM 首 acquire 缓存为 `scope:config`。`pod_concurrency` **仅用于 deploy follower 等待室推导上限(pc-1)**——per-Pod 容量闸门仍在 SM 侧(`SCARD < pod_concurrency`),RM 不做容量叠加判定(红线不变)。
  - **out**:`{ pod_id:str, pod_sse_url:str }`
  - **错**(Facade 抛异常):`MaxPodsReached`(对应原 `MAX_PODS_REACHED`)、`DeployFailed`(对应原 `DEPLOY_FAILED`)、`ValidationError`(对应原 `VALIDATION`)。错误码语义不变,见 §7;SM 的 `route` handler 捕获后映射为自身对外 HTTP 响应。
  - **语义**:按 `scope_id` 取 Pod——若 `scope:idle` 有暖 Pod 则复用,否则未达 `max_pods` → 选主 deploy +1,达 `max_pods` → `MaxPodsReached`。deploy 锁的**输家进 follower 等待室**(M8):原子闸门准入上限 `pod_concurrency-1`(leader 会话之外新 Pod 恰剩这些槽),overflow 严格快失败;等待有界(`ready_timeout`+余量),leader 失败(锁空闲且无进展)则 follower 直接失败**不接管**;检测到 leader 的 Pod 注册即**直接复用返回**(与 reuse 分支同构,SM 侧重跑仲裁)——RM 全程不读 SM 容量键。从 `scope:idle` 取暖 Pod 时**跳过 `deploy_ver` 不匹配当前 deploy 字段的**(A 类配置变更后的老版本暖 Pod 留在 idle 池按 `pod_ttl` 回收,不外发给新流量;见 HLD 场景 M / §2.2.1)。**config-agnostic**:不解析 `pod_spec` 语义,池参数经 `acquire` 传入并缓存于 `scope:config`。

### 2.2 `ResourceManagerFacade.idle_consider(...)` —— 该 scope 在该 Pod 上已无会话(⚠️ scope 级,修订)
- **in**:`{ pod_id:str, scope_id:str }`
  - **out**:`{ transitioned_to_idle:bool }`
  - **语义**:单 scope 占 Pod,Pod 上该 scope 0 session → 转入 `scope:idle` 暖池(`transitioned_to_idle=true`),供 reclaim / 复用判定。幂等(重复调用无副作用)。

### 2.2.1 `ResourceManagerFacade.update_pool_config(...)` —— 池参数 / deploy 字段主动刷新(config_sync 触发)
- **in**:`{ scope_id:str, pool_config:dict, pod_spec?:dict }`
  - `pool_config` = per-scope 池参数(`min_idle_pods`/`max_pods`(SM 派生)/`pod_ttl`);**A 类变更**(deploy 子集变更,新旧 `deploy_ver` 不等)时附带 `pod_spec` deploy 字段。
- **out**:`{ updated:bool }`
- **语义**:`HSET` 覆盖 `resource:scope:{scope_id}:config`(**幂等**,与 `acquire` 首建同款写入);A 类变更时**同时更新进程内 deploy 字段缓存**(仅 deploy 用,非编排态)。
- **效果**:autoscale / reclaim **立即**按新池参数执行(不再只等下次 acquire);`acquire` 的后续 deploy 用新 deploy 字段,且取暖 Pod 跳过 `deploy_ver` 不匹配的(§2.1)。
- **触发**:Session Manager 的 `config_sync` 处理流程(SM spec §4.3;语义权威见 HLD §6.2 场景 M「配置热更新」)。

### 2.3 `POST /api/session/cleanup` —— 运维批删(对应现 `cleanup_all_agentserver_pods`)
- **in**:`{ namespace?:str, label_selector?:str }`
  - **out**:`{ cleaned:int }`
  - **挂载点**:合并服务唯一 App(`/api/session`)下的 `POST /api/session/cleanup`,handler 在 session_manager,内部委托 `ResourceManagerFacade.cleanup(...)`。
  - **语义**:按 label 批删 K8s Pod(默认 `jiuwenclaw-component=agentserver`),**运维动作**(灾难恢复 / 重新部署 / 清孤儿 Pod);不操作 Redis 编排态(`scope:config` / `scope:idle` / `scope:pods` 等)。清完后 autoscale 重建 `min_idle_pods`。

> **反向调用**(Resource Manager → Session Manager,均经进程内 `SessionManagerFacade`,无 HTTP):
> - `sm_facade.notify_pod_dead(pod_id)` → `{invalidated:[session_id,...]}`。Pod **死亡**(K8s 探测)与 **reclaim**(主动回收)两种场景均发。
> - `sm_facade.reconcile_pods(view)`(`view = [{pod_id, scope_id}]`)→ `{stale:[{pod_id, scope_id}]}`。周期对账(每 30s),消除孤儿 Pod(§5.4.2)。

---

## 3. Redis 状态模型(编排语义态)

前缀:业务 key 以 `resource:` 开头;框架 `SystemContext.key_prefix` 设为 `resource_manager`(完整 key = `resource_manager:resource:...`)。**共享 Redis 实例**(`key_prefix=resource_manager`,与 Session Manager(`session_manager:`)**前缀隔离**,见 §10;合并后同一实例,原"不同进程/不同 DB 号"硬要求作废,见 §10)。**所有计数派生自集合(SCARD),不另设计数器 → 无漂移、崩溃安全。**

| 键 | 类型 | 内容 | 作用 |
|---|---|---|---|
| `resource:pod:{pod_id}:info` | HASH | `scope_id`, `pod_sse_url`, `pod_ip`, `namespace`, `phase`(created/idle/deleting), `created_ts`, `deploy_ver` | Pod 元信息;`scope_id` 标识所属 Pod 池;`deploy_ver` = 该 Pod deploy 子集 hash 指纹,acquire 版本过滤用(A 类变更后跳过老版本暖 Pod,§2.1 / §2.2.1) |
| `resource:pod:{pod_id}:idle_since` | STRING | Pod 转入 idle 的时间戳 | idle→reclaim 计时(reclaim sweeper 判定) |
| `resource:scope:{scope_id}:pods` | **ZSET** | `pod_id`(score=`created_ts`) | 该 scope 的全部 Pod 集(in_use ∪ idle);ZSET 保创建序 |
| `resource:scope:{scope_id}:idle` | SET | idle 的 `pod_id` | `SCARD` 对比 `min_idle_pods`(autoscale 闸门);excess 计 `pod_ttl` 回收 |
| `resource:scope:{scope_id}:config` | HASH | `min_idle_pods`, `max_pods`, `pod_ttl` | 首 acquire 存(config_sync 时经 `update_pool_config` **主动刷新**,§2.2.1),后续读;**不含 `pod_concurrency`**(SM 自用作 per-Pod 容量闸门,RM 不强制);`autoscale_interval` 全局默认,不入此 HASH |
| `resource:scope:{scope_id}:deploying` | SET | deploy 占位 token(uuid) | max_pods 判定含此(防并发超配);register/失败时清 |
| `resource:pods:all` | SET | 全部 `pod_id` | 对账 / RM 冷启动枚举 |
| `lock:rm:deploy:{scope_id}` | STRING(`SET NX EX`) | 选主标记 | per-`scope_id` deploy 串行(防并发 deploy 超配) |
| `lock:rm:autoscale` | STRING(`SET NX EX`) | 选主标记 | autoscale tick 级选主 |
| `lock:rm:reclaim` | STRING(`SET NX EX`) | 选主标记 | reclaim tick 级选主 |
| `lock:rm:reconcile` | STRING(`SET NX EX`) | 选主标记 | 对账 sweeper 选主 |
| `lock:rm:watch` | STRING(`SET NX EX`) | 选主标记 | K8s Watch + 死 Pod 轮询 + 健康 SSE 探测选主 |

**不变量(由 Lua 原子维护;崩溃/重启不破坏,因状态全在 Redis)**:
1. **一 Pod 恰属一 scope**:`resource:pod:{pod_id}:info.scope_id` 唯一标识所属 Pod 池;容量由 Session Manager 的 `SCARD(pod:{scope_id}:{pod_id}:sessions) < pod_concurrency` 闸门保证,无跨 scope 叠加。
   2. **idle 是 pods 子集**:`resource:scope:{scope_id}:idle ⊆ resource:scope:{scope_id}:pods`。
   3. **注册一致**:`resource:pods:all` 的每个 `pod_id` 同时存在于其 `info.scope_id` 对应的 `resource:scope:{scope_id}:pods`,且 `info.scope_id` 与之匹配。
   4. **物理为准**:Redis 的 `info` 描述的 Pod 必须在 K8s 中存在;漂移由 K8s Watch(实时)+ 对账 sweeper(周期)+ RM 冷启动扫 `pods:all` 校正。
   5. **idle 一致**:`resource:pod:{pod_id}:idle_since` 存在 ⟺ Pod 在 `resource:scope:{scope_id}:idle`(`scope_id = info.scope_id`)。

**键的生命周期(谁建 / 谁读 / 谁删 / TTL)**:

| 键 | 创建 | 读取 | 删除 | TTL |
|---|---|---|---|---|
| `resource:pod:{pod_id}:info` | LUA_REGISTER `HSET` | acquire / 对账 | LUA_PURGE `DEL` | 无 |
| `resource:pod:{pod_id}:idle_since` | LUA_RELEASE `SET` / LUA_REGISTER(idle_flag=true)`SET` | reclaim sweeper | reuse 时 `DEL` / LUA_PURGE | 无 |
| `resource:scope:{scope_id}:pods` | LUA_REGISTER `ZADD` | acquire / 对账 | LUA_PURGE `ZREM` | 无 |
| `resource:scope:{scope_id}:idle` | LUA_RELEASE `SADD` / LUA_REGISTER(idle_flag=true)`SADD` | autoscale / reclaim `SCARD` | reuse `SREM`(LUA_ACQUIRE)/ LUA_PURGE | 无 |
| `resource:scope:{scope_id}:config` | 首 acquire `HSET` | LUA_ACQUIRE / autoscale / reclaim | 无(随 scope 生命周期,长期) | 无 |
| `resource:scope:{scope_id}:deploying` | LUA_ACQUIRE(need_deploy)`SADD token` | LUA_ACQUIRE max_pods 判定 `SCARD` | LUA_REGISTER / deploy 失败 `SREM token` | 无(token 短命) |
| `resource:scope:{scope_id}:deploy_followers` | LUA_DEPLOY_FOLLOWER_GATE `ZADD request_id→deadline` | follower 闸门 `ZCARD ≤ pc-1` | follower 退出 `ZREM` / 闸门 `ZREMRANGEBYSCORE`(过期兜底) | 无(成员短命) |
| `resource:pods:all` | LUA_REGISTER `SADD` | 对账 / RM 冷启动 | LUA_PURGE `SREM` | 无 |
| `lock:rm:*` | sweeper `SET NX EX` | sweeper 判定 | 自动过期 / 下 tick 覆盖 | 见 §5.4 |

**无进程内状态**:Resource Manager 不持有任何进程内可变状态(框架硬约束)。所有键懒创建(首次 acquire/register 时建);进程重启不丢状态(全在 Redis+K8s)。进程级资源仅 K8s client 连接、K8s Watch 长连接、后台 asyncio task,由 `SystemContext` 子类在 `start()` 重建、`stop()` 释放。

---

## 4. `pod_spec` 与配置传入(config-agnostic)

Resource Manager **不查 config DB、不解析 template 语义**。所有部署与池参数由 Session Manager 经 `acquire` 传入,**按 `scope_id` 分组管理**(每 scope 独立 Pod 池、独立池参数):

- **首次** acquire 某 `scope_id`:Resource Manager 把 per-scope 池参数(`min_idle_pods`/`max_pods`/`pod_ttl`)存入 `resource:scope:{scope_id}:config`,deploy 字段(`pod_spec`)缓存于进程内(仅 deploy 用,非编排态)。
  - **后续** acquire 同 `scope_id`:复用已存 config,deploy 字段以本次传入为准(支持 template 更新;config 变更的生效见 §12.4)。**config_sync 时 SM 经 `rm_facade.update_pool_config` 主动刷新 `scope:config` 与进程内 deploy 字段缓存**(不再只等下次 acquire,见 §2.2.1)。
  - **`pod_concurrency` 不入 `scope:config`**:单 Pod 总并发上限由 Session Manager 自用作 per-Pod 容量闸门(`SCARD < pod_concurrency`);Resource Manager 不主动强制 `pod_concurrency`。

> Session Manager 侧对应:其 `route` 触发 `acquire` 时,从 template 行提取完整 `pod_spec`(deploy 子集 + SSE 端口/路径)+ per-scope 池参数 + `scope_id`。字段映射见 Session Manager spec §4.4。

---

## 5. 核心流程

### 5.1 Lua 脚本(承担所有编排态变更,原子)

> 脚本全集(6 个,实现与本文对齐):`LUA_ACQUIRE` / `LUA_REGISTER` / `LUA_RELEASE` / `LUA_PURGE`(核心 4 个,下文全文)+ `LUA_PLACEHOLDER`(实现期从 ACQUIRE 的占位逻辑抽出,供 autoscale 热备 deploy 专用——同样计入 max_pods 但**不碰 idle 池**,补位不该消耗既有暖 Pod)+ `LUA_DEPLOY_FOLLOWER_GATE`(M8:deploy 锁输家的等待室原子准入,ZSET+deadline,上限 `pod_concurrency-1`;先清过期成员再 ZADD 先行+超限自退——纪律同 SM 的 `LUA_WAITER_GATE`)。
>
> `LUA_DEPLOY_FOLLOWER_GATE(scope_id, follower_id, max_followers, deadline, now)` 全文:
> ```
> key = resource:scope:{scope_id}:deploy_followers
> ZREMRANGEBYSCORE(key, -inf, now)      # 清过期成员(等待进程崩溃兜底,不泄漏)
> ZADD(key, deadline, follower_id)     # 先行
> if ZCARD(key) > max_followers:
>     ZREM(key, follower_id)           # 超限自退
>     return {false}
> return {true}
> ```

**`LUA_ACQUIRE(scope_id, deploy_token, now)`** —— acquire 的原子核心:取 scope 暖 Pod 复用,或判定 need_deploy/max_reached(占位)。中途无并发插入(无 race)。
```
cfg = HGETALL(resource:scope:{scope_id}:config)                  # max_pods
if cfg 为空: return {action:"no_config"}                          # 首次 acquire 应由 handler 先建 config

# 1. 取 scope 暖 Pod 复用
pod_id = SPOP(resource:scope:{scope_id}:idle)
if pod_id:
    DEL resource:pod:{pod_id}:idle_since
    return {action:"reuse", pod_id, pod_sse_url:HGET(resource:pod:{pod_id}:info, "pod_sse_url")}

# 2. 无暖 Pod:判 max_pods(含 deploying 占位)
total = ZCARD(resource:scope:{scope_id}:pods) + SCARD(resource:scope:{scope_id}:deploying)
if total >= int(cfg.max_pods):
    return {action:"max_reached"}

# 3. 占位 deploy_token,返回 need_deploy(handler 选主 deploy)
SADD resource:scope:{scope_id}:deploying {deploy_token}
return {action:"need_deploy"}
```

**`LUA_REGISTER(pod_id, scope_id, pod_sse_url, pod_ip, namespace, deploy_token, idle_flag, now)`** —— deploy 成功后登记新 Pod(原子):一次性写 info/scope:pods/pods:all,清 deploying 占位。`idle_flag=true`(autoscale 暖备)则入 `scope:idle`。
```
HSET resource:pod:{pod_id}:info scope_id {scope_id}
           pod_sse_url {pod_sse_url} pod_ip {pod_ip} namespace {namespace}
           phase "created" created_ts {now}
ZADD resource:scope:{scope_id}:pods {now} {pod_id}
SADD resource:pods:all {pod_id}
SREM resource:scope:{scope_id}:deploying {deploy_token}      # 清占位
if idle_flag:
    SADD resource:scope:{scope_id}:idle {pod_id}             # autoscale 暖备
    SET resource:pod:{pod_id}:idle_since {now}               # 满足不变量⑤;reclaim sweeper 以 min_idle 底数保护
return {pod_id, pod_sse_url}
```

**`LUA_RELEASE(pod_id, scope_id, now)`** —— `idle_consider` 的原子核心:Pod 转入 `scope:idle` 暖池。重复调用幂等(`SADD`/`SET` 天然幂等)。
```
SADD resource:scope:{scope_id}:idle {pod_id}
SET resource:pod:{pod_id}:idle_since {now}
return {transitioned_to_idle:true}
```

**`LUA_PURGE(pod_id)`** —— Pod 死亡/reclaim 后清该 Pod 全部 Redis key(原子)。返回 `scope_id` 供 handler 决定后续(autoscale 补该 scope 暖备)。
```
scope_id = HGET(resource:pod:{pod_id}:info, "scope_id")
DEL resource:pod:{pod_id}:info
DEL resource:pod:{pod_id}:idle_since
if scope_id:
    ZREM resource:scope:{scope_id}:pods {pod_id}
    SREM resource:scope:{scope_id}:idle {pod_id}
SREM resource:pods:all {pod_id}
return {scope_id: scope_id}
```

### 5.2 acquire 编排(ResourceManagerFacade.acquire 方法)
```
# 幂等回放优先:同 request_id 重试直接返回上次结果,不重复 deploy。
cached = rm_sysctx.idempotency.get(metadata.request_id); if cached: return cached   # 幂等键 request_id 在 rm_sysctx(resource_manager 前缀)

scope_id, pool_config = metadata...
pod_spec = rawdata.pod_spec   # deploy 字段子集(image / namespace / ...)

# 首次见该 scope_id:存 scope:config(deploy 字段缓存进程内仅 deploy 用;per-scope 池参数入 Redis)
if not HEXISTS(resource:scope:{scope_id}:config):
    HSET(resource:scope:{scope_id}:config, min_idle_pods, max_pods,
         pod_ttl from pool_config)

deploy_token = uuid()
loop:
    result = redis.eval(LUA_ACQUIRE, scope_id, deploy_token, now)

    if result.action == "reuse":
        out = {pod_id: result.pod_id, pod_sse_url: result.pod_sse_url}
        rm_sysctx.idempotency.put(metadata.request_id, out); return out

    if result.action == "max_reached":
        SREM(resource:scope:{scope_id}:deploying, deploy_token)   # 清本请求占位
        raise MAX_PODS_REACHED(503)

    if result.action == "need_deploy":
        # 选主串行 deploy:抢 lock:rm:deploy:{scope_id}(或经 autoscale 同一选主通道),
        # 保证同 scope 不会并发 deploy 超配;deploy 是重操作(K8s create+wait Ready),串行可接受。
        if not acquire_lock("lock:rm:deploy:" + scope_id, ex=deploy_timeout):
            # 输家 → follower 等待室(M8):复用 leader 的 Pod,不再自建第 2 个空 Pod
            SREM deploying token
            out = await follow_leader(scope_id, pod_spec, pool_config, request_id)
            idempotency.put(request_id, out); return out
            # follow_leader 内部(见下):闸门准入(≤pc-1,overflow 快失败)→
            # 轮询:新 Pod 注册 → 返回该 Pod;锁空闲无进展(leader 失败)→ 失败不接管;
            # deadline(ready_timeout+余量)→ 失败;finally 必 ZREM 出室
        try:
            info = await k8s.deploy(pod_spec)                        # create + _wait_running_ready → pod_ip
            pod_id = info.pod_name
            pod_sse_url = build_sse_url(info.pod_ip, pod_spec)       # ⚠️ SSE 端口/路径(§13.1)
            redis.eval(LUA_REGISTER, pod_id, scope_id, pod_sse_url,
                       info.pod_ip, info.namespace, deploy_token, idle_flag=false, now)
        except DeployError:
            SREM(resource:scope:{scope_id}:deploying, deploy_token)
            raise DEPLOY_FAILED(503)
        finally:
            release_lock("lock:rm:deploy:" + scope_id)
        continue                                                     # 重跑 ACQUIRE:新 Pod 必被取作暖 Pod 复用
```
**并发安全**:`LUA_ACQUIRE` 单脚本原子(取暖 Pod + 移出 idle 不可分);`need_deploy` 走 `lock:rm:deploy:{scope_id}` 选主串行,避免并发 deploy 超配(SM spec §13.6 的"并发 acquire 超配"在此靠选主根治)。多个 `scope_id` 的 deploy 互不阻塞(按 scope 分锁)。锁输家经 follower 等待室复用 leader 成果——准入原子(`LUA_DEPLOY_FOLLOWER_GATE`,上限 pc-1)、等待有界、退出必清成员;跨副本冷竞争不再产生自建空 Pod。

### 5.3 idle_consider(ResourceManagerFacade.idle_consider 方法)
```
直接 redis.eval(LUA_RELEASE, pod_id, scope_id, now)
# scope_id 经方法参数传入(亦可从 resource:pod:{pod_id}:info 读做校验;单 scope 占 Pod,info.scope_id 唯一)。
# 返回 {transitioned_to_idle}。SADD/SET 天然幂等(重复/延迟抵达无副作用)。
# transitioned_to_idle=true 起 reclaim 计时(由 sweeper,非此处)。
```

### 5.4 后台任务(经 SystemContext 注入,各自选主)

| 任务 | 触发/周期 | 选主锁 | 流程 |
|---|---|---|---|
| **autoscale**(min_idle 热备) | 每全局 `autoscale_interval` 默认(0.5s) | `lock:rm:autoscale`(EX ≈ 2×interval) | per-`scope_id`:`SCARD(scope:idle) < min_idle_pods` 且 `ZCARD(scope:pods)+SCARD(scope:deploying) < max_pods` → 占位 deploy + `LUA_REGISTER(idle_flag=true)`(入 `scope:idle`;reclaim sweeper 以 min_idle 底数保护,见 §5.4.1) |
| **reclaim** | 每 1s | `lock:rm:reclaim`(EX 2s) | 见 §5.4.1(自治 reclaim,**无前置对账**;安全靠 SM `ZREM`) |
| **死 Pod 探测** | K8s Watch 长连 + 10s 轮询兜底 | Watch 每副本订阅(通知幂等);轮询发现+删除选主 `lock:rm:watch` | `monitor_pods_status` + Watch 事件 → `FAILED`/`NotFound`/`DELETED` → `LUA_PURGE` + K8s `delete`(若还在)+ `notify_pod_dead`(经 `sm_facade.notify_pod_dead`)。判死枚举:`Terminating`/`Failed`/`CrashLoopBackOff`/`ImagePullBackOff`/`ErrImagePull`/`InvalidImageName`;`Pending` 不判死 |
| **半死 Pod 健康探测**(HLD 场景 N) | 10s(与轮询同频,复用 `lock:rm:watch` 选主) | 同上 | 对 `pods:all` 每个 Pod 探测 `GET http://{pod_ip}:{sse_port}/health`(AgentServer 固定约定端点);**连续 2 次失败判半死**,按死 Pod 处理(`LUA_PURGE` + K8s `delete` + `notify_pod_dead`)。连续阈值防瞬时抖动误杀;探测恢复无动作。K8s Running/Ready 但 SSE hang 死只能靠此探测发现(K8s Watch 看不到) |
| **孤儿对账 sweeper** | 每 30s | `lock:rm:reconcile`(EX 60s) | 见 §5.4.2(Redis↔K8s 孤儿对账 + RM↔SM stale Pod 对账,**经 Facade、不读 SM 模块 key**) |

#### 5.4.1 reclaim(自治,无前置对账)
```
每 tick(抢 lock:rm:reclaim):
  for scope_id in 所有 scope:
    min_idle = HGET(scope:config, min_idle_pods); pod_ttl = HGET(scope:config, pod_ttl)
    idle_pods = SMEMBERS(resource:scope:{scope_id}:idle)
    # 不动 min_idle 底数:只考虑 idle 数 > min_idle 的部分
    excess = idle_pods 排除最早 min_idle 个(保底热备)
    for pod_id in excess:
        if now - int(GET(resource:pod:{pod_id}:idle_since)) < pod_ttl: continue   # 未到期
        # 无前置对账(不读 SM):reclaim 安全性由 SM 侧 ZREM 契约保证(见下)
        k8s.delete(pod_id)
        redis.eval(LUA_PURGE, pod_id)
        sm_facade.notify_pod_dead(pod_id)                  # 覆盖 reclaim 场景,清 SM 注册
```
> **自治 reclaim 安全保证**:不再读 SM Redis,安全性靠 SM→RM 单向契约:Session Manager `idle_consider` 时已**原子 `ZREM scope:{scope_id}:pods`**(SM sweeper 单 Lua,SM spec §5.4)→ 该 Pod 移出 route 候选 → reclaim 窗口内 SM 不会再 route 新 session 到它。
>
> Pod 转入 idle(`scope:idle` + 起 `idle_since` 计时)由 `idle_consider` 触发的 release 产生(`LUA_RELEASE`),亦由 autoscale 暖备 deploy 产生(`LUA_REGISTER(idle_flag=true)`);`idle_consider` 丢失时 Pod 仍可能在 SM 侧 `scope:pods`(SM 未 ZREM)→ SM 可能继续 route,由孤儿对账 sweeper(§5.4.2)兜底释放(SM 侧 60s `idle_notified` 过期重发自愈,期间仅资源暂留)。

#### 5.4.2 孤儿对账 sweeper(Redis↔K8s + RM↔SM,经 Facade、不读 SM 模块 key)
```
每 tick(抢 lock:rm:reconcile,默认 30s):
  # 1. Redis↔K8s 孤儿对账(K8s Watch 通常已先触发,此为兜底)
  for pod_id in SMEMBERS(resource:pods:all):
      if not k8s.exists(pod_id):
          redis.eval(LUA_PURGE, pod_id); sm_facade.notify_pod_dead(pod_id)

  # 2. RM↔SM 对账:消除「RM 有 Pod、SM 已不 route」的孤儿 Pod(idle_consider 丢失 / SM 重启漂移)
  view = [ {pod_id, scope_id: HGET(resource:pod:{pod_id}:info, "scope_id")} for pod_id in SMEMBERS(resource:pods:all) ]
  resp = sm_facade.reconcile_pods(view)                  # SM 对每个 (pod,scope) 查 scope:{scope}:pods 成员,非成员=stale
  for {pod_id, scope_id} in resp.stale:
      redis.eval(LUA_RELEASE, pod_id, scope_id, now)     # Pod 转 idle → reclaim sweeper 按 pod_ttl 回收
```
> Pod idle 转换一致性的兜底:主路径 = SM `idle_consider`;**`idle_consider` 丢失 / SM 重启漂移** 由本 sweeper 的 RM↔SM 对账(每 30s,经 Facade `reconcile_pods`)兜底——RM 将 SM 已不 route 的 stale Pod 转 idle,后续按 `pod_ttl` 回收。**不读 SM 模块 key**(SM 读自身 Redis、经 Facade 返回 stale);stale 判定 = SM 不再 route 该 Pod(`scope:{scope_id}:pods` 非成员),不误杀有活跃会话的 Pod。两模块共享 Redis(各自前缀)跨重启不丢,正常重启无需对账;对账仅兜底消息丢失/漂移。

### 5.5 冷恢复
- **Session Manager 冷启**:靠 SM 自身 Redis(AOF/RDB)跨重启不丢,无需从 Resource Manager 重建 pod 注册;不提供 `list_pods` / 枚举 Pod 端点。
  - **Resource Manager 冷启**(自身重启):Redis 编排态(AOF/RDB 持久化)不丢;启动扫 `resource:pods:all`,逐个查 K8s,K8s 不存在的孤儿 → `LUA_PURGE` + `notify_pod_dead`(兜 Watch 漏报)。K8s 有、Redis 无的(手动建/上轮残留)→ 保守忽略(不知 `scope_id`/`pod_sse_url`,无法纳入;后续靠 `cleanup` 清理)。
  - **Redis 持久化是硬要求**:Resource Manager 编排态(idle/info)在**共享 Redis**(`resource_manager` 前缀),**必须开 AOF/RDB**(部署要求,§11),否则 flush 后 idle/info 丢失 → reclaim/autoscale 失效(只能靠 K8s Watch + 对账逐步重建,期间可能短暂资源泄漏)。Redis flush 属灾难性丢数据(会话/TTL 态同时丢失),不在恢复目标内。

---

## 6. 正确性与原子性论证

| 性质 | 保证方式 |
|---|---|
| 并发 acquire 取暖 Pod 无 race | `LUA_ACQUIRE` 单脚本内完成取暖 Pod + 移出 idle(不可分);容量由 SM `SCARD < pod_concurrency` 闸门保证 |
| 并发 deploy 不超配 | `need_deploy` 走 `lock:rm:deploy:{scope_id}` 选主串行;`deploying` 占位计入 max_pods 判定 |
| 计数无漂移 | idle 用 SCARD,派生不另存计数器 |
| reclaim 安全(SM 不再 route 新 session 到该 Pod) | SM `idle_consider` 时**原子 `ZREM scope:pods`**(reclaim 窗口内 SM 不再 route 新 session 到该 Pod) |
| idle 转换一致(idle_consider 丢/重复) | 丢失:SM 60s `idle_notified` 过期重发;`SADD`/`SET` 天然幂等,重复/延迟抵达无副作用 |
| 崩溃安全 | 崩溃只可能"LUA 已提交已返回"或"未提交";deploy 中崩溃 → `deploying` 占位残留,由对账/超时清理(不超 max_pods 长期) |
| 死 Pod 不留孤儿 | K8s Watch(实时)+ 轮询(10s 兜底)+ 对账 sweeper(Redis↔K8s)+ RM 冷启动扫 `pods:all`,四重清理;均 `LUA_PURGE` + `notify_pod_dead` |
| 孤儿 Pod(RM 有 Pod、SM 已不 route)不泄漏 | 主路径 SM `idle_consider` 转 idle;`idle_consider` 丢失 / SM 重启漂移由孤儿对账 sweeper 每 30s 经 Facade `sm_facade.reconcile_pods` 兜底——RM 将 SM 已 `ZREM` 的 stale Pod 转 idle → 按 `pod_ttl` 回收。stale 只删 SM 已 ZREM 的对(SM 不再 route 到它),不误杀有活跃会话的 Pod |
| 幂等 | `acquire` 包在 `rm_sysctx.idempotency`(key=request_id);`idle_consider` 的 `SADD`/`SET` 天然幂等;`LUA_ACQUIRE` reuse 分支取暖 Pod 幂等 |
| 多副本不重复 deploy/reclaim | autoscale/reclaim/deploy 各自 tick 级选主锁,全局单副本执行写操作 |

---

## 7. 错误码与边界

- `MAX_PODS_REACHED`(503,可重试):该 `scope_id` 的 Pod 数达 `max_pods`(含 `deploying` 占位)。`Retry-After` 估算 = 最近一个 idle Pod 的 `pod_ttl` 剩余(保守)。
  - `DEPLOY_FAILED`(503,可重试):K8s create / `_wait_running_ready` 失败。
  - `VALIDATION`(400,不可重试):缺 `scope_id`;`pod_spec` 必填字段缺失。
  - 边界:
    - `min_idle_pods == 0` → 无热备,Pod 用完即回收。
    - `max_pods` 达上限 → acquire 返回 `MAX_PODS_REACHED`,Session Manager 侧映射为 `NO_POD_AVAILABLE`(SM spec §2.1)。

> **过载信号契约**(同 Session Manager §8):过载响应(`MAX_PODS_REACHED`/`DEPLOY_FAILED`)带 `error_code` + `Retry-After`;gateway 据此判可重试与退避。`VALIDATION` 不可重试。Resource Manager 不在每请求关键路径(acquire 仅偶发扩容),过载风险远低于 Session Manager;主要保护是 max_pods 封顶 + deploy 选主串行。

---

## 8. 安全性与其他生产特性

### 8.1 认证与授权
三个 endpoint 当前**无鉴权**——其中 `cleanup` 是高危写接口(批删 Pod),`acquire` 触发实际 deploy(资源消耗)。
- Session Manager ↔ Resource Manager、运维 ↔ Resource Manager 一律 **mTLS 或 token**(复用框架 foundation `link_auth`)。
  - `cleanup` 额外校验调用方身份(仅运维可调)。
  - v1:内网 mTLS + endpoint 级 caller 校验。

### 8.2 输入校验与键命名安全
`pod_id` / `scope_id` 由外部(Session Manager)可控,且直接拼进 Redis 键(`resource:scope:{scope_id}:pods`、`resource:pod:{pod_id}:info` 等)。未校验会键注入/解析错乱。缓解:handler 入参白名单(字符集 + 长度上限 + 非空);`scope_id` 派生由 Session Manager 侧保证(其 spec §9.2 已加安全分隔符);`pod_id` 由 Resource Manager 自己生成(deploy 时),外部 `idle_consider`/`notify_pod_dead` 的 `pod_id` 校验存在于 `pods:all`。数值字段(`min_idle_pods`/`max_pods`/`pod_ttl`)校验上下限。

### 8.3 Redis 持久化与冷启动恢复
见 §5.5。Redis 必须开 AOF/RDB;两模块共享 Redis(各自前缀)持久化跨重启不丢,无需互相重建;Resource Manager 冷启扫 `pods:all` 对账 K8s。

### 8.4 K8s API 限流保护
deploy/delete/watch 均打 K8s API。缓解:deploy 选主串行(天然限流);Watch 用 informer 共享缓存(长连,非轮询);轮询 monitor 兜底周期 10s(可配)。大规模(多 scope_id)下 watch 按集群 label 订阅(非逐 Pod)。

---

## 9. 模块文件结构

**核心包**(业务逻辑,与 Session Manager `session_manager/` 同进程、各自独立包;无 App、无对外 HTTP):
```
agent-runtime/management/openjiuwen_runtime/management/resource_manager/
  __init__.py
  orchestrator.py     # acquire 编排(pick/deploy/register)+ idle_consider(原 handler 逻辑搬此,作 ResourceManagerFacade 方法)
  state.py            # Redis 键 schema(per-scope 池:scope:{scope_id}:pods/idle/config/deploying)+ 不变量(封装 rm_sysctx.redis 唯一出口)
  lua_scripts.py      # LUA_ACQUIRE / LUA_REGISTER / LUA_RELEASE / LUA_PURGE / LUA_PLACEHOLDER 文本(per-scope 键)
  k8s.py              # K8s 交互(移植 K8sServiceHandler:deploy/delete/watch/monitor/cleanup)+ IDeployController/K8sDeployController/NoOpDeployController;Pod 打 jiuwenclaw-component=agentserver 等 label
  sweeper.py          # 后台:autoscale / reclaim(自治,均 per-scope)/ 死Pod探测 / RM↔K8s 孤儿对账 sweeper,各带选主锁;持有 SessionManagerFacade 引用(进程内调 notify_pod_dead/reconcile_pods)
  facade.py           # ResourceManagerFacade:acquire / idle_consider / cleanup(scope_id 作池键)
  models.py           # 入参/出参 dataclass、错误码常量、PodSpec、PodDeployInfo
```
**可运行壳**(本服务唯一壳;不再有独立 `applications/resource_manager/`):
```
agent-runtime/applications/orchestrator/            # 合并服务的唯一可运行壳(SM+RM 同进程)
  main.py             # 构造 sm_sysctx + rm_sysctx + 两 Facade(互持引用)+ 一个 App(prefix=/api/session)+ 注册 SM handler + 起 SM/RM 两套后台任务
  config.py           # 一个端口 / 一个 Redis URL / 一个 DB / K8s kubeconfig / 各 task 周期默认
  pyproject.toml
```
> 框架扩展(§0.4)复用 Session Manager M0(`ctx.redis`),无需再改框架。sweeper/K8s Watch/autoscale 生命周期经 `SystemContext` 子类 `start()`/`stop()` 注入。改动均在嵌套仓库 `agent-runtime/`(branch `develop`),单独提交。

---

## 10. 部署与 HA

- 与 Session Manager **同一部署单元**(合并服务),前置 LB;gateway 调任意副本。编排态在**共享 Redis**(同一实例,前缀 `resource_manager`,与 SM 前缀隔离;高可用 + AOF/RDB 持久化),Pod 物理态在 K8s。**作废**原"SM/RM 须配不同 Redis 进程或不同 DB 号"硬要求(同进程同信任域,模块边界由 Facade 强制)。
  - autoscale/reclaim/deploy/对账 各自 tick 级选主,多副本安全。
  - Redis 不可用 = 编排态不可读写 → `acquire`/`idle_consider` fail-fast(不降级到内存,Pod 池编排失效)。K8s 不可用 = deploy/delete 失败 → `DEPLOY_FAILED`;现有 Pod 继续服务(只影响扩缩容)。
  - 运维清理:调 `POST /api/session/cleanup` 按 label 批删 AgentServer Pod(灾难恢复 / 重新部署 / 清孤儿)→ autoscale 重建 `min_idle_pods`。

---

## 11. 测试策略

- **单元(fakeredis)**:`LUA_ACQUIRE`(reuse 取暖 Pod / need_deploy / max_pods 封顶 / 幂等)、`LUA_REGISTER`(含 `idle_flag` 分支)、`LUA_RELEASE`(转 idle / 幂等)、`LUA_PURGE`、不变量(§3)。⚠️ fakeredis ZSET/HASH/EVAL 需验证(同 Session Manager 已知陷阱:消费组 id=`"0"` bug、pubsub 需共享 FakeServer);不支持则相关用例移真 Redis 层。
  - **K8s 单元**:mock K8s client(deploy/wait Ready/delete/Watch 事件)+ `NoOpDeployController`;`cleanup_all_agentserver_pods` label 选择器。
  - **组件(直接调 `ResourceManagerFacade` + stub K8s + stub `SessionManagerFacade`)**:acquire 全链路(reuse / need_deploy→deploy→register)、`idle_consider` 转 idle、`cleanup`。sysctx 惰性建(进程内手构 SystemContext 不跑 App lifespan,需手动 start/stop)。
  - **reclaim / 对账专项**:① reclaim 自治安全:SM `ZREM scope:pods` 后即使 `pod_ttl` 到期,该 Pod 也已移出 route 候选(reclaim 窗口内 SM 不再 route 新 session 到它);② Redis↔K8s 孤儿对账;③ RM↔SM 孤儿对账:构造「RM 有 Pod、SM 已 `ZREM`」的 (pod,scope) → `reconcile_pods` 返回 stale → RM `LUA_RELEASE` 转 idle → 按 `pod_ttl` 回收;且 SM 仍 route 的活跃 Pod 不被误判 stale。
  - **集成(真 Redis + fake/minikube K8s)**:多副本选主(autoscale/reclaim/deploy 不超配)、K8s Watch 驱动 `notify_pod_dead`、运维 `cleanup` + autoscale 重建、RM↔SM `reconcile_pods` 驱动孤儿 Pod 释放。

---

## 12. 待确认假设 / 开放项

1. **`pod_sse_url` 的 SSE 端口/路径字段**(⚠️ 阻塞 deploy 实现细节,非架构):bypass 下 Pod 暴露 SSE 端点(现 SDK 是 WS `invoke_path`)。`pod_spec` 需新增 SSE 端口/路径字段,与 Session Manager template 对齐 —— 留实现计划,需确认 AgentServer Pod 的 SSE 端点契约。
   2. ~~**reclaim 的 scope 追踪**~~(已废弃):原设计 reclaim 前置对账需读"该 Pod 曾被哪些 scope 引用"以查 SM(`scope_history` SET)。**不共享 Redis 后已删除**:reclaim 自治不再枚举 scope 查 SM(§5.4.1)。
   3. **deploy 选主粒度**:当前设计 `lock:rm:deploy:{scope_id}`(per-scope 串行)。若单 scope acquire 并发高,deploy 串行成瓶颈 → 可改 per-scope 并发上限(N 个 deploy 并行)。v1 串行,留 follow-up。
   4. ~~**config 变更生效**~~(✅ **已解决,2026-08-12**):config_sync 时 SM 经 `rm_facade.update_pool_config` **主动推送**,池参数(`min_idle_pods`/`max_pods`/`pod_ttl`)与 deploy 字段**立即生效**(不再只等下次 acquire;见 §2.2.1);**A 类**(deploy 子集)变更同时触发 SM 侧 `ZREM` 软摘除 + acquire 版本过滤(见 HLD §6.2 场景 M「配置热更新」)。已驻留 Pod 不回溯重建(镜像升级走自然滚动 / 运维 `cleanup` 批删重建)。
   5. **集群 Pod 总数上限**:`max_pods` 是 per-`scope_id`;多 `scope_id` 共存时无全局 Pod 总数上限(K8s 集群容量保护)。可加全局 `max_total_pods`(RM config)—— 留 follow-up,非 v1。
   6. **Pod idle 转换一致性的实时性**:`idle_consider` 丢失后由 SM 侧 60s `idle_notified` 过期重发 → 最长 60s + pod_ttl 才回收。可接受(资源短暂浪费,不影响正确性)。重复/乱序 `idle_consider` 的 `SADD`/`SET` 幂等,无副作用。
   7. **K8s Watch 多副本**:Watch 每副本订阅(通知幂等),但 `LUA_PURGE` + K8s delete 可能多副本重复触发(`LUA_PURGE` 幂等安全,K8s delete NotFound 安全)。确认无副作用 —— 实现阶段验证。

---

## 13. 与现有代码的对应

| 现有(将移植/参考) | 用途 | 处置 |
|---|---|---|
| `session/` SDK `ServiceManager`:autoscale(`min_idle`)、idle→reclaim、`_bootstrap_min_idle`、失效 Pod 监控 | 控制面/池逻辑 | **移植为 Redis 状态 + 选主后台任务**(进程内 dict → Redis HASH/SET/ZSET;进程内 Timer → idle_since + sweeper) |
| `session/` SDK `K8sServiceHandler`:`deploy`/`delete`/`monitor_pods_status`/Watch/`cleanup_all_agentserver_pods` | K8s 交互层 | **近原样复用**(移植到 `k8s.py`;Pod label 用 `jiuwenclaw-component=agentserver` 等) |
| `session/` SDK `runtime.py`:`IDeployController`/`K8sDeployController`/`NoOpDeployController` | deploy 抽象 | **复用** |
| `session/` SDK `ServiceHandler._session_reserved` | 防超卖预留 | **由 Session Manager 的 `SCARD(pod:{scope_id}:{pod_id}:sessions) < pod_concurrency` 闸门承担**(进程内 dict → SM 侧 Redis);Resource Manager 不维护 `reserves` HASH(单 scope 占 Pod,无跨 scope 叠加) |
| `session/` SDK `models.py`:`AccessConfig` | 池配置 | **字段拆分**:per-scope 池参数(`min_idle_pods`/`pod_ttl`;`max_pods` SM 派生)经 `pool_config` 传入 RM,deploy 字段经 `pod_spec` 传入;`autoscale_interval` 全局默认(RM 服务级,不入 `scope:config`/`pool_config`/`pod_spec`);`pod_concurrency` SM 自用作 per-Pod 容量闸门,不入 RM `scope:config` |
| `session/` SDK 数据面(`WSServiceMessageChannel`/`response_queue`/`dual_queue`) | 数据面 | **弃用**(旁路定位) |
| `openjiuwen_runtime.service`(App/Envelope/RequestContext/Redis 原语 + `ctx.redis`) | 服务框架 | **直接使用**(`ctx.redis` 由 Session Manager M0 已加) |
| Session Manager `session_manager/`(app/handlers/orchestrator/state/lua_scripts/sweeper 结构) | 同范式参考 | **镜像结构**(RM 包 `resource_manager/` 同构) |
