# session_manager(SM)规格

> 会话编排:route/touch HTTP 端点、配置层(config_sync 全量下发 + 路由匹配)、老化 sweeper。
> **持唯一 App**(`/api/session`:8091),注册 4 个 handler。与 RM 互调只走进程内 Facade,**不直读 RM Redis key**。
> Lua 全文:`lua_scripts.py` 与 `../design/session-manager-design.md` 双份,改时同步。

## 文件一览

| 文件 | 职责 |
|---|---|
| `handlers.py` | 4 个 HTTP handler(route/touch/config_sync/cleanup)+ 错误信封映射 |
| `orchestrator.py` | route 主循环(匹配→Lua 仲裁→acquire→等待队列)+ touch |
| `state.py` | SM Redis 键 schema 唯一出口 + Lua 调用封装(`SMKeys`/`SessionState`) |
| `lua_scripts.py` | 7 个 Lua 全文 |
| `routing.py` | 路由匹配纯函数:routing_rules 表达式解析(词法+递归下降)/scope 定义、wire 校验、快照(反)序列化、first-fit 匹配 |
| `config_store.py` | template/routing_scope DB 持久化 + 路由快照 + config_sync 编排 |
| `sweeper.py` | 到期 pass + 空 Pod pass(每 tick 选主) |
| `facade.py` | `SessionManagerFacade`(RM→SM:notify_pod_dead / reconcile_pods) |
| `models.py` | `Template` dataclass(字段/派生/pod_spec) |

## handlers.py —— 对外 4 端点

| 端点 | handler | 行为 |
|---|---|---|
| POST /api/session/route | `handle_route` | 同步路由+占额度,返回 `{pod_sse_url, pod_id}`;**幂等键 = metadata.request_id**(框架 idempotency,窗口 60s,回放缓存结果) |
| POST /api/session/touch | `handle_touch` | 保活/EOS,返回 `{touched}`;False=已过期/不存在(gateway 回退重新 route) |
| POST /api/session/config_sync | `handle_config_sync` | 全量配置下发 `{templates, scopes}`,委托 `ConfigStore.config_sync` |
| POST /api/session/cleanup | `handle_cleanup` | 运维批删 Pod,委托 `rm_facade.cleanup`(handler 在 SM,逻辑在 RM) |

- 入参从 `Envelope.metadata`(session_id/user_id/group_id/bot_id/request_id)与 `rawdata` 取;`group_id` 在 `metadata.extra`,**user_id/group_id/bot_id/session_id 四项均必填非空**(orchestrator 校验,缺 → 400 VALIDATION)。
- `AgentRuntimeError` 统一捕获 → `ResponseEnvelope(ok=False, error_code, error_message, retry_after)`。
- handler 无模块级可变状态;服务对象从 `sysctx` 取(`main._bind_modules` 注入)。

## orchestrator.py —— route 主循环

`SessionOrchestrator.route(request_id, session_id, group_id, bot_id, user_id)`:

```
四参非空校验(缺 → InvalidParams 400)
→ resolve(user_id, group_id, bot_id)(config_store:读路由快照 first-fit 匹配)
   返回 (scope_id, template);无匹配 → ConfigNotFound(503)
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
| `routing:snapshot` | STRING | **路由快照**:全部 scopes(规则/index)+ templates 的 JSON;resolve 唯一读源,config_sync 原子 SET 覆盖,缺失/损坏由首次 resolve 从 DB 重建 |
| `scope:{sid}:sessions` | SET | 活跃 session;**SCARD = scope_concurrency 闸门** |
| `scope:{sid}:pods` | ZSET | first-fit 候选(score=接入序;ZREM 即退出候选——软摘除/idle 通知都用它) |
| `scope:{sid}:pod_seq` | STRING | 单调递增,pods 的 score 来源 |
| `scope:{sid}:waiters` | SET | 等待队列(LUA_WAITER_GATE 原子进出) |
| `scope:{sid}:free` | PubSub | 额度释放信号(EVICT 发布/route 订阅) |
| `pod:{scope}:{pod}:sessions` | SET | per-Pod 会话;**SCARD < pod_concurrency = per-Pod 容量闸门(SM 侧,RM 不强制)** |
| `pod:{scope}:{pod}:info` | HASH | sse_url / deploy_ver |
| `pod:{scope}:{pod}:idle_notified` | STR(NX EX 60) | 空 Pod 通知去重 |
| `pods:registered` | SET | 全部 `"{scope}:{pod}"`(不变量:scope:pods ⊆ pods:registered;**因此 scope_id 禁 `:`**——config_sync 入口正则校验) |
| `pods:{pod}:scopes` | SET | Pod 被哪些 scope 引用(notify_pod_dead 反查) |
| `lock:sweep` / `lock:config_sync` | STR(NX EX) | tick 级选主 / config_sync 串行化(TTL 60) |

不变量 1:一个活跃会话同时存在于四处(session HASH + scope:sessions + pod:sessions + session_expiry)——EVICT/惰性回收四处同删 + PUBLISH free。

`eval()` 统一出口带异常留痕(排障):Lua 返回空表属真异常 → WARNING(`route_place` 的 scope_full 兜底会掩盖);单次 >200ms → WARNING(`lua eval slow`,即 Redis 延迟探针);常规仅 DEBUG。

**诊断只读方法**(/debug/* 用,无业务调用方):`session_hash(sid)`、`session_expiry_score(sid)`、`scope_session_count(sid)`(SCARD)、`routing_snapshot_raw()`(快照原文)。

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

## config_store.py —— 配置层(scope 重构版)

**DB 表**(列名沿用 EE 兼容名,映射在 `_COLUMN_OF`):`service_config_template`(`min_idle_pods→min_idle_services`、`pod_concurrency→service_concurrency`、`pod_ttl→service_ttl`、`scope_concurrency→session_concurrency`)、`routing_scope`(`scope_id` unique / `match_index`(避 SQL 保留字 index) / `template_id` / `routing_rules` JSON)。表结构常量 `*_TABLE_DEF` 由 main 传给框架建表。旧 `routing_rule` 表已废弃(不再读写,老库残留无害)。

**routing.py(纯函数)**:`routing_rules` 是**布尔表达式字符串**——条件 `field in|not in ('v1', 'v2')` 经 `and`/`or` 与括号任意组合;优先级 条件 > and > or;关键字大小写不敏感,字段名固定小写枚举(user_id/group_id/bot_id);值单引号串(`''` 加倍或 `\'`/`\\` 转义);空值列表 `()` → in 恒假、not_in 恒真;不支持一元 `not`;上限长度 8000、括号嵌套 32。**空 routing_rules(null/空串/纯空白)= 通配兜底**;遍历按 `(index ASC, scope_id ASC)` **first-fit**;引用模板缺失/禁用的 scope 跳过落下一个。解析器 = 词法(`_TOKEN_RE`)+ 递归下降(`_Parser`:or_expr → and_expr → primary),产物为表达式树(`MatchExpression` 叶 / `AndNode` / `OrNode`),存于 `RoutingScopeDef.rule`(与原始串 `expr` 成对,后者是 wire/DB/快照载体)。`SCOPE_ID_RE = ^[0-9A-Za-z._-]{1,128}$`(禁 `:`/`*`/空白——Redis 键与 `pods:registered` 切分依赖)。

**resolve(user_id, group_id, bot_id) → (scope_id, Template)**:读单键快照 `routing:snapshot`(1 GET;进程内按原文 memo 免重复解析)→ first-fit 匹配;快照缺失/损坏 → 从 DB 重建;无匹配 → `ConfigNotFound(503)`。

**config_sync(payload)**(场景 M,全量快照式):`{templates: [...], scopes: [...]}`(旧 kind/op 协议 → 400)

```
锁外校验(纯 CPU 400):kind/op 遗迹拒绝;templates/scopes 非 list;template 缺 template_id;
  scope_id 字符集/index 非真 int(拒 bool)/引用不在本批模板集/routing_rules 非字符串
  (含旧结构化 list 格式)/表达式语法错误(未知字段、裸 not、悬空括号、未引号值、
  超长 >8000 或嵌套 >32)/同批 scope_id 重复
  缺通配 scope(无空表达式项)→ 仅 WARNING 放行(响应 wildcard_present:false)
lock:config_sync 串行化(忙→409 CONFIG_SYNC_BUSY,TTL 60)
→ 读 DB 旧态(templates + scopes)
→ diff:模板 changed_ids(_diff_class 沿用)/ 引用切换 ref_switched;
  sunset_scopes = 有效模板 deploy_ver 前后不同(模板 A 类 或 scope 换引用且版本不同)的存活 scope
→ 日落中间态检查(受影响 scope 有「在 pods:registered 不在候选集」的 Pod → 409;★先于写库,拒绝时零副作用)
→ 写 DB(upsert incoming + delete 消失项;红线:任一失败立即中止,不 SET 快照、不推送)
→ rebuild_snapshot()(DB 读回 → 原子 SET;B 类立即生效由此完成)
→ eager 预热:每个存活 scope 推 push(sid, pool_config, deploy_subset)——必须带 pod_spec
  (RM 才落 pod_spec_json/deploy_ver;autoscale 无请求预热 min_idle 的依赖)
→ A 类日落:sunset_scopes 逐个 _soft_remove_stale_pods(ZREM 老版本 Pod 出候选)
→ 删除处理:消失 scope 推 push(sid, {**旧模板池参数, min_idle_pods:0}, None)——停预热自然排空
→ 响应 {ok, templates_synced/deleted, scopes_synced/deleted, affected_scopes, wildcard_present}
```

幂等重放收敛(changed 空 → affected=[]);启动期 `main.start()` 调 `ensure_snapshot()` 无条件重建(消冷启动窗口);`Template.deploy_ver()` / RM `_deploy_ver()` 同一算法(`util.fingerprint` + `DEPLOY_VER_FIELDS`)——A 类过滤两端一致的前提。

## sweeper.py —— 老化扫描

`SessionSweeper.sweep_once()`(tick=`sweep_interval` 1s;`lock:sweep` SET NX EX 2 选主,抢不到即跳过):
- **到期 pass**:ZRANGEBYSCORE `session_expiry` → 逐个 `LUA_EVICT`(废弃 session 的唯一回收路径)。
- **空 Pod pass**(idle_consider 的**唯一触发点**):枚举 `pods:registered` → `LUA_SWEEP_IDLE_NOTIFY` 原子判定(空+未通知过+ZREM 退出候选)→ notified=True 才 **fire-and-forget** `rm_facade.idle_consider`(失败不阻塞;60s 后 idle_notified 过期自愈重发)。统一覆盖三种空 Pod 成因:到期 evict / 惰性 evict / acquire 后从未放置的孤儿。

## facade.py —— RM→SM 入口

- `notify_pod_dead(pod_id)`(场景 G):反查 `pods:{pod}:scopes` → 逐 session `LUA_EVICT`(释放额度+唤醒等待者)→ `LUA_CLEANUP_POD` 清注册;幂等。
- `reconcile_pods(view)`(场景 L):对 RM 持有的每个 (pod, scope) 查 `scope:pods` 成员资格,非成员=SM 已不用 → stale;只读、单向。

## models.py

- `Template`:template 行业务视图。派生:`max_pods = ⌈scope_concurrency/pod_concurrency⌉`(**派生值,不存储,不配置**);`deploy_subset()`=acquire 下发 RM 的 pod_spec;`deploy_ver()`=A 类指纹;`pool_config()`={min_idle_pods, max_pods, pod_ttl, pod_concurrency}(pod_concurrency 仅供 RM follower 等待室推导上限 pc-1)。
- scope 定义(`RoutingScopeDef`/表达式树)在 `routing.py`,不再有 ScopeConfig(快照取代 per-scope 缓存)。

## 高频踩点

- 改键名/Lua:HLD §5 键表、`state.py`、SM 详细设计三处同步。
- 等待队列入队只准走 `LUA_WAITER_GATE`;「先查后加」已被真环境验收证伪。
- config_sync 全局串行锁——e2e 脚本播种配置必须串行,并发即 409。
- scope_id 禁 `:`(Redis 键与 `pods:registered` 的 `{scope}:{pod}` 切分依赖)——入口 `SCOPE_ID_RE` 强校验,新增拼键代码时勿破坏该前提。
- config_sync 的 RM 推送**必须带 pod_spec**(eager 预热依赖 pod_spec_json;不带则 autoscale `skip_no_spec`,无请求 scope 永远不预热)。
- 经多副本 LB 跑冒烟须设 `AGENT_RUNTIME_SCOPE_FULL_TIMEOUT`(显著小于 session_ttl),排查实录见 `e2e-test-cases.md` §8.1。
