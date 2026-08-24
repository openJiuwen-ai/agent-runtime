# session_manager(SM)规格

> 会话编排:route/touch HTTP 端点、配置层(config_sync)、老化 sweeper。
> **持唯一 App**(`/api/session`:8091),注册 4 个 handler。与 RM 互调只走进程内 Facade,**不直读 RM Redis key**。
> Lua 全文:`lua_scripts.py` 与 `../design/session-manager-design.md` 双份,改时同步。

## 文件一览

| 文件 | 职责 |
|---|---|
| `handlers.py` | 4 个 HTTP handler(route/touch/config_sync/cleanup)+ 错误信封映射 |
| `orchestrator.py` | route 主循环(resolve→Lua 仲裁→acquire→等待队列)+ touch |
| `state.py` | SM Redis 键 schema 唯一出口 + Lua 调用封装(`SMKeys`/`SessionState`) |
| `lua_scripts.py` | 7 个 Lua 全文 |
| `config_store.py` | template/routing_rule DB 持久化 + resolve 缓存 + config_sync 编排 |
| `sweeper.py` | 到期 pass + 空 Pod pass(每 tick 选主) |
| `facade.py` | `SessionManagerFacade`(RM→SM:notify_pod_dead / reconcile_pods) |
| `models.py` | `Template` / `ScopeConfig` dataclass |

## handlers.py —— 对外 4 端点

| 端点 | handler | 行为 |
|---|---|---|
| POST /api/session/route | `handle_route` | 同步路由+占额度,返回 `{pod_sse_url, pod_id}`;**幂等键 = metadata.request_id**(框架 idempotency,窗口 60s,回放缓存结果) |
| POST /api/session/touch | `handle_touch` | 保活/EOS,返回 `{touched}`;False=已过期/不存在(gateway 回退重新 route) |
| POST /api/session/config_sync | `handle_config_sync` | 配置下发,委托 `ConfigStore.config_sync` |
| POST /api/session/cleanup | `handle_cleanup` | 运维批删 Pod,委托 `rm_facade.cleanup`(handler 在 SM,逻辑在 RM) |

- 入参从 `Envelope.metadata`(session_id/group_id/bot_id/request_id)与 `rawdata` 取;`group_id` 在 `metadata.extra`。
- `AgentRuntimeError` 统一捕获 → `ResponseEnvelope(ok=False, error_code, error_message, retry_after)`。
- handler 无模块级可变状态;服务对象从 `sysctx` 取(`main._bind_modules` 注入)。

## orchestrator.py —— route 主循环

`SessionOrchestrator.route(request_id, session_id, group_id, bot_id)`:

```
resolve(scope)(config_store:Redis 缓存→DB,规则优先级 精确>(g,*)>(*,b)>(*,*))
→ 循环 { LUA_ROUTE_PLACE 原子仲裁 → (action, pod_id):
     refresh/placed → 读 pod:info sse_url 返回(缺失=极端竞态被清,continue 重跑)
     scope_full     → _wait_for_capacity(场景 F)后重跑
     need_acquire   → rm_facade.acquire(扩+1)→ state.register_pod → 重跑(新 Pod 必被 first-fit 选中) }
finally: 若仍在等待队列 → remove_waiter(异常路径出队)
```

`_wait_for_capacity`(场景 F 有界等待):
- `max_waiters = 2 * scope_concurrency`;过 deadline(`scope_full_timeout`)→ 504 ScopeFullTimeout。
- **入队只走 `LUA_WAITER_GATE` 原子闸门**(SADD 先行+超限自退);满 → 503 ScopeQueueFull 快失败。
- 订阅 `scope:{sid}:free` PubSub + ≤500ms 安全轮询双保险(兜 publish 早于 subscribe 的丢失);收到信号即出队重跑 Lua——**原子 admit 是唯一仲裁,败者重 wait**。

`_acquire_pod`:`MaxPodsReached`/`DeployFailed` → 映射 `NoPodAvailable(503, retry_after=1)`。

`touch(session_id)`:LUA_TOUCH;不存在/已过期返回 False。

## state.py —— SM 键表(`SMKeys`,全部含 `session_manager:` 前缀)

| 键 | 类型 | 语义 |
|---|---|---|
| `session:{sid}` | HASH | 亲和绑定:scope_id/pod_id/expiry/session_ttl |
| `session_expiry` | ZSET | 到期时间戳(sweeper 到期 pass 扫它) |
| `scope:{sid}:sessions` | SET | 活跃 session;**SCARD = scope_concurrency 闸门** |
| `scope:{sid}:pods` | ZSET | first-fit 候选(score=接入序;ZREM 即退出候选——软摘除/idle 通知都用它) |
| `scope:{sid}:pod_seq` | STRING | 单调递增,pods 的 score 来源 |
| `scope:{sid}:config` | HASH | resolve 缓存(策略字段 + `template_json`;config_sync 主动 DEL 失效) |
| `scope:{sid}:waiters` | SET | 等待队列(LUA_WAITER_GATE 原子进出) |
| `scope:{sid}:free` | PubSub | 额度释放信号(EVICT 发布/route 订阅) |
| `pod:{scope}:{pod}:sessions` | SET | per-Pod 会话;**SCARD < pod_concurrency = per-Pod 容量闸门(SM 侧,RM 不强制)** |
| `pod:{scope}:{pod}:info` | HASH | sse_url / deploy_ver |
| `pod:{scope}:{pod}:idle_notified` | STR(NX EX 60) | 空 Pod 通知去重 |
| `pods:registered` | SET | 全部 `"{scope}:{pod}"`(不变量:scope:pods ⊆ pods:registered) |
| `pods:{pod}:scopes` | SET | Pod 被哪些 scope 引用(notify_pod_dead 反查) |
| `lock:sweep` / `lock:config_sync` | STR(NX EX) | tick 级选主 / config_sync 串行化(TTL 60) |

不变量 1:一个活跃会话同时存在于四处(session HASH + scope:sessions + pod:sessions + session_expiry)——EVICT/惰性回收四处同删 + PUBLISH free。

## lua_scripts.py —— 7 个 Lua

| 脚本 | 一句话职责 |
|---|---|
| `LUA_ROUTE_PLACE` | route 原子核心:亲和续期→惰性回收旧绑定→scope 闸门(SCARD)→first-fit(接入序)→达 max_pods 则 scope_full / 否则 need_acquire→原子提交四处同写(复用时清 idle_notified) |
| `LUA_EVICT` | session 移除**唯一原语**(四处同删 + PUBLISH free 唤醒等待者;返回 scope/pod/remaining;幂等 noop) |
| `LUA_TOUCH` | 保活续期;已过期当场惰性 evict;ttl 就地读 session HASH(不依赖 scope:config) |
| `LUA_SWEEP_IDLE_NOTIFY` | 空 Pod 判定(SCARD==0)+ 60s NX 去重 + ZREM 退出候选(堵 reclaim 窗口内 route 直选的竞态 A) |
| `LUA_REGISTER_POD` | acquire 成功登记:三处注册(scope:pods/pod:info/pods:registered)+ 接入序 + pods:{pod}:scopes |
| `LUA_CLEANUP_POD` | notify_pod_dead 清该 (scope,pod) 全部注册(会话 evict 由调用方先行) |
| `LUA_WAITER_GATE` | 等待队列原子入队(SADD 先行 + SCARD 超限自退)——**禁止改回「先 SCARD 再 SADD」**(M6 验收发现的并发超收事故) |

约定:脚本不传 KEYS,`ARGV[1]`=键前缀,键在脚本内拼;返回扁平字符串数组(`SessionState.eval` 统一转 str)。

## config_store.py —— 配置层

**DB 表**(列名沿用 EE 兼容名,映射在 `_COLUMN_OF`):`service_config_template`(`min_idle_pods→min_idle_services`、`pod_concurrency→service_concurrency`、`pod_ttl→service_ttl`、`scope_concurrency→session_concurrency`)、`routing_rule`。表结构常量 `*_TABLE_DEF` 由 main 传给框架建表。

**resolve(scope_id, group_id, bot_id)**:缓存(`scope:{sid}:config`)命中直接回;miss 读 DB(优先级 精确>(g,\*)>(\*,b)>(\*,\*),模板须 enabled)→ 回写缓存。缓存值 = ScopeConfig 字段 + `template_json`(deploy 子集,need_acquire 时零 DB)。无匹配 → `ConfigNotFound(503)`。

**config_sync(payload)**(场景 M):`{kind: template|routing_rule, op: create|update|delete|sync, ...}`

```
lock:config_sync 串行化(忙→409 CONFIG_SYNC_BUSY,TTL 60)
→ 写 DB(失败立即中止,不碰缓存、不推送——红线:DB 写失败不得刷新缓存)
→ template 变更扩散 _propagate_template_change:
   完成判定:受影响 scope 仍有「已日落待回收」中间态 Pod(在 pods:registered 不在候选集)→ 409 拒绝
   逐字段 diff 判类(spec_fields):
   A 类(deploy_ver 变)→ 软摘除(ZREM 老版本 Pod 出候选;存量会话不受影响)
                       + 写新缓存 + 推 RM(新 deploy_ver/pod_spec → 新流量落新 Pod,自然滚动)
   B 类(策略字段变)  → DEL 缓存 + 推池参数(不带 pod_spec),立即生效
routing_rule 任何变更 → 无法定位受影响 scope(缓存无 group/bot)→ SCAN 全量 DEL 缓存(resolve 便宜)
```

`Template.deploy_ver()` / RM `_deploy_ver()` 同一算法(`util.fingerprint` + `DEPLOY_VER_FIELDS`)——A 类过滤两端一致的前提。

## sweeper.py —— 老化扫描

`SessionSweeper.sweep_once()`(tick=`sweep_interval` 1s;`lock:sweep` SET NX EX 2 选主,抢不到即跳过):
- **到期 pass**:ZRANGEBYSCORE `session_expiry` → 逐个 `LUA_EVICT`(废弃 session 的唯一回收路径)。
- **空 Pod pass**(idle_consider 的**唯一触发点**):枚举 `pods:registered` → `LUA_SWEEP_IDLE_NOTIFY` 原子判定(空+未通知过+ZREM 退出候选)→ notified=True 才 **fire-and-forget** `rm_facade.idle_consider`(失败不阻塞;60s 后 idle_notified 过期自愈重发)。统一覆盖三种空 Pod 成因:到期 evict / 惰性 evict / acquire 后从未放置的孤儿。

## facade.py —— RM→SM 入口

- `notify_pod_dead(pod_id)`(场景 G):反查 `pods:{pod}:scopes` → 逐 session `LUA_EVICT`(释放额度+唤醒等待者)→ `LUA_CLEANUP_POD` 清注册;幂等。
- `reconcile_pods(view)`(场景 L):对 RM 持有的每个 (pod, scope) 查 `scope:pods` 成员资格,非成员=SM 已不用 → stale;只读、单向。

## models.py

- `Template`:template 行业务视图。派生:`max_pods = ⌈scope_concurrency/pod_concurrency⌉`(**派生值,不存储,不配置**);`deploy_subset()`=acquire 下发 RM 的 pod_spec;`deploy_ver()`=A 类指纹;`pool_config()`={min_idle_pods, max_pods, pod_ttl, pod_concurrency}(pod_concurrency 仅供 RM follower 等待室推导上限 pc-1)。
- `ScopeConfig`:resolve 产物(scope:config 缓存的 HASH 字段一一对应)。

## 高频踩点

- 改键名/Lua:HLD §5 键表、`state.py`、SM 详细设计三处同步。
- 等待队列入队只准走 `LUA_WAITER_GATE`;「先查后加」已被真环境验收证伪。
- config_sync 全局串行锁——e2e 脚本播种配置必须串行,并发即 409。
- 经多副本 LB 跑冒烟须设 `AGENT_RUNTIME_SCOPE_FULL_TIMEOUT`(显著小于 session_ttl),排查实录见 `e2e-test-cases.md` §8.1。
