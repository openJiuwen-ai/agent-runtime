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
| `config_store.py` | template/service_config_container/routing_scope DB 持久化 + 路由快照 + config_sync 编排 |
| `container_spec.py` | 容器规范形层:K8s 形态 wire 解析/校验 + volumes×volumeMounts join + 主/sidecar 投影(SM 私有纯函数) |
| `sweeper.py` | 到期 pass + 空 Pod pass(每 tick 选主) |
| `facade.py` | `SessionManagerFacade`(RM→SM:notify_pod_dead / reconcile_pods) |
| `models.py` | `Template` dataclass(字段/派生/pod_spec) |

## handlers.py —— 对外 4 端点

| 端点 | handler | 行为 |
|---|---|---|
| POST /api/session/route | `handle_route` | 同步路由+占额度,返回 `{pod_sse_url, pod_id}`;**幂等键 = metadata.request_id**(框架 idempotency,窗口 60s,回放缓存结果) |
| POST /api/session/touch | `handle_touch` | 保活/EOS,返回 `{touched}`;False=已过期/不存在(gateway 回退重新 route) |
| POST /api/session/config_sync | `handle_config_sync` | 全量配置下发 `{containers, templates, scopes}`(三段式**独占**;无 containers 键的 legacy 内联载荷 → 400),委托 `ConfigStore.config_sync` |
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
     refresh/placed → 读 pod:info sse_url 返回(缺失=极端竞态被清,continue 重跑;
                     refresh 分支有 info 存活守卫,下一轮惰性回收死绑定重新放置,不构成自旋)
     scope_full     → _wait_for_capacity(场景 F)后重跑
     need_acquire   → rm_facade.acquire(扩+1)→ state.register_pod → 重跑(新 Pod 必被 first-fit 选中) }
```

`_wait_for_capacity`(场景 F 有界等待):
- `max_waiters = 2 * scope_concurrency`;过 deadline(`scope_full_timeout`)→ 504 ScopeFullTimeout。
- **入队只走 `LUA_WAITER_GATE` 原子闸门**(ZSET+deadline:先清过期成员再 ZADD 先行+超限自退);满 → 503 ScopeQueueFull 快失败。
- **等待者成员资格全程保持**(入队一次、退出删一次;中途删/加的空窗会让 max_waiters 上限漏收)。
- 订阅 `scope:{sid}:free` PubSub + ≤500ms 安全轮询双保险(兜 publish 早于 subscribe 的丢失):
  收到信号 → 返回重跑;**轮询超时无信号 → 经 re_arbitrate 就地重跑 ROUTE_PLACE**,非 scope_full 即返回——原子 admit 是唯一仲裁,丢信号后 ~0.5s 内仍能拿到空闲额度而非空等满 30s。

`_acquire_pod`:`MaxPodsReached`/`DeployFailed` → 映射 `NoPodAvailable(503, retry_after=1)`。

`touch(session_id)`:LUA_TOUCH;不存在/已过期返回 False(此时 INFO `touch missed: session=…` 留痕——gateway 回退重新 route 的排障入口;命中仅 DEBUG)。

## state.py —— SM 键表(`SMKeys`,全部含 `{session_manager}:` 前缀,带 cluster hash tag)

| 键 | 类型 | 语义 |
|---|---|---|
| `session:{sid}` | HASH | 亲和绑定:scope_id/pod_id/expiry/session_ttl |
| `session_expiry` | ZSET | 到期时间戳(sweeper 到期 pass 扫它) |
| `routing:snapshot` | STRING | **路由快照**:全部 scopes(规则/index)+ templates 的 JSON;resolve 唯一读源,config_sync 原子 SET 覆盖,缺失/损坏由首次 resolve 从 DB 重建 |
| `scope:{sid}:sessions` | SET | 活跃 session;**SCARD = scope_concurrency 闸门** |
| `scope:{sid}:pods` | ZSET | first-fit 候选(score=接入序;ZREM 即退出候选——软摘除/idle 通知都用它) |
| `scope:{sid}:pod_seq` | STRING | 单调递增,pods 的 score 来源 |
| `scope:{sid}:waiters` | ZSET | 等待队列(request_id → deadline 秒级时间戳;LUA_WAITER_GATE 原子进出;score=deadline 供闸门清理崩溃遗留——等待进程消失后名额不永久占用) |
| `scope:{sid}:free` | PubSub | 额度释放信号(EVICT 发布/route 订阅) |
| `pod:{scope}:{pod}:sessions` | SET | per-Pod 会话;**SCARD < pod_concurrency = per-Pod 容量闸门(SM 侧,RM 不强制)** |
| `pod:{scope}:{pod}:info` | HASH | sse_url / deploy_ver |
| `pod:{scope}:{pod}:idle_notified` | STR(NX EX 60) | 空 Pod 通知去重 |
| `pods:registered` | SET | 全部 `"{scope}:{pod}"`(不变量:scope:pods ⊆ pods:registered;**因此 scope_id 禁 `:`**——config_sync 入口正则校验) |
| `pods:{pod}:scopes` | SET | Pod 被哪些 scope 引用(notify_pod_dead 反查) |
| `lock:sweep` / `lock:config_sync` | STR(NX EX) | tick 级选主 / config_sync 串行化(TTL 60) |

不变量 1:一个活跃会话同时存在于四处(session HASH + scope:sessions + pod:sessions + session_expiry)——EVICT/惰性回收四处同删 + PUBLISH free。

`eval()` 统一出口带异常留痕(排障):Lua 返回空表属真异常 → WARNING(`route_place` 的 scope_full 兜底会掩盖);单次 >200ms → WARNING(`lua eval slow`,即 Redis 延迟探针);常规仅 DEBUG。

**诊断只读方法**(/visualization/* 用,无业务调用方):`session_hash(sid)`、`session_expiry_score(sid)`、`scope_session_count(sid)`(SCARD)、`routing_snapshot_raw()`(快照原文)。

## lua_scripts.py —— 7 个 Lua

| 脚本 | 一句话职责 |
|---|---|
| `LUA_ROUTE_PLACE` | route 原子核心:亲和续期(**前提 pod:info 存在**——注册已被清的绑定判死,惰性回收后走重新放置;否则 notify_pod_dead 窗口内新落的会话会无限自旋且每圈续期 expiry)→惰性回收旧绑定→scope 闸门(SCARD)→first-fit(接入序)→达 max_pods 则 scope_full / 否则 need_acquire→原子提交四处同写(复用时清 idle_notified) |
| `LUA_EVICT` | session 移除**唯一原语**(四处同删 + PUBLISH free 唤醒等待者;返回 scope/pod/remaining;幂等 noop) |
| `LUA_TOUCH` | 保活续期;已过期当场惰性 evict;ttl 就地读 session HASH(不依赖 scope:config) |
| `LUA_SWEEP_IDLE_NOTIFY` | 空 Pod 判定(SCARD==0)+ 60s NX 去重 + ZREM 退出候选(堵 reclaim 窗口内 route 直选的竞态 A) |
| `LUA_REGISTER_POD` | acquire 成功登记:三处注册(scope:pods/pod:info/pods:registered)+ 接入序 + pods:{pod}:scopes |
| `LUA_CLEANUP_POD` | notify_pod_dead 清该 (scope,pod) 全部注册(会话 evict 由调用方先行) |
| `LUA_WAITER_GATE` | 等待队列原子入队(ZREMRANGEBYSCORE 清过期 + ZADD 先行 + ZCARD 超限自退)——**禁止改回「先查后加」**(M6 验收发现的并发超收事故);ZSET+deadline 使崩溃遗留名额自清 |

约定:脚本不传 KEYS,`ARGV[1]`=键前缀,键在脚本内拼;返回扁平字符串数组(`SessionState.eval` 统一转 str)。

## config_store.py —— 配置层(scope 重构版)

**DB 表**(列名沿用 EE 兼容名,映射在 `_COLUMN_OF`):`service_config_template`(`min_idle_pods→min_idle_services`、`pod_concurrency→service_concurrency`、`pod_ttl→service_ttl`、`scope_concurrency→session_concurrency`;另有 JSON 列 `agent_env`、`sidecars`、`agent_host_path_mounts`、`agent_configmap_mounts`、`agent_pvc_mounts` + 三段式新列 `main_container_id`(string 100)/`sidecar_container_ids`(JSON)/`volumes`(JSON))、**`service_config_container`**(容器规格表,15 列:`container_id` unique ≤100、`name`/`image`/`image_pull_policy` 标量 + `ports`/`env`/`env_from`/`resources`/`volume_mounts`/`security_context`/`readiness_probe` 七个内部规范形 JSON 段落列;框架 init_table 自动建,无需手工 DDL)、`routing_scope`(`scope_id` unique / `match_index`(避 SQL 保留字 index) / `template_id` / `routing_rules` JSON)。表结构常量 `*_TABLE_DEF` 由 main 传给框架建表。旧 `routing_rule` 表已废弃(不再读写,老库残留无害)。**模板表三段式三列为后期新增:存量库须先手工 ALTER 再发版**(`ALTER TABLE service_config_template ADD COLUMN main_container_id VARCHAR(100) NULL; ADD COLUMN sidecar_container_ids JSON NULL; ADD COLUMN volumes JSON NULL;`,框架建表只 create_all 不补列;`sidecars`/挂载列/`agent_env`/`health_path` 同款义务)。

**行形态双轨(仅读路径)**(`template_from_row(row, containers)`):行有真值 `main_container_id` → 三段式形态(模板级列 + 容器行 + volumes join 水合;**任一引用容器行缺失 → WARNING + 整模板跳过**,绝不静默丢单个 sidecar——那会隐形改 deploy_ver;引用它的 scope 视为不命中落兜底);否则 → legacy 内联列路径(**读兼容:升级后重放前的存量旧行**;wire 已收紧,legacy 写路径已删,新写入一律三段式形态)。新形态写行(`row_from_template_split`)只写模板级列 + 引用列 + volumes + `agent_image: ""` 死值(该列 NOT NULL 无默认,create/update 都写,防转换残留误导诊断)。

**sidecars.py(顶层共享模块,SM 校验与 RM 渲染共用;与 spec_fields 同款先例)**:通用 sidecar 容器列表(单 JSON 列),每项一个容器规格 dict,jiuwenbox 是第一个使用者(与主 agent 容器同 Pod、共享网络命名空间,agent 经 `127.0.0.1:port` 访问)。单项 schema(规范形填满全部默认键,列表按 name 升序):`name`(必,DNS-1123 ≤63,≠ `container_name` 且 Pod 内唯一)、`image`(必,≤512)、`port`?(≠ `sse_port`/`container_port`/兄弟 sidecar;探针目标)、`env`(同 agent_env 规则 str→scalar)、`image_pull_policy`(默认 IfNotPresent)、`cpu/memory_request/limit`、`privileged`/`capabilities_add|drop`/`seccomp_unconfined`/`apparmor_unconfined`(apparmor 经 Pod annotation 表达)/`run_as_user|group`、`host_path_mounts`/`configmap_mounts`/`pvc_mounts`(见 mounts.py)、`readiness_probe_type`("tcp"|"http",设了必须有 port)/`readiness_path`(默认 /health)/`readiness_initial_delay|period|timeout_seconds`(默认 5/10/3)/`env_from`(envFrom 引用,`canonical_env_from` 规范形——**条件键:None/[] 省略**,区别于其他显式存 None 的键,为保存量 sidecar 指纹);列表 ≤8 条;**未知键 400 拒绝**(安全敏感面,拼错键不得静默吞)。**指纹不变式(★)**:`Template.sidecars` 默认 `None`、`__post_init__` 经 `normalize_sidecars` 把空列表/坏值归一为 None——`util.fingerprint` 只滤 None,以 `[]` 为默认会使全部存量模板 deploy_ver 变化(全量伪 A 类日落);"显式给默认值"与"省略键"、下发顺序重排、DB JSON 键序重排必须同指纹(规范形 + name 排序保证)。

**mounts.py(顶层共享模块,主容器与 sidecar 挂载共用)**:三种卷挂载的规范形/校验/归一,主 agent 容器经 Template 三字段(`agent_host_path_mounts`/`agent_configmap_mounts`/`agent_pvc_mounts`),sidecar 经各自子字段,同一套谓词。单项 schema(规范形填满默认键 + 按 `mount_path` 升序——挂载顺序无语义):
- hostPath:`{host_path(必,绝对), mount_path(必,绝对), read_only=False, host_path_type?∈7 枚举}`;
- ConfigMap(沿老 SDK ConfigMapMount):`{config_map_name(必,k8s 资源名), mount_path(必,绝对), sub_path?(相对路径,单 key 挂到文件), items?=[{key,path}](按 key 排序), read_only=True}`;
- PVC:`{claim_name(必,k8s 资源名), mount_path(必,绝对), read_only=False}`。
同一容器内 `mount_path` 不得重复(主容器含 NFS 挂载点一起查)→ 400。指纹不变式同 sidecars(默认 None + 空归一 None + 排序)。

**container_spec.py(SM 私有纯函数层,三段式契约的翻译层;不放顶层——顶层是 SM/RM 共享区,RM 不感知容器表)**:K8s 原生 wire(camelCase:`imagePullPolicy`/`containerPort`/`mountPath`/`periodSeconds`…;业务键 `container_id` snake)→ 内部规范形(snake,DB JSON 段落的存储形态)。要点:
- `parse_container_spec(item, where, role)`:role = 引用位置(主容器/sidecar)。主容器:ports 必有 `name="sse"`(可另有一个 `http`;无名/他名 → 400)、securityContext 只许 `runAsUser/runAsGroup`(越角色键 400)、探针恒 httpGet(`tcpSocket`/`timeoutSeconds` → 400)、缺省落定与 `Template` 默认逐项相等(period 5);sidecar:ports 至多 1 个无名端口、securityContext 全六键(`seccompProfile`/`appArmorProfile` type ∈ {Unconfined→true, RuntimeDefault→false},Localhost → 400)、探针缺省 {None, /health, 5, **10**, 3}(与 `_canonical_sidecar` 默认逐项相等)。**不可表示即拒绝**(command/args/protocol/envFrom 双 ref 等 → 400,绝不静默丢弃)。
- **卷 join(K8s spec.volumes 同构)**:模板级 `volumes`(每卷恰一源:hostPath/configMap/persistentVolumeClaim/nfs;卷名 DNS-1123 唯一)+ 容器 `volumeMounts` 按名引用;`fuse_mounts` 重建内部 fused 挂载(mounts.py 规范形,指纹承重)。源类型规则:悬挂引用/未挂载卷 → 400;`subPath` 仅 configMap;`readOnly` 缺省按内部规范(cm→**true**、hp/pvc→false);NFS 仅主容器至多一个且不支持 readOnly=true。
- 投影:`main_template_kwargs`(主容器 22 个 Template 容器级 kwargs,挂载过 `validate_agent_mounts`)与 `sidecar_wire_input`(交 `validate_sidecars` 幂等再规范化,跨字段冲突免费)——**同值必同 deploy_ver**(`test_split_contract_deploy_ver_identical_to_inline` 承重)。

**routing.py(纯函数)**:`routing_rules` 是**布尔表达式字符串**——条件 `field in|not in ('v1', 'v2')` 经 `and`/`or` 与括号任意组合;优先级 条件 > and > or;关键字大小写不敏感,字段名固定小写枚举(user_id/group_id/bot_id);值单引号串(`''` 加倍或 `\'`/`\\` 转义);空值列表 `()` → in 恒假、not_in 恒真;不支持一元 `not`;上限长度 8000、括号嵌套 32。**空 routing_rules(null/空串/纯空白)= 通配兜底**;遍历按 `(index ASC, scope_id ASC)` **first-fit**;引用模板缺失/禁用的 scope 跳过落下一个。解析器 = 词法(`_TOKEN_RE`)+ 递归下降(`_Parser`:or_expr → and_expr → primary),产物为表达式树(`MatchExpression` 叶 / `AndNode` / `OrNode`),存于 `RoutingScopeDef.rule`(与原始串 `expr` 成对,后者是 wire/DB/快照载体)。`SCOPE_ID_RE = ^[0-9A-Za-z._-]{1,128}$`(禁 `:`/`*`/空白——Redis 键与 `pods:registered` 切分依赖)。

**resolve(user_id, group_id, bot_id) → (scope_id, Template)**:读单键快照 `routing:snapshot`(1 GET;进程内按原文 memo 免重复解析)→ first-fit 匹配;快照缺失/损坏 → 从 DB 重建;无匹配 → `ConfigNotFound(503)`。

**config_sync(payload)**(场景 M,全量快照式):`{containers: [...], templates: [...], scopes: [...]}` 三段式**独占**(无 `containers` 键 → 400;旧 kind/op 协议 → 400)

```
锁外校验(纯 CPU 400):kind/op 遗迹拒绝;缺 containers 键(legacy 内联载荷,
  2026-08-31 收紧后拒绝);templates/scopes 非 list;
  模板缺 main_container_id → 400;mixed(引用键与 legacy 内联容器键并存)
  → 400,防半迁移下发静默生效;
  容器段:container_id 空/>100/同批重复/未被任何模板引用/同 id 双角色(main+sidecar)
  → 400;逐项按角色 parse_container_spec(见上);
  模板段:缺 template_id/同批重复;引用不在本批 containers;sidecar 引用重复;
  volumes join(悬挂引用/未挂载卷/多源/NFS 规则);
  int 字段严格校验(畸形 "abc" → 400,不裸抛 ValueError 成 500;
  容器级端口/runAs/探针为 K8s 严格 int——数字串不再容忍);
  策略字段下界(cc/pc/ttl ≥1、min_idle ≥0、sse_port 域)——0 值是拒绝服务配置;
  pod 落位字段校验(run_as_user/group ≥0;nodeName hostname 形态 ≤253——
  坏值 Pod 永久 Pending 挂满 ready_timeout 才暴露;空串归一 None 同未设;
  snake 双形态 node_name → 400,用 K8s 拼写 nodeName)
  scope 段同前(scope_id 字符集/index 拒 bool/引用不在本批模板集/routing_rules
  表达式串语法/重复);缺通配 scope → 仅 WARNING 放行(响应 wildcard_present:false)
lock:config_sync 串行化(忙→409 CONFIG_SYNC_BUSY,TTL 60)
→ 读 DB 旧态(containers + templates(双形态水合) + scopes)
→ diff:模板 changed_ids(_diff_class 沿用)/ 引用切换 ref_switched → affected
→ 日落中间态检查(★先于写库,拒绝时零副作用;★按版本判定:registered∖candidates
  且 deploy_ver ≠ 新版本的才是真日落残留——该集合差同时是 idle_consider 合法
  中间态,按形状判定会误拒正常空闲 Pod,min_idle≥1 时变配置面永久 409)
→ 写 DB(顺序:先 upsert 容器(模板引用永不悬挂)→ upsert/delete 模板(三段式
  形态行)→ upsert/delete scopes → GC 容器(container_id ∉ 本批 → 删;空全量
  ⇒ 容器行清空);红线:任一失败立即中止,不 SET 快照、不推送)
→ rebuild_snapshot()(DB 读回 → 原子 SET;B 类立即生效由此完成)
→ eager 预热:每个存活 scope 推 push(sid, pool_config, deploy_subset)——必须带 pod_spec
  (RM 才落 pod_spec_json/deploy_ver;autoscale 无请求预热 min_idle 的依赖)
→ 候选集版本收敛(声明式,非 one-shot):对**每个**存活 scope 把 deploy_ver ≠ 当前版本的
  Pod ZREM 出候选——不由 diff 驱动,写 DB 后中途失败/同载荷重试(diff==none)也每拍
  重算,旧版 Pod 不会无限期接新流量
→ 删除处理:目标集 = **RM 已知 scope(known_rm_scopes 回调)∪ DB 旧 scope** − 本批;
  推 push(sid, {**旧模板池参数, min_idle_pods:0}, None)——RM config 键是幻影预热的
  真源,只看 DB(删行后失忆)的话一次推送失败后 min_idle=0 永远补不上
→ 响应 {ok, templates_synced/deleted, **containers_synced/deleted**, scopes_synced/deleted, affected_scopes, wildcard_present}
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

- `Template`:template 行业务视图。派生:`max_pods = ⌈scope_concurrency/pod_concurrency⌉`(**派生值,不存储,不配置**);`deploy_subset()`=acquire 下发 RM 的 pod_spec;`deploy_ver()`=A 类指纹;`pool_config()`={min_idle_pods, max_pods, pod_ttl, pod_concurrency}(pod_concurrency 仅供 RM follower 等待室推导上限 pc-1)。A 类 pod 落位字段 `node_name`/`run_as_user`/`run_as_group`(默认 `None`:不进指纹——存量模板 deploy_ver 不因字段引入漂移;RM 渲染侧 `None`=不绑节点/不设 securityContext,走镜像默认)。`agent_env_from`(envFrom 引用,内部规范形 `[{prefix?, secret_ref|config_map_ref: {name, optional}}]`;仅三段式契约可下发,legacy 内联不接)缺省 None 被 fingerprint 滤除——引入零扰动,带值变化 = 正确 A 类日落。`__post_init__` 归一:sse_path/health_path 补前导 `/`(缺失会拼出 `http://ip:8080api/...` 非法 URL→健康 Pod 被探死循环)、sidecars/mounts 空归一 None、agent_env_from 空列表归一 None(指纹不变式)。
- scope 定义(`RoutingScopeDef`/表达式树)在 `routing.py`,不再有 ScopeConfig(快照取代 per-scope 缓存)。

## 高频踩点

- 改键名/Lua:HLD §5 键表、`state.py`、SM 详细设计三处同步。
- 等待队列入队只准走 `LUA_WAITER_GATE`;「先查后加」已被真环境验收证伪。
- config_sync 全局串行锁——e2e 脚本播种配置必须串行,并发即 409。
- scope_id 禁 `:`(Redis 键与 `pods:registered` 的 `{scope}:{pod}` 切分依赖)——入口 `SCOPE_ID_RE` 强校验,新增拼键代码时勿破坏该前提。
- config_sync 的 RM 推送**必须带 pod_spec**(eager 预热依赖 pod_spec_json;不带则 autoscale `skip_no_spec`,无请求 scope 永远不预热)。
- 经多副本 LB 跑冒烟须设 `AGENT_RUNTIME_SCOPE_FULL_TIMEOUT`(显著小于 session_ttl),排查实录见 `e2e-test-cases.md` §8.1。
