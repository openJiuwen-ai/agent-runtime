# service-core 规格(组装 / 配置 / 错误码 / 字段分类 / 部署)

> 覆盖 `src/agent_runtime/` 顶层文件:`main.py`、`cli.py`、`config.py`、`errors.py`、`spec_fields.py`、`util.py`,及可观测性三件:`logsetup.py`(日志装配)、`metrics.py`(请求指标)、`visualization_api.py`(可视化端点)。
> SM/RM 模块内文件见 [session-manager.md](session-manager.md) / [resource-manager.md](resource-manager.md)。

## main.py —— 组装入口(唯一可运行的壳)

### build_resources(settings, arc) → (redis, db, k8s)

双模式构造共享物理资源:

| mode | redis | db | k8s |
|---|---|---|---|
| `local` | fakeredis(进程内) | 文件型 SQLite(`AGENT_RUNTIME_SQLITE_PATH`,默认 `./agent_runtime_local.db`;`:memory:` 在连接池下会丢表) | `FakeK8sPodClient` |
| `server` | `build_redis_client(settings)` | `build_db_handler(settings)`,**None 即 RuntimeError**(禁 SQLite 回退,fail-fast) | `RealK8sPodClient` |

### OrchestratorSystemContext(SystemContext 子类)

SM 侧 ctx,级联管理全部生命周期(框架 App 的 lifespan 只认一个 ctx_factory——返回本类):

- 构造时同时建 **rm_sysctx**(同 redis/db,仅 `key_prefix` 不同:SM=`{session_manager}`,RM=`{resource_manager}`;前缀带 Redis Cluster hash tag,模块键域同槽,详见 `docs/feature/2026-08-redis-cluster.md`)。**rm_sysctx 必须显式 `_owns_db=False, _owns_redis=False`**(2026-09 加固):框架缺省语义是「传了 db 即拥有」——不传则 `start()` 对共享 DB handler 二次 `init_database()+connect()`(重建 engine、旧池泄漏一条连接),`stop()` 会 dispose SM ctx 还在用的共享 engine;非拥有态仍保留 db readiness/redis ping 双前缀健康检查。
- `_bind_modules()`:先构造后绑定,破解 SM↔RM 循环引用——
  `SessionState`/`ResourceState` → `SessionManagerFacade`/`ResourceManagerFacade(ResourceOrchestrator)` → `ConfigStore(push_pool_config=rm_facade.update_pool_config)` → `SessionOrchestrator` → `SessionSweeper`/`ResourceSweeper`。
- `_build_jobs()`:7 个后台任务,全部 `create_single_leader_job`(tick 级 Redis 选主锁,多副本全局单副本执行;`tick_timeout_sec` 取 `TICK_TIMEOUTS` 常量表——单次 tick 上限,防 redis/k8s IO 抖动挂死 `_run_forever` 循环,超时取消本拍记日志、下一拍重试):

| 任务 | tick | tick 超时 | 锁键(`agent_runtime:job:*`) | 动作 |
|---|---|---|---|---|
| sm_sweep | `sweep_interval`(1s) | 30s | `sm_sweep` | 到期 pass + 空 Pod pass |
| rm_autoscale | `autoscale_interval`(1s) | 370s(盖住 deploy ready_timeout 300 + DEPLOY_LOCK_TTL 360) | `rm_autoscale` | min_idle 热备补位 |
| rm_reclaim | `reclaim_interval`(1s) | 60s | `rm_reclaim` | idle 超 pod_ttl 回收 |
| rm_watch | `watch_interval`(10s) | 300s | `rm_watch` | 死 Pod 判定 + 健康探测 |
| rm_reconcile | `reconcile_interval`(30s) | 300s | `rm_reconcile` | 孤儿/stale 对账 |
| sys_sample | `eval_sample_interval`(30s,钳 5) | 30s | `sys_sample` | 自评估:per-scope 池态+计数快照采样(spec/evaluation.md) |
| sys_eval | `eval_interval`(300s,钳 30) | 120s(盖住 LLM timeout 60) | `sys_eval` | 自评估:规则引擎(+可选 LLM)产报告落 Redis |

另有**非选主**任务 telemetry flusher(2026-09):`start()` 起、每副本一个,5s
周期 drain 热路径计数缓冲 → `{agent_runtime:eval}:ct:scope:{sid}` HINCRBY
(10s 超时防御,失败留到下轮);`stop()` cancel + 终结 drain。

- `start()` 顺序:super().start() → rm_sysctx.start() → k8s.start()(**失败仅降级扩缩容,不阻断启动**)→ 启动 7 个 job + telemetry flusher。`stop()` 逆序且**逐步兜底**(2026-09 加固:jobs/k8s/rm_sysctx/super().stop() 每段独立 try/except,单组件停机失败只留痕不阻断其余资源回收——连接泄漏比单点报错难排查)。
- DB 表初始化:构造参数 `table_definitions=[SERVICE_CONFIG_TEMPLATE_TABLE_DEF, SERVICE_CONFIG_CONTAINER_TABLE_DEF, ROUTING_SCOPE_TABLE_DEF]`(表结构在 `config_store.py`)。

### create_app(settings, arc, *, resources=None, instance_id=None, own_resources=True)

构造唯一 App(`prefix=/api/session`,`enable_ws=False`)+ `/healthz` + `/visualization/*` + 5 个 handler;
`app.use(request_metrics_middleware(registry))` 挂请求汇总中间件,registry 存 `app.asgi.state.metrics`(双实例测试各自独立)。
`resources`/`instance_id`/`own_resources` 仅供多实例测试注入共享物理资源(`tests/integration/_dual_harness.py`);生产路径不传。

### /healthz(main.py:_register_healthz)

进程就绪探针(K8s probe / deploy_replicas.sh 就绪轮询 / e2e 实例观测)。sysctx 未就绪 → 503;就绪返回 `{ok, instance_id}`。
**坑**:模块顶部 `from __future__ import annotations` 下,FastAPI 经 `get_type_hints` 用模块全局解析注解——`Request` 必须顶层 import,函数内局部导入会被当成 query 参数(422)。`visualization_api.py` 同源坑见下。

## 网络/IO 抖动超时兜底(框架层,server 模式生效)

本模块 redis/db 客户端均经框架构建,socket 级超时在构建点注入(网络黑洞/TCP 半开时 await 不再永久挂起):

- **Redis**(`service/bootstrap.build_redis_client`,env 可调,0=关闭该项):
  `OPENJIUWEN_SERVICE_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS`(默认 3,建连)、
  `OPENJIUWEN_SERVICE_REDIS_SOCKET_TIMEOUT_SECONDS`(默认 5,命令读写;本项目无 BLPOP 等长阻塞命令,短超时安全)、
  `OPENJIUWEN_SERVICE_REDIS_HEALTH_CHECK_INTERVAL_SECONDS`(默认 30,空闲连接周期 PING 验活)、
  `OPENJIUWEN_SERVICE_REDIS_RETRY_ATTEMPTS`(默认 3,连接类错误指数退避重试)。
  **密码**:`REDIS_PASSWORD`(裸名,无 `OPENJIUWEN_SERVICE_` 前缀——deploy tool 从 Secret
  envFrom 注入约定;2026-08-28 引入)非空时作为客户端 `password` kwarg 注入,避免明文进
  `OPENJIUWEN_SERVICE_REDIS_URL`;**同设时 kwarg 覆盖 URL 内嵌密码**。
  **Cluster 支持**:`OPENJIUWEN_SERVICE_REDIS_URL` 用 `redis+cluster://`(TLS 用 `rediss+cluster://`)
  scheme 即构造集群客户端(`RedisCluster.from_url`,一种子节点即可,拓扑自发现;cluster 只有
  db 0,URL 带库号会在构建点直接报配置错误)。多键 Lua 的同槽前提由键前缀 hash tag 保证。
  本地 local 模式 fakeredis 不经此路径,不受影响。
- **MySQL**(`foundation/db`:`RUNTIME_DB_CONNECT_TIMEOUT`/`DB_CONNECT_TIMEOUT`,默认 5s):aiomysql 建连超时(mysql 系 driver 自动注入,调用方显式 connect_args 优先)。查询读超时 aiomysql 无参数,由请求级 deadline 兜底。
- **PostgreSQL**(`DB_TYPE=postgresql` → `PostgreSQLHandler`,asyncpg 驱动;服务框架 bootstrap/ServiceConfig 已接入,必填校验同 mysql,默认端口 5432;K8s 部署经 `deploy/agent_runtime.env` 的 `AGENT_RUNTIME_DB_TYPE` 切换,连接参数同组 `AGENT_RUNTIME_DB_*`):建连 `timeout` 与命令 `command_timeout` 均注入——asyncpg 建连默认 60s、命令默认**无限制**,不注入则慢查询可永久挂起。`timeout` 复用 `RUNTIME_DB_CONNECT_TIMEOUT`(默认 5s);`command_timeout` 独立旋钮 `RUNTIME_DB_COMMAND_TIMEOUT`/`DB_COMMAND_TIMEOUT`(默认 30s,低于请求级 deadline)。`init_database` 的临时引擎(CREATE DATABASE/SCHEMA)同款注入。
- **请求级总兜底**:`OPENJIUWEN_SERVICE_REQUEST_TIMEOUT_SECONDS`(部署模板 70s)经框架 router 的 `asyncio.timeout` 硬包全部 handler——redis/db 挂起最坏 70s 后取消该请求。
- **启动 fail-fast**:框架 `SystemContext.start()` 的 readiness 探活(ping/SELECT 1)带 10s 上限,超时按失败处理(fail-fast 不变成 fail-hang)。

## cli.py —— 命令行入口

`deploy.sh` 调用。参数:`--mode local|server`(必填)、`--env-file`(dotenv 先加载,`override=False`)、`--host`/`--port`(覆盖 `OPENJIUWEN_SERVICE_*`)。流程:load_dotenv → 设 `AGENT_RUNTIME_MODE` → 框架 import(其导入期 `setup_logging`→dictConfig 会重置 root,**任何更早的 basicConfig 都是死配置**)→ `logsetup.configure_logging()` 收口 → `ServiceConfig.from_env()` + `AgentRuntimeConfig.from_env()` → `create_app` → uvicorn.run(`log_level` 跟随 `AGENT_RUNTIME_LOG_LEVEL`)。

## 可观测性(日志 / 可视化端点)

三个文件:`logsetup.py`(日志装配)、`metrics.py`(请求指标 + 汇总中间件)、`visualization_api.py`(/visualization/* 端点)。

### logsetup.py —— 日志装配

- `configure_logging()`(仅 cli.py 调用;pytest 勿调——会改写 root handler):
  1. 重放框架 `setup_logging()`(读取 `OPENJIUWEN_RUNTIME_LOG_FILE=disabled` 等);
  2. `AGENT_RUNTIME_LOG_LEVEL` → root 级别;handler 级别**只放宽不收紧**(yaml 钉死的 INFO 不被覆盖,DEBUG 请求可穿透);非法值 WARNING 并回退 INFO;
  3. `httpx`/`httpcore` → WARNING(健康探测刷屏降噪);
  4. `install_request_context()`。
- **请求关联**:contextvars + root 级 `_RequestContextFilter`/`_ContextFormatter`。请求处理期间的日志行尾部追加 `| request_id=… session_id=… endpoint=… instance=…`;后台任务行(无上下文)与原格式逐字节一致。中间件负责 set/reset(`metrics.py`)。
- **stdout 主形态**:`OPENJIUWEN_RUNTIME_LOG_FILE=disabled|off|none|false` → 框架 `setup_logging` 从 dictConfig 三处(handlers/root/loggers)移除 file handler(K8s 模板已固定注入;容器文件随 pod 丢失且无处导出)。留空则维持文件 handler(宿主机调试可用)。

### 日志契约(排障入口)

- **每请求一行汇总**(INFO,`agent_runtime.metrics`):`request: endpoint= route outcome=ok error_code=- duration_ms=11.0 | request_id=…`——五个端点全覆盖(含 touch/config_refresh/cleanup),与 uvicorn access 行经 request_id 关联。
- **慢请求分诊**(WARNING,同 logger):汇总后 `duration_ms` 超 `_SLOW_REQUEST_MS`(2s)补 `request slow: endpoint= …`——冷部署(ready_timeout 量级)会合法超阈,本行只作"值得看一眼"入口,精确语义以汇总行 duration_ms 为准。
- **touch 未命中 INFO**(`touch missed: session=…`):会话过期/gateway 回退重新 route 的排障入口;命中保持 DEBUG(保活高频防刷屏)。
- **前置校验失败留痕**(WARNING,框架 RestAdapter):信封体模型被 FastAPI 拒绝(422)时请求**未进 router**——无汇总行/上下文尾巴,`request validation failed: path= request_id= detail=`(request_id 尽力从原始 body 抢救,errors 只取 loc/msg 摘要)是该请求唯一日志证据;响应体保持 FastAPI 默认形状不变。
- **每次 acquire 一行结果**(INFO):`acquire done: scope= … outcome=deployed pod=… duration_ms=…`。
- **异常拍留痕**:handler 失败 WARNING+`exc_info`(异常链);`NO_POD_AVAILABLE` 粗化前记录真因(`mapped_from=MAX_PODS_REACHED|DEPLOY_FAILED`);框架 `FrameworkError`(validation/not_found/deadline)在 router 层补 WARNING。
- **Redis 延迟探针**:两个 state.py 的 `eval()` 计时,>200ms WARNING(`lua eval slow`);Lua 返回空表属真异常 → WARNING(`lua returned empty (anomaly)`)。
- **降噪**:框架 `tick lock acquired`/`single_leader claimed` 降 DEBUG;`tick done` 常态 DEBUG、异常/慢拍(>1s)/每 600 拍心跳保留 INFO。1Hz 三任务从 ~6 行/秒降到 INFO 下 ~0 行/秒。
- **DEBUG 解锁明细**:lua eval 明细、k8s get/list/delete 耗时、touch 命中明细、resolve 缓存命中。

### metrics.py —— 请求指标

`MetricsRegistry`(挂 `app.asgi.state.metrics`):per-endpoint `{total, ok, error, by_error_code, p50/p95/max_ms}`(延迟窗口 deque 1024,读取时算分位)+ `recent_errors` 环形缓冲(200 条,新在前:`{ts, endpoint, error_code, request_id, session_id, duration_ms, detail}`)。`request_metrics_middleware` 经 `App.use()` 进 router 派发链(最外层),读 `UnaryResult.response.ok/error_code` 即客户端最终结果。

### visualization_api.py —— 可视化端点(全 GET、只读;前缀 2026-08 由 /debug 更名)

挂裸 FastAPI(同 /healthz 模式,`register_visualization_api(app, registry=registry)` 在 create_app 调用)。响应统一 `{ok, instance_id, generated_at, …}`;sysctx 未就绪 503、缺参 400、未知对象 404、内部异常 503 JSON+服务端堆栈;limit 夹取 [1,500]。**访问控制:默认开放**(靠网络边界,Service ClusterIP);输出统一 `redact()` 脱敏(敏感 key→`***`、URL 剥 userinfo、嵌套 JSON 字符串深入)。

| 端点 | 内容 |
|---|---|
| `/visualization/overview` | instance/mode/uptime/pid/python、脱敏配置摘要(含 eval 四项:sample_interval/interval/llm enabled/pod_budget)、`sysctx.readiness()`、7 个 job 的 interval/tick_timeout/JobRunner 计数快照/当前 leader(`GET agent_runtime:job:{name}` 解析 token;tick 间隙锁瞬时缺失 → leader=null 属正常) |
| `/visualization/session?session_id=` | 会话 HASH、ttl_remaining_s、所属 scope 会话数/候选 Pod、绑定 Pod sse_url/deploy_ver |
| `/visualization/scope?scope_id=&limit=50` | RM:pod_count/idle/deploying/deploy_followers/scope_config(脱敏)/逐 Pod 详情(phase/ip/health_fails/idle_since);SM:session_count/候选 Pod/`capacity` 容量闸门子对象(scope_concurrency/pod_concurrency/session_ttl/pod_ttl/min_idle_pods/派生 max_pods/session_utilization/route_budget_sec=ready_timeout+10)与路由定义;顶层 phase(active/disabled/orphan_rm/missing_rm_cfg) |
| `/visualization/scopes?limit=100` | scope 清单 = RM 键 ∪ 路由快照(2026-09)+ 每 scope 一行摘要(pods/idle/deploying/session_count/max_pods/min_idle_pods + phase/template_id/scope_enabled/expires_at/scope_concurrency/pod_concurrency/session_ttl);total/truncated |
| `/visualization/config` | DB routing_scopes + templates(脱敏 kubeconfig)+ 路由快照 ver/scope_count/template_count + Redis 缓存键计数 |
| `/visualization/stats` | registry.snapshot()(计数/分位/错误码分布,命中实例视角)+ pid/uptime + `scopes` 段(2026-09:per-scope route/acquire/事件计数,**Redis 全副本聚合**视角) |
| `/visualization/recent_errors?limit=50` | 错误环形缓冲(新在前) |
| `/visualization/history?scope_id=&window_sec=3600&limit=240` | 单 scope 历史趋势采样(sys_sample 30s 一拍,25h TTL;新在前;数据在 Redis 重启不丢;limit 钳 [1,1440]) |
| `/visualization/evaluation?limit=10` | 系统评估报告 latest+history(sys_eval 周期产出;**全局视角**读 Redis;无报告 latest=null 属正常态;llm 段只含 status/model/latency 无凭证) |

- **LB 后是 per-instance**:命中哪个副本就是哪个副本的数据,响应 `instance_id` 标识应答者;看指定副本直连 Pod IP。
- **坑**(与 /healthz 同源):`Request`/`JSONResponse` 顶层 import;query 参数用 `request.query_params.get()` 读,**不在签名里声明**;`_visualization_endpoint` 包装器**不用 functools.wraps**(会把内层 `(request, sysctx)` 注解复制给 FastAPI 解析,sysctx 会被当成 query 参数)。

## config.py —— AgentRuntimeConfig(本服务自有配置)

框架级(host/port/redis/db)走 `ServiceConfig.from_env()`(`OPENJIUWEN_SERVICE_*`);本文件只放 `AGENT_RUNTIME_*`:

| 字段 | env | 默认 | 说明 |
|---|---|---|---|
| mode | `AGENT_RUNTIME_MODE` | server | server\|local |
| kubeconfig | `AGENT_RUNTIME_KUBECONFIG` | None(集群内 SA) | |
| default_namespace | `AGENT_RUNTIME_DEFAULT_NAMESPACE` | default | |
| sweep_interval | `AGENT_RUNTIME_SWEEP_INTERVAL` | 1 | SM:到期+空 Pod pass |
| autoscale_interval | `AGENT_RUNTIME_AUTOSCALE_INTERVAL` | 1 | RM:min_idle 补位(**全局默认,无 per-scope 覆盖**) |
| reclaim_interval | `AGENT_RUNTIME_RECLAIM_INTERVAL` | 1 | RM:idle 回收 |
| watch_interval | `AGENT_RUNTIME_WATCH_INTERVAL` | 10 | RM:死 Pod+健康探测 |
| reconcile_interval | `AGENT_RUNTIME_RECONCILE_INTERVAL` | 30 | RM:对账 |
| default_session_ttl | `AGENT_RUNTIME_DEFAULT_SESSION_TTL` | 60 | touch 兜底 ttl |
| eval_sample_interval | `AGENT_RUNTIME_EVAL_SAMPLE_INTERVAL` | 30 | sys_sample 采样间隔(钳 5;spec/evaluation.md) |
| eval_interval | `AGENT_RUNTIME_EVAL_INTERVAL` | 300 | sys_eval 评估间隔(钳 30) |
| eval_llm_base_url | `AGENT_RUNTIME_EVAL_LLM_BASE_URL` | 空 | OpenAI 兼容端点;**与 model 均非空才启用** |
| eval_llm_api_key | `AGENT_RUNTIME_EVAL_LLM_API_KEY` | 空 | 可空(内网免鉴权);绝不进日志/报告 |
| eval_llm_model | `AGENT_RUNTIME_EVAL_LLM_MODEL` | 空 | 模型名 |
| eval_llm_timeout | `AGENT_RUNTIME_EVAL_LLM_TIMEOUT` | 60.0 | < TICK_TIMEOUTS.sys_eval=120 |
| eval_pod_budget | `AGENT_RUNTIME_EVAL_POD_BUDGET` | 0 | 集群 Pod 预算;0=预算规则关闭 |

常量:`SM_KEY_PREFIX="session_manager"`、`RM_KEY_PREFIX="resource_manager"`、`SERVICE_PREFIX="/api/session"`。

## errors.py —— 错误码契约(语义权威 HLD §3.1)

`ErrorCode` 常量 + 异常类(均继承 `AgentRuntimeError(FrameworkError)`,类属性 `code`)+ `HTTP_STATUS_MAP` + `register_codes()`(幂等,main.py import 时调用)。

| 码 | HTTP | retry_after | 场景 |
|---|---|---|---|
| `SCOPE_FULL` | 503 | ✅ | scope 满/达总容量,立即快失败(2026-09 起) |
| `NO_POD_AVAILABLE` | 503 | ✅ | acquire 失败(MaxPodsReached/DeployFailed 在 SM 侧映射而来) |
| `CONFIG_NOT_FOUND` | 503 | ❌ | resolve 无匹配规则/模板禁用 |
| `VALIDATION` | 400 | ❌ | 参数错 |
| `CONFIG_SYNC_BUSY` | 409 | — | 上一次热更新未完成 / 日落待回收中间态 Pod / `lock:config_sync` 被 config_sync 或 config_refresh 占用 |
| `STATE_UNAVAILABLE` | 503 | ✅ | 状态后端(Redis/DB)连接级故障,handler 层翻译(`handlers._INFRA_EXCEPTIONS`);区别于 internal 500——暂态可重试 |

- `retry_after` 仅过载类携带(秒);Facade 间以 Python 异常传播,handler 捕获后映射为 `ResponseEnvelope(ok=False, error_code, retry_after)`。
- `MAX_PODS_REACHED` / `DEPLOY_FAILED` 是 RM Facade 内部异常,SM route 捕获后统一映射 `NO_POD_AVAILABLE`,不对外。

## spec_fields.py —— template 字段分类(SM/RM 静态共享)

- `DEPLOY_FIELDS`:A 类(deploy 子集,值烘焙进运行中 Pod,变更需日落)。
- `DEPLOY_VER_FIELDS = DEPLOY_FIELDS + (ready_timeout, ready_poll_interval)`:deploy 指纹字段集。
- `POLICY_FIELDS`:B 类策略(`scope_concurrency/pod_concurrency/session_ttl/pod_ttl/min_idle_pods`)。
- **kubeconfig 例外**:在 deploy 子集但**不入指纹**(只影响新 deploy,不日落)。
- 约束:SM `Template.deploy_ver()` 与 RM `orchestrator._deploy_ver()` 必须用同一字段集与算法(`util.fingerprint`)——A 类版本过滤依赖两端一致。
- **新增 template 字段时**:先在此分类 → 再补 `config_store.py` 的 `_COLUMN_OF` 列映射与 `*_TABLE_DEF` 表结构。

## util.py —— 纯函数

- `scope_id_of(group_id, bot_id)` = `md5(group_id + "\x00" + bot_id)`;`\x00` 防 `(ab,c)`/`(a,bc)` 撞号。
- `fingerprint(fields)`:按 key 排序、剔 None、md5 取前 16 hex(deploy_ver 用)。
- `s()`/`to_int()`:Redis 返回值 bytes/str 归一(真实 client 是 bytes,fakeredis 可能是 str)。
- `now_ts()`:秒级 int(Redis 键内时间统一秒级)。

## 部署

- **双进程宿主机**:`scripts/deploy_replicas.sh N [env] [port]`(N 进程共 Redis/DB,`/healthz` 就绪轮询,trap 清理;local 模式 fail-fast)。
- **K8s 生产形态**:`deploy/` 目录——`agent_runtime.template.yaml`(SA+Role×2+Deployment 多副本/反亲和//healthz 探针+ClusterIP Service LB)、可选 NodePort(30091)、`Dockerfile`(**build context=仓库根**,保 `../../foundation`/`../../service` 布局,`uv sync --frozen --extra server --no-dev`,logs/ 预建归 appuser)、`render_and_apply.sh`(env 渲染→apply,残留 `<<` 即 fail-fast)、`build_image.sh`。
- **namespace 布局**:服务与 AgentServer 同 ns `agent-runtime-e2e`(`deploy/agent_runtime.env` 的 `NAMESPACE`;e2e_multi_replica 的 `--namespace` 即此 ns)。模板 RBAC 仍按「服务 ns + AgentServer 目标 ns」两份渲染——同 ns 时为重复授权,无害。
- K8s 部署红线:
  - `OPENJIUWEN_SERVICE_DEPLOY_REPLICAS=1` 固定(副本数=Deployment replicas;框架该项 >1 会因缺分布式锁后端启动即失败)。
  - RBAC 两份:服务 ns + AgentServer 目标 ns(缺则 create pod 403 → route 全 503;同 ns 部署时两份指向同一 ns)。
  - 本地 tag 镜像(无仓库)必须 `docker save | ssh <node> docker load` 分发到**每个可调度节点**——`imagePullPolicy=IfNotPresent` 拉不到本地 tag,缺镜像节点上 pod 直接 ErrImagePull;且每次构建**换新 tag**并同步 `AGENT_RUNTIME_IMAGE`(同 tag 节点不会重拉)。
  - 迁移 ns 时先删旧 ns 的 Deployment/Service(含 NodePort 30091 占用)再 apply 新 ns,避免双部署竞争选主与 NodePort 冲突。
  - Pod 内 MySQL 用户须授权 Pod CIDR(`'agent_runtime'@'10.244.%'`)。
  - server 模式硬要求:Redis 开 AOF/RDB;DB 用 MySQL/PostgreSQL。
