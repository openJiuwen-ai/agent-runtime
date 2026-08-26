# resource_manager(RM)规格

> Pod 池管理:acquire(扩容决策)、后台任务(autoscale/reclaim/watch/reconcile)、K8s 适配。
> **无 App/端口/prefix**,纯进程内 Facade + 后台任务。**不读 SM 的容量键**(per-Pod 容量闸门在 SM 侧)。
> Lua 全文:`lua_scripts.py` 与 `../design/resource-manager-design.md` 双份,改时同步。

## 文件一览

| 文件 | 职责 |
|---|---|
| `facade.py` | `ResourceManagerFacade`(SM→RM 进程内入口,薄封装) |
| `orchestrator.py` | acquire(取暖/选主 deploy/follower 等待室)+ idle_consider + update_pool_config + cleanup + 幂等缓存 |
| `state.py` | RM Redis 键 schema 唯一出口(`RMKeys`/`ResourceState`) |
| `lua_scripts.py` | 6 个 Lua 全文 |
| `k8s.py` | `K8sPodClient` 接口 + `RealK8sPodClient`(kubernetes_asyncio)+ `FakeK8sPodClient` |
| `sweeper.py` | 四个后台任务(各自带选主锁) |
| `models.py` | `PodInfo`/`PodDeployInfo`/判死枚举/label 常量 |

## facade.py —— SM→RM 契约

| 方法 | 返回/异常 |
|---|---|
| `acquire(scope_id, pod_spec, pool_config, request_id)` | `{pod_id, pod_sse_url}`;失败抛 `MaxPodsReached`/`DeployFailed`(SM 映射 503 NO_POD_AVAILABLE) |
| `idle_consider(pod_id, scope_id)` | `{transitioned_to_idle}`,幂等 |
| `update_pool_config(scope_id, pool_config, pod_spec?)` | `{updated}`(config_sync 触发) |
| `cleanup(namespace?, label_selector?)` | `cleaned: int`(运维批删) |

## orchestrator.py —— acquire 决策树

```
幂等缓存(request_id,键 resource_manager:idem:{rid},TTL 60,命中续期)
→ 首见 scope:缓存池参数 + pod_spec_json 到 resource:scope:{sid}:config
→ 循环 { LUA_ACQUIRE → (action, pod_id, sse_url):
     reuse        → 直接返回(deploy_ver 过滤后的暖 Pod,已弹出 idle 池)
     max_reached  → 清占位 → MaxPodsReached
     no_config    → continue(上面已写配置)
     need_deploy  → 抢 lock:rm:deploy:{scope}(TTL 360,盖住 ready_timeout 300+余量):
                    赢家 → _deploy_and_register(idle_flag=False)
                    输家 → 清占位 → _follow_leader(follower 等待室) }
```

`_deploy_and_register`:`k8s.deploy(pod_spec)`(create+wait Ready)→ 拼 `pod_sse_url = http://{pod_ip}:{sse_port}{sse_path}` → `LUA_REGISTER`。**失败必须清 deploying 占位再抛 DeployFailed(红线:防 max_pods 永久虚高)**。

`_follow_leader`(M8,deploy 锁输家的等待室):
- 准入走 `LUA_DEPLOY_FOLLOWER_GATE` 原子闸门,上限 `pod_concurrency - 1`(leader 会话之外新 Pod 恰剩这些槽);overflow 严格快失败 MaxPodsReached。
- 等待有界:`ready_timeout + 10s` 余量;轮询 `resource:scope:{sid}:pods` 出现新 Pod 且 pod:info 有 sse_url → **直接复用返回**(与 reuse 分支同构,SM 侧重跑仲裁即可)。
- leader 失败判定:deploy 锁空闲且无新 Pod → `DeployFailed`(**follower 不接管**——同镜像同环境大概率也失败);deadline 到 → MaxPodsReached。
- 错误路径双清:占位 + follower 成员都进 finally;崩溃遗留由闸门 `ZREMRANGEBYSCORE(deadline)` 兜底。

`idle_consider`:`LUA_RELEASE` 转 idle 暖池(起 pod_ttl 计时)+ pod:info.phase=idle;幂等。
`update_pool_config`:HSET 覆盖池参数;A 类变更附带 pod_spec 时同时刷 deploy_ver/pod_spec_json(autoscale 补位用新 deploy 字段)。
`cleanup`:K8s list+delete,**不操作 Redis 编排态**(被删 Pod 由 watch/reconcile 兜底发现);ns 404 容忍为 cleaned=0,**403 保持 fail-fast**(静默清零会掩盖部署配错)。

## state.py —— RM 键表(`RMKeys`,前缀 `resource_manager:`,业务键再带 `resource:` 段)

| 键 | 类型 | 语义 |
|---|---|---|
| `resource:scope:{sid}:pods` | ZSET | 该 scope 全部 Pod(in_use ∪ idle);**ZCARD+deploying SCARD 参与 max_pods 判定** |
| `resource:scope:{sid}:idle` | SET | idle 暖池;acquire 从此取暖 Pod |
| `resource:scope:{sid}:config` | HASH | min_idle_pods/max_pods/pod_ttl/pod_concurrency/deploy_ver/pod_spec_json |
| `resource:scope:{sid}:deploying` | SET | deploy 占位 token(计入 max_pods,防并发超配) |
| `resource:scope:{sid}:deploy_followers` | ZSET | follower 等待室(request_id→deadline 秒级 score;闸门按 deadline 原子清过期) |
| `resource:pod:{pod}:info` | HASH | scope_id/pod_sse_url/pod_ip/namespace/phase/created_ts/deploy_ver |
| `resource:pod:{pod}:idle_since` | STR | idle 起始(reclaim 计时);存在 ⟺ 在 idle 池 |
| `resource:pod:{pod}:health_fails` | STR | 健康探测连续失败次数(场景 N) |
| `resource:pods:all` | SET | 全部 pod_id(watch/reconcile 枚举) |
| `lock:rm:deploy:{sid}` | STR(NX EX 360) | per-scope deploy 选主串行 |
| `lock:rm:autoscale\|reclaim\|watch\|reconcile` | STR(NX EX) | 后台任务 tick 级选主 |
| `idem:{request_id}` | STR | acquire 结果幂等缓存(TTL 60) |

计数全部派生自 SCARD/ZCARD,无独立计数器。

`eval()` 统一出口带异常留痕(同 SM:空表异常 WARNING、>200ms 慢 eval WARNING、常规 DEBUG)。**诊断只读方法**:`health_fails(pod_id)`(/debug/scope 用)。

## lua_scripts.py —— 6 个 Lua

| 脚本 | 一句话职责 |
|---|---|
| `LUA_ACQUIRE` | 取暖 Pod 复用(**跳过 deploy_ver 不匹配**——A 类变更后老版本暖 Pod 不外发,按 pod_ttl 自然回收)→ 无匹配判 max_pods(ZCARD pods + SCARD deploying)→ 占位 SADD deploying → need_deploy |
| `LUA_PLACEHOLDER` | autoscale 专用占位(判 max_pods + SADD,**不碰 idle 池**——补位不该消耗暖 Pod) |
| `LUA_REGISTER` | deploy 成功登记:pod:info / scope:pods / pods:all 同写,清占位;idle_flag=1(热备)入 idle 池 |
| `LUA_RELEASE` | idle_consider:转 idle 暖池 + 起 pod_ttl 计时(SADD/SET 天然幂等) |
| `LUA_PURGE` | Pod 死亡/reclaim 后清全部 RM key(返回其 scope_id;幂等) |
| `LUA_DEPLOY_FOLLOWER_GATE` | follower 等待室原子准入:先 `ZREMRANGEBYSCORE` 清过期 → ZADD 先行 → ZCARD 超限自退(纪律同 LUA_WAITER_GATE,禁止先查后加) |

约定同 SM:不传 KEYS,`ARGV[1]`=前缀,返回扁平字符串数组。

## k8s.py —— K8s 适配层

`K8sPodClient` 抽象(Real/Fake 同签名):`start/close/deploy/delete/get_pod/list_pods/probe_health`。

**RealK8sPodClient**(kubernetes_asyncio):
- `start()`:先 `load_incluster_config()`(**同步函数,不可 await**——await 会 TypeError→in-cluster 必挂,M7 修复),ConfigException 再 `await load_kubeconfig()`。
- `deploy(pod_spec)`:pod_id = `{pod_name}-{随机10}-{随机5}`(**K8s 随机 Pod 名,严禁业务 id 当实例 id——历史死锁根因**);409 名字冲突重命名重试至多 3 次;`_wait_ready` 轮询至 Ready+有 podIP(每 30s 一条 INFO 进度行,终态/超时 WARNING 带 waited_s),终态(Failed/Succeeded)/消失/超时 → DeployFailed;`get_pod`/`list_pods`/`delete` 带 DEBUG 耗时;`probe_health` 异常原因 DEBUG 留痕(调用方 sweeper 同节奏 WARNING)。
- `_build_pod_body`:label `{jiuwenclaw-component: agentserver, app: pod_id}`;NFS 卷挂载;资源 requests/limits;sse_port 必开(名 `sse`),container_port≠sse_port 加 `http`;readiness probe = `GET /health:sse_port`(AgentServer 固定约定,场景 N);restart_policy=Always。
- `normalize_phase`:deletion→Terminating;容器 waiting reason(ImagePullBackOff/CrashLoopBackOff/…)优先于 phase。

**FakeK8sPodClient**(local/单测):deploy 立即 Ready;可编程 `unready_pods`/`dead_pods`/`unhealthy_pods`/`deploy_failures` 模拟异常分支。

`probe_health(pod_ip, sse_port)`:`GET http://{pod_ip}:{sse_port}/health`,3s 超时,非 200/异常即不健康(K8sPodClient 基类默认实现,Real/Fake 共用)。

## models.py

- `DEAD_POD_STATUSES`:Terminating/Failed/CrashLoopBackOff/ImagePullBackOff/ErrImagePull/InvalidImageName。**Pending 不判死**(deploy 靠 ready_timeout 兜)。
- `POD_LABEL_SELECTOR = "jiuwenclaw-component=agentserver"`(cleanup 默认 selector)。
- `PodDeployInfo`(deploy 产物物理信息)/`PodInfo`(get/list 状态视图:归一化 phase+ready+reason)。

## sweeper.py —— 四个后台任务

均 per-scope 操作、不读 SM Redis key、各自带 tick 级选主锁(调度由 main 注入):

| 任务 | 周期/锁 | 逻辑 |
|---|---|---|
| `autoscale_once`(场景 H) | 1s / lock:rm:autoscale | 遍历 `known_scope_ids()`(SCAN scope:config):idle < min_idle_pods 且 pods+deploying < max_pods → `LUA_PLACEHOLDER` 占位 → 抢 deploy 锁 → `_deploy_and_register(idle_flag=True)` 热备入池;pod_spec 取 scope:config 缓存(A 类变更后为新值) |
| `reclaim_once`(场景 K) | 1s / lock:rm:reclaim | idle 超 min_idle 底数的 excess 中 `aged ≥ pod_ttl` → `_purge_and_notify`(K8s delete → LUA_PURGE → notify_pod_dead);保护最早入 idle 的 min_idle 个(保底热备) |
| `watch_once`(场景 J/N) | 10s / lock:rm:watch(TTL 15) | 遍历 pods:all:get_pod 为 None 或 phase∈DEAD → 清理;Running 但 `probe_health` **连续 2 次失败**(health_fails 阈值,防瞬时抖动误杀)→ 半死清理;成功清零计数。sse_port 从 scope:config 的 pod_spec_json 取 |
| `reconcile_once`(场景 L) | 30s / lock:rm:reconcile(TTL 60) | ① Redis 有 K8s 无 → PURGE+notify;② RM 持有但 SM 候选集已无的 stale Pod(经 `sm_facade.reconcile_pods`,Facade 单向)→ `LUA_RELEASE` 转 idle 按 pod_ttl 回收 |

`_purge_and_notify(pod_id)` 三步(K8s delete 若还在 → LUA_PURGE → notify_pod_dead);全幂等,单步失败仅记录(30s reconcile 兜底);PURGE 失败 `logger.exception` + 成功 INFO `pod purged`(三步可审计)。

日志纪律:四个 `*_once` 每拍一条 DEBUG 汇总(计数聚合 + duration,如 `autoscale tick: scopes=N skip_warm=N deployed=N`),仅真正动作用 INFO;`_health_probe` 数据缺失(pod_ip/sse_port 空 → 探测被静默跳过)按 pod 去重 WARNING(`_probe_gap_warned`,仅诊断用进程内集合)。

## 高频踩点

- 错误路径必须清占位(deploy 失败 → SREM deploying);follower 路径还要清 deploy_followers 成员——**双清纪律**。
- 跨副本冷竞争:follower 等待室保证并发冷启动「恰好 1 次部署」;测试断言「窗口零重叠 + Pod ≤ max_pods」,多后端冷突发 NO_POD_AVAILABLE 快失败属预期。
- cleanup 空目标必须用**无匹配 label_selector**(业务 ns 同 label 会误删真实 AgentServer;不存在 ns 在 in-cluster SA 下 403 而非空列表)。
- 框架 `load_incluster_config` 是同步函数(已修);`kubernetes_asyncio` 仅 server extra 依赖,local 模式不得 import(判定用 `getattr(exc, "status")`)。
