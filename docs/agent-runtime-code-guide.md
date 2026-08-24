# agent-runtime 代码说明文档

- 日期:2026-08-15(M0–M6 完成后整理)
- 读者:维护/二次开发本服务的工程师
- 配套:语义权威 = `design/Agent-Runtime-HLD.md`;模块细节 = SM/RM 两份详细设计。本文回答"**代码在哪、怎么跑起来、改哪里**",不重复论证设计决策。

---

## 1. 一页纸架构

一个进程、一个 App(`/api/session`,端口 8091)、两个模块:

```
gateway ──route/touch──► agent-runtime(uvicorn)
claw mgr ──config_sync──►   ├─ session_manager  持 App,4 个 HTTP handler
运维    ──cleanup──────►    └─ resource_manager  无 App,纯 Facade + 后台任务
                            两模块共享同一 Redis(前缀隔离)+ 同一 DB,互调只走进程内 Facade
数据面(本服务全程旁路):gateway ◄──SSE──► AgentServer Pod(route 返回 pod_sse_url)
```

- 状态分层:**编排态在 Redis**(SM `session_manager:` / RM `resource_manager:` 前缀)、**配置在 DB**(`service_config_template` / `routing_rule` 表)、**Pod 物理态以 K8s 为唯一真相源**。
- 多副本无状态;后台任务经 Redis 选主锁全局单副本执行写操作。

## 2. 代码地图(`applications/agent_runtime/`)

```
src/agent_runtime/
├── main.py              ★ 组装入口:create_app → OrchestratorSystemContext
│                          (级联拉起 rm_sysctx + Facade 互绑 + 5 个后台任务)
├── cli.py               命令行入口(deploy.sh 调用;--mode local|server)
├── config.py            AGENT_RUNTIME_* 环境变量(任务周期/超时/namespace)
├── errors.py            错误码契约(异常类 + HTTP 状态注册;retry_after 语义)
├── util.py              scope_id 派生(md5+\x00)/ deploy 指纹 / to_int 等纯函数
├── spec_fields.py       template 字段分类:A 类 deploy 子集 / B 类策略(静态,两端共享)
├── session_manager/     SM 模块
│   ├── handlers.py      4 个 HTTP handler(route/touch/config_sync/cleanup)
│   ├── orchestrator.py  route 编排(resolve→Lua 仲裁→acquire→等待队列) + touch
│   ├── state.py         SM Redis 键 schema 唯一出口(含 Lua 调用封装)
│   ├── lua_scripts.py   7 个 Lua 全文(见 §4)
│   ├── sweeper.py       到期 pass(老化)+ 空 Pod pass(idle_consider)
│   ├── config_store.py  template/routing_rule 持久化 + resolve 缓存 + config_sync
│   ├── facade.py        SessionManagerFacade(notify_pod_dead / reconcile_pods)
│   └── models.py        Template/ScopeConfig dataclass
└── resource_manager/    RM 模块
    ├── orchestrator.py  acquire(取暖/选主 deploy/封顶)+ idle_consider
    │                    + update_pool_config + cleanup + 结果幂等缓存
    ├── state.py         RM Redis 键 schema 唯一出口
    ├── lua_scripts.py   6 个 Lua 全文(见 §4)
    ├── k8s.py           RealK8sPodClient(kubernetes_asyncio)/ FakeK8sPodClient
    │                    (deploy 等 Ready、409 重命名重试、判死归一化、/health 探测)
    ├── sweeper.py       autoscale / reclaim / watch(死 Pod+健康探测)/ reconcile
    ├── facade.py        ResourceManagerFacade(acquire / idle_consider / update_pool_config / cleanup)
    └── models.py        PodInfo / PodDeployInfo / 判死枚举 / Pod label 常量

scripts/deploy.sh                local|server 启动
scripts/e2e_hld_acceptance.py    集成冒烟(场景 A–L,真环境)
scripts/integration_smoke.sh     冒烟入口包装
tests/                           114 个单测(见 §7)
```

## 3. 关键流程(读代码的切入点)

### 3.1 route(`session_manager/orchestrator.py:route`)

```
幂等回放(handler 层 ctx.idempotency,request_id 键)
→ resolve(scope)(config_store:Redis 缓存 → DB,规则优先级 精确>(g,*)>(*,b)>(*,*))
→ 循环 { LUA_ROUTE_PLACE 原子仲裁:
     refresh/placed → 读 pod:info sse_url 返回
     scope_full     → LUA_WAITER_GATE 原子入队(满→503 快失败;否则订阅 free
                      等 scope_full_timeout→504;唤醒后重跑仲裁)
     need_acquire   → rm_facade.acquire(扩 +1 Pod)→ register_pod → 重跑 }
```

### 3.2 RM acquire(`resource_manager/orchestrator.py:acquire`)

```
幂等缓存(request_id)→ LUA_ACQUIRE:
  reuse(暖 Pod,deploy_ver 过滤)→ 返回
  max_reached → 抛 MaxPodsReached(SM 映射 503 NO_POD_AVAILABLE)
  need_deploy → 抢 lock:rm:deploy:{scope} 选主串行 deploy
               (输家 → follower 等待室:准入≤pc-1/overflow 快失败/等 leader
                Pod 注册即复用/leader 失败不接管/等待有界)
k8s.deploy:create + wait Ready(409 重命名重试;超时/镜像失败 → DeployFailed)
错误路径必须清 deploying 占位(红线)
```

### 3.3 老化与回收链(场景 D→K)

```
SessionSweeper(1s,lock:sweep):
  到期 pass   ZRANGEBYSCORE session_expiry → 逐个 LUA_EVICT(四处同删 + PUBLISH free)
  空 Pod pass pods:registered 枚举 → LUA_SWEEP_IDLE_NOTIFY(原子:空判定+去重+ZREM 候选)
              → fire-and-forget rm_facade.idle_consider → RM 转 idle 暖池
ResourceSweeper:
  autoscale(1s) idle < min_idle 且未达 max_pods → LUA_PLACEHOLDER 占位 → deploy 热备
  reclaim(1s)  idle 超 min_idle 底数的 excess 中 aged ≥ pod_ttl → K8s delete + PURGE
               + notify_pod_dead(清 SM 注册;保底热备不被回收)
  watch(10s)   判死枚举(Terminating/Failed/CrashLoopBackOff/ImagePullBackOff/...,
               Pending 不判死)→ 清理;Running 但 /health 连续 2 次失败 → 半死清理(场景 N)
  reconcile(30s) Redis 有 K8s 无 → PURGE;SM 不再引用的 stale Pod → 转 idle
```

### 3.4 配置热更新(场景 M,`config_store.py:config_sync`)

```
lock:config_sync 串行化(忙 → 409 CONFIG_SYNC_BUSY)
→ 写 DB(失败即中止,不碰缓存——红线)
→ diff 判类(spec_fields.A/B):
   A 类(deploy 子集变,deploy_ver 变)→ SM 软摘除(ZREM 老版本 Pod 出候选,存量会话不受影响)
                                      + 推 RM(新 deploy_ver/pod_spec,新流量落新 Pod,自然滚动)
   B 类(策略字段变)                  → DEL scope:config + 推池参数,立即生效
→ 完成判定:受影响 scope 仍有日落待回收的中间态 Pod → 409 拒绝下一次
```

## 4. Lua 脚本清单(所有编排态变更,原子;Redis 单线程执行无 race)

| 模块 | 脚本 | 一句话职责 |
|---|---|---|
| SM | `LUA_ROUTE_PLACE` | route 原子核心:亲和续期/惰性回收/闸门/first-fit/提交 |
| SM | `LUA_EVICT` | session 移除唯一原语(四处同删 + PUBLISH free 唤醒等待者) |
| SM | `LUA_TOUCH` | 保活续期(惰性 evict 兜底;ttl 就地读 session HASH) |
| SM | `LUA_SWEEP_IDLE_NOTIFY` | 空 Pod 判定 + 60s 去重 + ZREM 退出候选(堵竞态 A) |
| SM | `LUA_REGISTER_POD` | acquire 成功登记(三处注册 + 接入序) |
| SM | `LUA_CLEANUP_POD` | notify_pod_dead 清该 (scope,pod) 全部注册 |
| SM | `LUA_WAITER_GATE` | 等待队列原子入队(SADD 先行 + 超限自退;M6 修复的并发超收) |
| RM | `LUA_ACQUIRE` | 取暖复用 / need_deploy 占位 / max_reached |
| RM | `LUA_REGISTER` | deploy 成功登记(info/池/pods:all,清占位;idle_flag 入暖池) |
| RM | `LUA_RELEASE` | idle_consider 转 idle 暖池(起 pod_ttl 计时) |
| RM | `LUA_PURGE` | 清该 Pod 全部 RM key(返回其 scope) |
| RM | `LUA_PLACEHOLDER` | autoscale 专用占位(计入 max_pods,不碰 idle 池) |
| RM | `LUA_DEPLOY_FOLLOWER_GATE` | deploy 锁输家等待室原子准入(ZSET+deadline,≤pc-1;先清过期再 ZADD 先行+超限自退) |

约定:脚本不传 KEYS(键由 `ARGV[1]` 前缀在脚本内拼);调用统一经各自 `state.py` 的 `eval()`。

## 5. Redis 键速查(全文见 HLD §5 两张表)

```
session_manager:session:{sid}                     HASH  亲和绑定(scope/pod/expiry/ttl)
session_manager:session_expiry                    ZSET  到期时间(sweeper 扫)
session_manager:scope:{sid}:sessions              SET   SCARD = scope 闸门
session_manager:scope:{sid}:pods                  ZSET  first-fit 候选(接入序)
session_manager:scope:{sid}:config                HASH  resolve 缓存(config_sync DEL 失效)
session_manager:scope:{sid}:waiters               SET   等待队列(LUA_WAITER_GATE 原子进出)
session_manager:scope:{sid}:free                  PubSub 额度释放信号
session_manager:pod:{scope}:{pod}:sessions|info   SET|HASH per-Pod 会话 / sse_url+deploy_ver
session_manager:pods:registered                   SET   "{scope}:{pod}"(不变量 5)
resource_manager:resource:scope:{sid}:pods|idle|config|deploying|deploy_followers
                                                     ZSET|SET|HASH|SET|ZSET(follower 等待室,≤pc-1)
resource_manager:resource:pod:{pod}:info|idle_since|health_fails HASH|STR|STR
resource_manager:resource:pods:all                SET   全部 pod_id(watch/reconcile 枚举)
resource_manager:lock:rm:deploy:{sid}|autoscale|reclaim|watch|reconcile  选主/串行化锁
```

## 6. 错误码(`errors.py`,契约见 HLD §3.1)

| 码 | HTTP | retry_after | 场景 |
|---|---|---|---|
| `SCOPE_QUEUE_FULL` | 503 | ✅ | 等待队列满,快失败 |
| `SCOPE_FULL_TIMEOUT` | 504 | ✅ | 队列内等待超时 |
| `NO_POD_AVAILABLE` | 503 | ✅ | acquire 失败(MaxPodsReached/DeployFailed 映射) |
| `CONFIG_NOT_FOUND` | 503 | ❌ | resolve 无匹配规则/模板禁用 |
| `VALIDATION` | 400 | ❌ | 参数错 |
| `CONFIG_SYNC_BUSY` | 409 | — | 上一次热更新未完成 / 日落待回收 |

Facade 间以 Python 异常传播,handler 捕获后映射为错误信封。

## 7. 测试与验收

> 全部 e2e 用例的场景/输入/预期输出逐条说明见 **`docs/e2e-test-cases.md`**。

```bash
cd applications/agent_runtime
uv sync --extra local && uv run pytest     # 114 用例,fakeredis+SQLite+FakeK8s
./scripts/integration_smoke.sh             # 真环境冒烟(场景 A–L;FLUSHDB 目标库,有防误刷)
```

| 层 | 文件 | 内容 |
|---|---|---|
| SM 状态层 | `tests/session_manager/test_sm_state.py` | Lua 原子语义(亲和/first-fit/闸门/老化/幂等) |
| RM 状态层 | `tests/resource_manager/test_rm_state.py` | acquire 占位/封顶/暖池/PURGE |
| SM config 层 | `tests/session_manager/test_config_store.py` | resolve 优先级/缓存/AB 类 diff/串行化/DB 失败红线 |
| RM 业务 | `tests/resource_manager/test_rm_business.py` | watch/健康探测/reconcile/cleanup/update_pool_config |
| 组件全链路 | `tests/integration/test_route_flow.py` | 场景 A–K 全链路(Fakeredis 共享态) |
| 分支/corner | `tests/integration/test_corner_cases.py` | 边界与异常分支(18 项) |
| HTTP 冒烟 | `tests/integration/test_http_smoke.py` | 4 端点契约 + 错误码映射 + /healthz |
| **双实例多副本** | `tests/integration/test_multi_replica.py` | 跨副本确定性语义(12 项,`_dual_harness.py` 同进程两 App 共享一组资源) |
| 集成冒烟 | `scripts/e2e_hld_acceptance.py` | 真 Redis/MySQL/K8s,65 项断言;场景 N 暂缓(AgentServer /health 未支持) |
| 多副本 e2e | `scripts/e2e_multi_replica.py` | 真 LB 单入口 35 项(选主互斥/突发/幂等/传播/failover);<2 实例自动 DEGRADED |
| 压测/浸泡 | `scripts/load_test.py` | 场景化(route/route_touch/queued),分位数+错误直方图,周期浸泡报告 |

- 双实例 harness 要点:`create_app(resources=..., instance_id=..., own_resources=False)` 注入共享资源;httpx `ASGITransport` 单事件循环驱动(**先手动驱动 lifespan**,否则 RestAdapter 惰性二建 sysctx 绕过后台 Job);两个 TestClient(双事件循环共享 fakeredis)不可行。
- 经多副本 LB 亦可跑(实测 65/65):部署须带 `AGENT_RUNTIME_SCOPE_FULL_TIMEOUT`(模板默认 8,显著小于 session_ttl——漏设曾致等待者 deadline 与会话到期碰撞,F 阶段混合结果);排查实录与 cleanup 空目标三坑见 `docs/e2e-test-cases.md` §8.1。

## 8. 配置与部署

- 启动:`scripts/deploy.sh local|server [env-file]`;server 读 `.env.production.local`(模板 `agent_runtime.server.env.example`)。
- 框架配置(`OPENJIUWEN_SERVICE_*`):host/port/Redis URL/DB。服务自有配置(`AGENT_RUNTIME_*`):任务周期、scope_full_timeout、kubeconfig、default_namespace——见 `config.py`。
- server 模式硬要求:Redis 开 AOF/RDB;DB 用 MySQL/PostgreSQL(fail-fast,禁 SQLite 回退)。
- 双模式实现:`main.py:build_resources`(local=fakeredis+SQLite+FakeK8s;server=真客户端);K8s 层 Real/Fake 同签名(`k8s.py:K8sPodClient`)。
- **多副本宿主机**:`scripts/deploy_replicas.sh N [env] [port]`(N 进程共 Redis/DB,`/healthz` 就绪轮询,trap 清理;local 模式 fail-fast)。
- **K8s 生产形态**:`deploy/` 目录——`agent_runtime.template.yaml`(SA+Role×2+Deployment 多副本/反亲和//healthz 探针+ClusterIP Service LB)、可选 NodePort(30091)、`Dockerfile`(**build context=仓库根**,保 `../../foundation`/`../../service` 布局,`uv sync --frozen --extra server --no-dev`,logs/ 须预建归 appuser)、`render_and_apply.sh`(env 渲染→apply,残留 `<<` 即 fail-fast)、`build_image.sh`。
- K8s 部署红线:`OPENJIUWEN_SERVICE_DEPLOY_REPLICAS=1` 固定(副本数=Deployment replicas);RBAC 两份(服务 ns + AgentServer 目标 ns,否则 create pod 403→route 全 503);Pod 内 MySQL 用户须授权 Pod CIDR(`'agent_runtime'@'10.244.%'`)。

## 9. 改动时的注意事项(高频踩点)

- **改键名/Lua**:HLD §5 键表与两个 `state.py` 必须同步;Lua 全文在 SM/RM 详细设计与 `lua_scripts.py` 双份,同步改。
- **新增 template 字段**:先分类(`spec_fields.py`:A 类进 deploy 子集与指纹,或 B 类策略),再补 `config_store.py` 的 `_COLUMN_OF` 列映射(DB 列名沿用 EE 兼容名)与表结构 `*_TABLE_DEF`。
- **等待队列**:入队只准走 `LUA_WAITER_GATE`;「先查后加」是已被真环境验收证伪的写法。
- **跨模块**:SM↔RM 数据只走 Facade 方法,不直读对方 Redis key(架构红线)。
- **测试环境**:构造 `ServiceManager` 传 `deploy_mode="subprocess"`;fakeredis pubsub 需共享同一实例。
- **真环境验收**:独立 namespace + 独立 Redis DB(冒烟脚本自带防误刷与前置自检)。
- **多副本踩点**:选主元数据键 TTL ~3s,观测/采样须 ≤0.5s 间隔(SCAN);`instance_id` 前缀=hostname(容器内即 Pod 名),可反查副本。跨副本冷竞争时 deploy 锁输家会自建第 2 个 Pod(`max_pods` 内,空 Pod 经 empty-pod pass→idle_consider→reclaim 自愈)——测试断言「窗口零重叠+Pod≤max_pods」,不断言「恰好 1 个 Pod」;多后端冷突发 NO_POD_AVAILABLE 快失败属预期。`config_sync` 全局串行锁:脚本播种必须串行,并发即 409。框架 `load_incluster_config` 是**同步**函数(`k8s.py` 已修,M7 首次在 in-cluster 暴露)。部署须设 `AGENT_RUNTIME_SCOPE_FULL_TIMEOUT` 且显著小于 session_ttl(等待者 deadline≈会话到期会被到期驱逐唤醒,产生 200/503 混合而非干净 504)。
- **cleanup 踩点**:目标 ns 不存在时,宿主机 admin 凭据得空列表、in-cluster SA 的 namespaced RBAC 得 403——产品已对 404 容忍为 cleaned=0、403 保持 fail-fast;测试的「空目标」必须用无匹配 label_selector(业务 ns 会误删同 label 真实 AgentServer;min_idle 模板的热备 1s 内重建,刚清空的 ns 也不为空)。
