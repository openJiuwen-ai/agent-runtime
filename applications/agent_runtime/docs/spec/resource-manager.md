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
| `update_pool_config(scope_id, pool_config, pod_spec?)` | `{updated}`(config_sync 触发;mapping **永不含 generation**) |
| `bump_generation(scope_id)` | `generation: int`(config_refresh 触发的代次日落,HINCRBY 原子自增,唯一写点) |
| `cleanup(namespace?, label_selector?)` | `cleaned: int`(运维批删) |
| `known_scope_ids()` | RM 已知 scope 枚举(SCAN scope:config;config_sync 的被删 scope drain 收敛用——RM config 键是幻影预热的真源) |

## orchestrator.py —— acquire 决策树

```
幂等缓存(request_id,键 {resource_manager}:idem:{rid},TTL 60,命中续期;
  ★存活校验:缓存 Pod 已被 PURGE(watch/reclaim 判死)→ 弃缓存清键走全新
  acquire——回放死 Pod 会在 SM 侧复活注册并持续喂死地址给重试客户端)
→ 首见 scope:缓存池参数 + pod_spec_json 到 resource:scope:{sid}:config
→ 循环 { LUA_ACQUIRE → (action, pod_id, sse_url):
     reuse        → 直接返回(deploy_ver+generation 过滤后的暖 Pod,已弹出 idle 池)
     max_reached  → 清占位 → MaxPodsReached
     no_config    → continue(上面已写配置)
     need_deploy  → 抢 lock:rm:deploy:{scope}(TTL 360,盖住 ready_timeout 300+余量):
                    赢家 → _deploy_and_register(idle_flag=False)
                    输家 → 清占位 → _follow_leader(follower 等待室) }
```

`_deploy_and_register`:`k8s.deploy(pod_spec)`(create+wait Ready)→ 拼 `pod_sse_url = http://{pod_ip}:{sse_port}{sse_path}` → `LUA_REGISTER`(带 sse_port/health_path,Pod 烘焙自己的探测契约)。**deploy 与 REGISTER 都在 `except BaseException` 保护内**(红线:占位清理含取消路径;REGISTER 步失败不清占位一样虚占 max_pods);异常若携带 pod_id/namespace(k8s.deploy 契约)→ 兜底 `k8s.delete` 防孤儿物理 Pod。

`_follow_leader`(M8,deploy 锁输家的等待室):
- 准入走 `LUA_DEPLOY_FOLLOWER_GATE` 原子闸门,上限 `pod_concurrency - 1`(leader 会话之外新 Pod 恰剩这些槽);overflow 严格快失败 MaxPodsReached。
- 等待有界:`ready_timeout + 10s` 余量;轮询 `resource:scope:{sid}:pods` 出现新 Pod 且 pod:info 有 sse_url → **直接复用返回**(与 reuse 分支同构,SM 侧重跑仲裁即可)。
- leader 失败判定:deploy 锁空闲且无新 Pod → `DeployFailed`(**follower 不接管**——同镜像同环境大概率也失败);deadline 到 → MaxPodsReached。
- 等待期进度行:每 `FOLLOWER_PROGRESS_LOG_SEC`(5s)一条 INFO `follower still waiting: scope= follower= waited_s=`——ready_timeout 最长 300s,INFO 下不留日志空白(部署风暴期的观测窗口);复用成功另有一条 INFO `acquire follower reuses leader pod`。
- 错误路径双清:占位 + follower 成员都进 finally;崩溃遗留由闸门 `ZREMRANGEBYSCORE(deadline)` 兜底。

`idle_consider`:`LUA_RELEASE` 转 idle 暖池(起 pod_ttl 计时)+ pod:info.phase=idle;幂等。
`update_pool_config`:HSET 覆盖池参数;A 类变更附带 pod_spec 时同时刷 deploy_ver/pod_spec_json(autoscale 补位用新 deploy 字段);**mapping 永不含 generation——代次只经 `bump_generation` 单调递增,推送永不重置**。
`bump_generation`:HINCRBY `scope:config.generation`;现有 Pod 代次全部落后 → LUA_ACQUIRE 过滤 / `_current_version_idle` 判 stale / autoscale 重建(场景 M-R,SM 的 config_refresh 触发)。"当前版本"判定 = **deploy_ver 相等 ∧ generation 相等**;两侧同缺(空串)视为一致——从未刷新过的 scope 零行为变化。
`cleanup`:K8s list+delete,**不操作 Redis 编排态**(被删 Pod 由 watch/reconcile 兜底发现);ns 404 容忍为 cleaned=0,**403 保持 fail-fast**(静默清零会掩盖部署配错);逐 Pod 一条 INFO `cleanup deleted pod: pod= namespace=`(批删中途中断时可见删到哪;k8s.delete 自身明细在 DEBUG)+ 结尾 WARNING 聚合。

## state.py —— RM 键表(`RMKeys`,前缀 `{resource_manager}:`,业务键再带 `resource:` 段)

| 键 | 类型 | 语义 |
|---|---|---|
| `resource:scope:{sid}:pods` | ZSET | 该 scope 全部 Pod(in_use ∪ idle);**ZCARD+deploying SCARD 参与 max_pods 判定** |
| `resource:scope:{sid}:idle` | SET | idle 暖池;acquire 从此取暖 Pod |
| `resource:scope:{sid}:config` | HASH | min_idle_pods/max_pods/pod_ttl/pod_concurrency/deploy_ver/pod_spec_json/**generation**(config_refresh 的代次日落标记,**唯一写点 = HINCRBY**,config_sync 推送永不重置)。**config_sync 对每个存活 scope 主动写入/刷新(带 pod_spec)——无请求 scope 的 min_idle 预热依赖它**;首 acquire 兜底写入;被删 scope 推 min_idle=0 停预热自然排空 |
| `resource:scope:{sid}:deploying` | ZSET | deploy 占位 token→deadline 秒级 score(计入 max_pods,防并发超配;闸门/autoscale 按 deadline 原子清崩溃遗留——硬崩后进程内清理不存在,占位不得永久虚占容量) |
| `resource:scope:{sid}:deploy_followers` | ZSET | follower 等待室(request_id→deadline 秒级 score;闸门按 deadline 原子清过期) |
| `resource:pod:{pod}:info` | HASH | scope_id/pod_sse_url/pod_ip/namespace/phase/created_ts/deploy_ver/**sse_port/health_path**(Pod 自己烘焙的探测契约;A 类变更后 scope 当前配置已换代,watch 探测必须用 Pod 自己的参数,否则存量老 Pod 被探错路径误杀)/**generation**(注册时刻代次烙印,REGISTER 服务端读 scope:config——与 bump 原子排队,deploy 中途刷新不误伤晚注册的新 Pod) |
| `resource:pod:{pod}:idle_since` | STR | idle 起始(reclaim 计时);存在 ⟺ 在 idle 池 |
| `resource:pod:{pod}:health_fails` | STR | 健康探测连续失败次数(场景 N) |
| `resource:pods:all` | SET | 全部 pod_id(watch/reconcile 枚举) |
| `lock:rm:deploy:{sid}` | STR(NX EX 360) | per-scope deploy 选主串行 |
| `lock:rm:autoscale\|reclaim\|watch\|reconcile` | STR(NX EX) | 后台任务 tick 级选主 |
| `idem:{request_id}` | STR | acquire 结果幂等缓存(TTL 60) |

计数全部派生自 SCARD/ZCARD,无独立计数器。

`eval()` 统一出口带异常留痕(同 SM:空表异常 WARNING、>200ms 慢 eval WARNING、常规 DEBUG)。**诊断只读方法**:`health_fails(pod_id)`(/visualization/scope 用)。

## lua_scripts.py —— 6 个 Lua

| 脚本 | 一句话职责 |
|---|---|
| `LUA_ACQUIRE` | 取暖 Pod 复用(**跳过 deploy_ver 或 generation 不匹配**——A 类变更后老版本、config_refresh 后老代次暖 Pod 不外发,由 reclaim 版本/代次感知回收)→ 无匹配判 max_pods(ZCARD pods + ZCARD deploying)→ 占位 ZADD deploying(score=deadline,先清过期) → need_deploy |
| `LUA_PLACEHOLDER` | autoscale 专用占位(判 max_pods + ZADD,**不碰 idle 池**——补位不该消耗暖 Pod;同款 deadline 自清) |
| `LUA_REGISTER` | deploy 成功登记:pod:info(含 sse_port/health_path + **generation 服务端烙印**——读注册时刻 scope:config 当前代次)/ scope:pods / pods:all 同写,清占位;idle_flag=1(热备)入 idle 池 |
| `LUA_RELEASE` | idle_consider:转 idle 暖池,**仅首次转入(SADD=1)起 pod_ttl 计时**;周期重放(reconcile stale/idle_consider 去重重发)不刷新计时——否则空闲 Pod 永不回收;acquire 弹出后再转 idle 重新计时;**已 PURGE 的 Pod(info 已清)no-op**(防 TOCTOU 幽灵成员) |
| `LUA_PURGE` | Pod 死亡/reclaim 后清全部 RM key(返回其 scope_id;幂等) |
| `LUA_DEPLOY_FOLLOWER_GATE` | follower 等待室原子准入:先 `ZREMRANGEBYSCORE` 清过期 → ZADD 先行 → ZCARD 超限自退(纪律同 LUA_WAITER_GATE,禁止先查后加) |

约定同 SM:不传 KEYS,`ARGV[1]`=前缀,返回扁平字符串数组。

## k8s.py —— K8s 适配层

`K8sPodClient` 抽象(Real/Fake 同签名):`start/close/deploy/delete/get_pod/list_pods/probe_health`。

**RealK8sPodClient**(kubernetes_asyncio):
- `start()`:先 `load_incluster_config()`(**同步函数,不可 await**——await 会 TypeError→in-cluster 必挂,M7 修复),ConfigException 再 `await load_kubeconfig()`。
- `deploy(pod_spec)`:pod_id = `{pod_name}-{随机10}-{随机5}`(**K8s 随机 Pod 名,严禁业务 id 当实例 id——历史死锁根因**);409 名字冲突重命名重试至多 3 次;`_wait_ready` 轮询至 Ready+有 podIP(每 30s 一条 INFO 进度行,终态/超时 WARNING 带 waited_s),终态(Failed/Succeeded)/消失/超时 → DeployFailed;**create 之后的任何失败/取消先 best-effort 删除该 Pod 再抛,DeployFailed 携带 pod_id/namespace 属性供上层兜底**(契约:失败路径不留孤儿物理 Pod——未 REGISTER 的 Pod 不在 pods:all,watch/reconcile 只做 Redis→K8s 单向对账,孤儿无人认领);`get_pod`/`list_pods`/`delete` 带 DEBUG 耗时;`probe_health` 异常原因 DEBUG 留痕(调用方 sweeper 同节奏 WARNING)。
- `_build_pod_body`:label `{jiuwenclaw-component: agentserver, app: pod_id}`;NFS 卷挂载;资源 requests/limits;sse_port 必开(名 `sse`),container_port≠sse_port 加 `http`;**模板 `agent_env` 注入容器 env**(真 AgentServer 需 `AGENT_HTTP_ENABLED/HOST/PORT` 开 HTTP 入口);**`agent_env_from`(envFrom 引用)→ 主容器 `envFrom`(`_render_env_from`:secretRef/configMapRef/prefix/optional 逐字段透传;缺省 None 不设——与历史行为逐字节一致;脏缓存坏项跳过)**,sidecar 子键 `env_from` 同款(值不落模板/快照,只传引用名——密钥不再明文);readiness probe = `GET {health_path:-/health}:sse_port`;restart_policy=Always。`probe_health`(场景 N)与 readiness 同源取 `health_path`(sweeper 从 scope:config 的 pod_spec_json 读)。
- `_build_pod_body` **多容器(pod_spec.sidecars,规范形见 `sidecars.py`)**:入口 `normalize_sidecars` 兜底(pod_spec 可能来自 Redis 缓存脏数据,坏项静默丢弃);`find_sidecar_conflict`(撞主容器名/撞 agent 端口)→ **DeployFailed**(fail-fast,防 agent 经 127.0.0.1 连错进程);每项经 `_build_sidecar_container` 渲染 V1Container——端口纯声明性**无名**(sidecar 只被同 Pod 127.0.0.1 访问,不进 Service)、独立 resources、security_context(privileged/caps/seccomp/run_as)、apparmor unconfined 落 **Pod annotation**、tcp/http readiness 探针;`containers=[主容器, *sidecars]`,无 sidecars 时 annotations=None、单容器——**与历史逐字节一致**。sidecar readiness 参与 Pod Ready → `_wait_ready` 天然等 sidecar 就绪(慢启动 sidecar 需调大模板 ready_timeout)。
- **卷挂载渲染(`mounts.py` 规范形,主容器与 sidecar 共用 `_render_volume_mounts`)**:hostPath/ConfigMap/PVC 三种;卷名 `_scoped_volume_name(prefix, 容器名净化, 容器idx, 挂载idx)`,前缀 `hp-`/`cm-`/`pvc-`,≤63 防撞,与主容器 NFS 卷名 `{pod_id}-nfs` 天然不撞(容器名唯一保证跨容器不撞);**PVC 同 claim 跨容器去重**:`_build_pod_body` 以 `pvc_seen` 登记簿贯穿主容器与 sidecars,同 claim 只建**一个**共享卷(卷名取首现容器,主容器先渲染),后继容器的 volumeMount 复用该卷名——对齐 gateway 写法,防 kubelet 挂第二个同 claim 卷死锁/超时;卷级 `read_only` 取首现值(kubelet 语义:卷源 ro 压 mount 级 rw,主 ro + sidecar rw 组合下 sidecar 实际只读);ConfigMap 支持 `sub_path`(单 key 挂到文件)与 `items`(V1KeyToPath);主容器三字段 `agent_host_path_mounts`/`agent_configmap_mounts`/`agent_pvc_mounts` 与 sidecar 子字段同款;RM 入口 `normalize_mounts` 兜底脏缓存;无挂载时零增量(与历史一致)。
- `_build_pod_body` **pod 落位字段**:模板 `node_name` → `V1PodSpec.node_name`(绕调度器点名绑节点,`None`/空串不设);`run_as_user`/`run_as_group` → 主容器 `securityContext.runAsUser/runAsGroup`(覆盖镜像 `USER`;给了才设,`None` 不设键——与历史 Pod 零差异;sidecar 的同名字段早有,这是主容器对齐)。
- `normalize_phase`:deletion→Terminating;容器 waiting reason(ImagePullBackOff/CrashLoopBackOff/…)优先于 phase。

**FakeK8sPodClient**(local/单测):deploy 立即 Ready;可编程 `unready_pods`/`dead_pods`/`unhealthy_pods`/`deploy_failures`(create 前失败,无物理残留)/`fail_after_create`(create 成功但永不 Ready——Pod 留在集群、DeployFailed 携带 pod_id,考验上层兜底删除)模拟异常分支;`deployed_specs` 录制每次 deploy 收到的 pod_spec(断言 pod_spec 端到端透传,如 sidecars)。

`probe_health(pod_ip, sse_port, health_path="/health")`:`GET http://{pod_ip}:{sse_port}{health_path}`,3s 超时,非 200/异常即不健康(K8sPodClient 基类默认实现,Real/Fake 共用;调用方 sweeper 按 Pod 自己的 info 参数传,回退 scope 当前配置)。

## models.py

- `DEAD_POD_STATUSES`:Terminating/Failed/CrashLoopBackOff/ImagePullBackOff/ErrImagePull/InvalidImageName。**Pending 不判死**(deploy 靠 ready_timeout 兜)。
- `POD_LABEL_SELECTOR = "jiuwenclaw-component=agentserver"`(cleanup 默认 selector)。
- `PodDeployInfo`(deploy 产物物理信息)/`PodInfo`(get/list 状态视图:归一化 phase+ready+reason)。

## sweeper.py —— 四个后台任务

均 per-scope 操作、不读 SM Redis key、各自带 tick 级选主锁(调度由 main 注入):

| 任务 | 周期/锁 | 逻辑 |
|---|---|---|
| `autoscale_once`(场景 H) | 1s / lock:rm:autoscale | 先 `reap_expired_deploying`(崩溃遗留占位自愈)再遍历 `known_scope_ids()`(SCAN scope:config):**当前版本+代次** idle < min_idle_pods(`_current_version_idle`:deploy_ver ∧ generation 均与 scope:config 一致——A 类变更后旧版、config_refresh 后老代次 idle Pod 永不可能被复用,不能拿来满足 min_idle,否则暖池被旧版钉死)且 pods+deploying < max_pods → `LUA_PLACEHOLDER` 占位 → 抢 deploy 锁 → `_deploy_and_register(idle_flag=True)` 热备入池;pod_spec 取 scope:config 缓存(A 类变更后为新值;刷新后同值仅换代)。**config_sync 会主动写每个存活 scope 的 config(带 pod_spec)→ 从未被请求过的 scope 也会被预热(eager,下发即预备热备)** |
| `reclaim_once`(场景 K) | 1s / lock:rm:reclaim | excess = 旧版本/老代次 idle Pod(**恒为 excess**——acquire want_ver+generation 过滤后永不可复用,若受底数保护则暖池钉死旧版+蹲占 max_pods)+ 当前版本代次 idle 超出 min_idle 的部分(按转 idle 先后);excess 中 `aged ≥ pod_ttl` → `_purge_and_notify`(K8s delete → LUA_PURGE → notify_pod_dead);底数只保护当前版本代次最早入 idle 的 min_idle 个(保底热备)。**scope 被删除时 config_sync 推 min_idle=0 → 本任务把其空闲 Pod 按 pod_ttl 自然排空(存量会话到期止,不强制驱逐)** |
| `watch_once`(场景 J/N) | 10s / lock:rm:watch(TTL 15) | 遍历 pods:all:get_pod 为 None 或 phase∈DEAD → 清理;Running 但 `probe_health` **连续 2 次失败**(health_fails 阈值,防瞬时抖动误杀)→ 半死清理;成功清零计数。**探测参数优先取 Pod 自己 info 烘焙的 sse_port/health_path**(A 类变更后存量老 Pod 用旧契约探测;旧 Pod 无字段时回退 scope:config 的 pod_spec_json) |
| `reconcile_once`(场景 L) | 30s / lock:rm:reconcile(TTL 60) | ① Redis 有 K8s 无 → PURGE+notify;② RM 持有但 SM 候选集已无的 stale Pod(经 `sm_facade.reconcile_pods`,Facade 单向)→ `LUA_RELEASE` 转 idle 按 pod_ttl 回收 |

`_purge_and_notify(pod_id)` 三步(K8s delete 若还在 → LUA_PURGE → notify_pod_dead);全幂等,单步失败仅记录(30s reconcile 兜底);PURGE 失败 `logger.exception` + 成功 INFO `pod purged`(三步可审计)。

日志纪律:四个 `*_once` 每拍一条 DEBUG 汇总(计数聚合 + duration,如 `autoscale tick: scopes=N skip_warm=N deployed=N`),仅真正动作用 INFO;`_health_probe` 数据缺失(pod_ip/sse_port 空 → 探测被静默跳过)按 pod 去重 WARNING(`_probe_gap_warned`,仅诊断用进程内集合)。

## 高频踩点

- 错误路径必须清占位(deploy/REGISTER 任一步失败 → ZREM deploying,保护含 CancelledError);follower 路径还要清 deploy_followers 成员;k8s.deploy 失败若已建 Pod → 兜底物理删除——**三清纪律(占位/成员/物理 Pod)**。
- 跨副本冷竞争:follower 等待室保证并发冷启动「恰好 1 次部署」;测试断言「窗口零重叠 + Pod ≤ max_pods」,多后端冷突发 NO_POD_AVAILABLE 快失败属预期。
- cleanup 空目标必须用**无匹配 label_selector**(业务 ns 同 label 会误删真实 AgentServer;不存在 ns 在 in-cluster SA 下 403 而非空列表)。
- 框架 `load_incluster_config` 是同步函数(已修);`kubernetes_asyncio` 仅 server extra 依赖,local 模式不得 import(判定用 `getattr(exc, "status")`)。
