# service-core 规格(组装 / 配置 / 错误码 / 字段分类 / 部署)

> 覆盖 `src/agent_runtime/` 顶层 6 个文件:`main.py`、`cli.py`、`config.py`、`errors.py`、`spec_fields.py`、`util.py`。
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

- 构造时同时建 **rm_sysctx**(同 redis/db,仅 `key_prefix` 不同:SM=`session_manager`,RM=`resource_manager`)。
- `_bind_modules()`:先构造后绑定,破解 SM↔RM 循环引用——
  `SessionState`/`ResourceState` → `SessionManagerFacade`/`ResourceManagerFacade(ResourceOrchestrator)` → `ConfigStore(push_pool_config=rm_facade.update_pool_config)` → `SessionOrchestrator` → `SessionSweeper`/`ResourceSweeper`。
- `_build_jobs()`:5 个后台任务,全部 `create_single_leader_job`(tick 级 Redis 选主锁,多副本全局单副本执行;`tick_timeout_sec` 取 `TICK_TIMEOUTS` 常量表——单次 tick 上限,防 redis/k8s IO 抖动挂死 `_run_forever` 循环,超时取消本拍记日志、下一拍重试):

| 任务 | tick | tick 超时 | 锁键(`agent_runtime:job:*`) | 动作 |
|---|---|---|---|---|
| sm_sweep | `sweep_interval`(1s) | 30s | `sm_sweep` | 到期 pass + 空 Pod pass |
| rm_autoscale | `autoscale_interval`(1s) | 370s(盖住 deploy ready_timeout 300 + DEPLOY_LOCK_TTL 360) | `rm_autoscale` | min_idle 热备补位 |
| rm_reclaim | `reclaim_interval`(1s) | 60s | `rm_reclaim` | idle 超 pod_ttl 回收 |
| rm_watch | `watch_interval`(10s) | 300s | `rm_watch` | 死 Pod 判定 + 健康探测 |
| rm_reconcile | `reconcile_interval`(30s) | 300s | `rm_reconcile` | 孤儿/stale 对账 |

- `start()` 顺序:super().start() → rm_sysctx.start() → k8s.start()(**失败仅降级扩缩容,不阻断启动**)→ 启动 5 个 job。`stop()` 逆序。
- DB 表初始化:构造参数 `table_definitions=[SERVICE_CONFIG_TEMPLATE_TABLE_DEF, ROUTING_RULE_TABLE_DEF]`(表结构在 `config_store.py`)。

### create_app(settings, arc, *, resources=None, instance_id=None, own_resources=True)

构造唯一 App(`prefix=/api/session`,`enable_ws=False`)+ `/healthz` + 4 个 handler。
`resources`/`instance_id`/`own_resources` 仅供多实例测试注入共享物理资源(`tests/integration/_dual_harness.py`);生产路径不传。

### /healthz(main.py:_register_healthz)

进程就绪探针(K8s probe / deploy_replicas.sh 就绪轮询 / e2e 实例观测)。sysctx 未就绪 → 503;就绪返回 `{ok, instance_id}`。
**坑**:模块顶部 `from __future__ import annotations` 下,FastAPI 经 `get_type_hints` 用模块全局解析注解——`Request` 必须顶层 import,函数内局部导入会被当成 query 参数(422)。

## 网络/IO 抖动超时兜底(框架层,server 模式生效)

本模块 redis/db 客户端均经框架构建,socket 级超时在构建点注入(网络黑洞/TCP 半开时 await 不再永久挂起):

- **Redis**(`service/bootstrap.build_redis_client`,env 可调,0=关闭该项):
  `OPENJIUWEN_SERVICE_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS`(默认 3,建连)、
  `OPENJIUWEN_SERVICE_REDIS_SOCKET_TIMEOUT_SECONDS`(默认 5,命令读写;本项目无 BLPOP 等长阻塞命令,短超时安全)、
  `OPENJIUWEN_SERVICE_REDIS_HEALTH_CHECK_INTERVAL_SECONDS`(默认 30,空闲连接周期 PING 验活)、
  `OPENJIUWEN_SERVICE_REDIS_RETRY_ATTEMPTS`(默认 3,连接类错误指数退避重试)。
  本地 local 模式 fakeredis 不经此路径,不受影响。
- **MySQL**(`foundation/db`:`RUNTIME_DB_CONNECT_TIMEOUT`/`DB_CONNECT_TIMEOUT`,默认 5s):aiomysql 建连超时(mysql 系 driver 自动注入,调用方显式 connect_args 优先);asyncpg 自带 60s 默认不注入。查询读超时 aiomysql 无参数,由请求级 deadline 兜底。
- **请求级总兜底**:`OPENJIUWEN_SERVICE_REQUEST_TIMEOUT_SECONDS`(部署模板 70s)经框架 router 的 `asyncio.timeout` 硬包全部 handler——redis/db 挂起最坏 70s 后取消该请求。
- **启动 fail-fast**:框架 `SystemContext.start()` 的 readiness 探活(ping/SELECT 1)带 10s 上限,超时按失败处理(fail-fast 不变成 fail-hang)。

## cli.py —— 命令行入口

`deploy.sh` 调用。参数:`--mode local|server`(必填)、`--env-file`(dotenv 先加载,`override=False`)、`--host`/`--port`(覆盖 `OPENJIUWEN_SERVICE_*`)。流程:load_dotenv → 设 `AGENT_RUNTIME_MODE` → basicConfig(`AGENT_RUNTIME_LOG_LEVEL`)→ `ServiceConfig.from_env()` + `AgentRuntimeConfig.from_env()` → `create_app` → uvicorn.run。

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
| scope_full_timeout | `AGENT_RUNTIME_SCOPE_FULL_TIMEOUT` | 30.0 | 等待队列阻塞上限(**部署须显著小于 session_ttl**,否则等待者 deadline 与会话到期碰撞) |
| default_session_ttl | `AGENT_RUNTIME_DEFAULT_SESSION_TTL` | 60 | touch 兜底 ttl |

常量:`SM_KEY_PREFIX="session_manager"`、`RM_KEY_PREFIX="resource_manager"`、`SERVICE_PREFIX="/api/session"`。

## errors.py —— 错误码契约(语义权威 HLD §3.1)

`ErrorCode` 常量 + 异常类(均继承 `AgentRuntimeError(FrameworkError)`,类属性 `code`)+ `HTTP_STATUS_MAP` + `register_codes()`(幂等,main.py import 时调用)。

| 码 | HTTP | retry_after | 场景 |
|---|---|---|---|
| `SCOPE_QUEUE_FULL` | 503 | ✅ | 等待队列满,快失败 |
| `SCOPE_FULL_TIMEOUT` | 504 | ✅ | 队列内等待超时 |
| `NO_POD_AVAILABLE` | 503 | ✅ | acquire 失败(MaxPodsReached/DeployFailed 在 SM 侧映射而来) |
| `CONFIG_NOT_FOUND` | 503 | ❌ | resolve 无匹配规则/模板禁用 |
| `VALIDATION` | 400 | ❌ | 参数错 |
| `CONFIG_SYNC_BUSY` | 409 | — | 上一次热更新未完成 / 日落待回收中间态 Pod |

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
- K8s 部署红线:
  - `OPENJIUWEN_SERVICE_DEPLOY_REPLICAS=1` 固定(副本数=Deployment replicas;框架该项 >1 会因缺分布式锁后端启动即失败)。
  - RBAC 两份:服务 ns + AgentServer 目标 ns(缺则 create pod 403 → route 全 503)。
  - Pod 内 MySQL 用户须授权 Pod CIDR(`'agent_runtime'@'10.244.%'`)。
  - server 模式硬要求:Redis 开 AOF/RDB;DB 用 MySQL/PostgreSQL。
