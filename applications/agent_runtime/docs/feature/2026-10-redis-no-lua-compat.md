# Redis 无 Lua(EVAL 被禁)兼容模式:AGENT_RUNTIME_REDIS_LUA=off 时 WATCH/MULTI/EXEC 全链等价运行

- 日期:2026-10-10
- 涉及模块:session_manager / resource_manager / service-core(框架 openjiuwen_runtime)/ a2a_service / 测试 / 文档

## 背景与动机

部分客户环境的**公用 Redis** 因安全限制禁用脚本命令(`rename-command EVAL ""` 或 ACL 撤权),而 runtime 核心逻辑(路由三重闸门/亲和持有/槽位搬移/idle 暖池/选主)全部依赖 EVAL,在此类环境完全不可用。方向已确认:**仅代码侧解决**(不做部署侧专用 Redis);一致性底线 = **并发竞态强一致 + 崩溃窗口周期自愈**(不追求与 Lua 在进程崩溃时序上严格等价)。

关键可行性前提(既有设计的红利):SM/RM/job 键域已 hash tag 同槽 → **WATCH/MULTI/EXEC 在 Redis Cluster 下与 EVAL 同等可用**,不改任何键名;redis-py 7.1.0 ClusterPipeline 已支持 `transaction=True` 的同槽事务(步 0 真环境冒烟实测:多键 WATCH/EXEC 响应序/WatchError/跨槽客户端拒绝,10/10)。

## 方案

- **分派点 = State 门面 eval() 出口**:`SessionState/ResourceState` 构造参数 `use_lua`(None→进程级默认→lua);off 时分派到 `session_manager/no_lua.py` / `resource_manager/no_lua.py`(与 Lua 脚本一一对应,同参数、同扁平返回规约)——**业务层(orchestrator/sweeper/facade/handlers)零改动,Lua 默认路径字节不变**。
- **开关仅显式 `AGENT_RUNTIME_REDIS_LUA=on|off`,默认 on,非法值启动 raise**(确认过:不要 auto 探测)。off 不发任何 EVAL 探测命令(禁用环境可能审计命令);on 不做启动校验保持现行为(误配 on 到禁 EVAL 环境 → 首个 Redis 操作自然报错,与今天相同)。
- **强一致范式**:read-decide-write 一律「预读 → WATCH 决策依赖键 → pipe 复读关键前提 → MULTI 写集 → EXEC」;WatchError 全抖动退避重试(热 12×[0,120ms)/温冷 5 次),耗尽上抛走既有 503 STATE_UNAVAILABLE。纯写集脚本只 MULTI 不 WATCH。逐脚本 WATCH 集与偏差 → design 两篇的「无 Lua 模式语义映射表」。
- **框架层**(`service/openjiuwen_runtime/service/context/redis_compat.py`):EVAL 禁用错误分类(rename-command 的 unknown command / ACL 的 noperm 两族)+ 按 client 的进程级 memo + `watch_cas`(自 `RedisLockBackend._watch_cas` 泛化上提)+ `eval_or_watch` 惰性降级。TickLock 续期/释放、SingleLeaderCoordinator 报名(纯 MULTI SADD+EXPIRE)与抽签(WATCH winner/candidates 同槽双键)、RedisLockBackend 全部走该降级——**框架独立使用方(如 echo)零配置可用**;off 模式 main.start() 主动 memo,免首拍撞墙。a2a_service 不 import 框架,`release_lock` 方法内同款惰性降级(独立小改)。
- **唯一新增崩溃窗口**:RM release 的 EXEC(SADD idle) 与 SET idle_since 之间进程死 → idle 成员无计时(reclaim 永不到龄)——**rm_reconcile 新增步 0 检测项**:`set_idle_since_if_absent` 补 SET NX(不刷新既有计时),对齐冒烟阶段 11b「idle_since 存在」不变量。SET 不入 MULTI 是为保「重放不刷新计时」幂等。
- 被否方案:**部署侧专用 Redis**(用户决策:仅代码侧);**SET NX 占位后补偿回滚闸门**(崩溃窗口内可能永久超卖,违背强一致底线);**Redis Functions**(同属脚本命令族,大概率一并被禁)。

## 实现

- 新增:`session_manager/no_lua.py`(7 脚本)/ `resource_manager/no_lua.py`(7 脚本)/ `redis_lua_mode.py`(进程级默认)/ 框架 `context/redis_compat.py` / `scripts/verify_redis_no_lua.py`。
- 修改:两个 `state.py`(构造参数 + eval 分派 + 内联锁走 watch_cas)/ `config.py`(redis_lua 字段)/ `main.py`(start() 置默认与 memo)/ RM `sweeper.py`(reconcile 步 0)/ 框架 `periodic/lock.py` + `periodic/coordinator/single_leader.py` + `locks/backends/redis.py` / a2a `common/redis_client.py`。
- **两个真环境踩坑实录(单测测不出,已固化到代码注释与测试)**:
  1. **ClusterPipeline 上 `pipe.exists` pre-MULTI 不立即执行**(属 DELETE/EXISTS/TOUCH/UNLINK 可拆分命令族,返回 pipeline 对象;单实例/fakeredis 返回 coroutine——静默分歧)→ 亲和判活恒假误回收绑定;判活一律改 HLEN(HASH)/GET(STRING)。
  2. **route 放置的乐观锁惊群**:每次放置提交必 SADD scope:sessions 踢掉全部竞争者 WATCH,真 cluster 40 路并发实测 349 次 abort/18 任务 12 次重试全耗尽(协同调度下输家恒定队尾,纯加宽退避无效)→ 放置段加**进程内 per-scope 串行锁**,WATCH 只防跨副本竞争(修复后 0 abort/0.15s/恰收满)。

## 验证

- 单测:agent_runtime 508→632(新增双模式套件 `test_sm_state_no_lua.py` 17/`test_rm_state_no_lua.py` 16,`parametrize(use_lua=[lua,no-lua])` 同断言双跑;`test_redis_lua_mode.py` 16 开关/分派;`test_no_lua_mode.py` 5 组件全链)——fakeredis 原生支持 WATCH/MULTI,**无 lupa 的 CI 也能跑满 no-lua 参数**。框架 service 231→234(`test_redis_compat.py`:分类/watch_cas/惰性降级+memo/TickLock/选主)。
- 真环境:`scripts/verify_redis_no_lua.py` **单实例 19/19 + 禁 EVAL 三主 cluster 19/19**(环境自证 EVAL 必报错防假绿;覆盖框架锁/选主/SM 全分支含 40 路并发不超卖/RM 闸门/烙印/drain surge/reconcile 自愈);`verify_redis_cluster.py` Lua 默认路径回归 29/29(字节不变红线)。
- **真环境发布门禁(2026-10-10,镜像 agent-runtime:nolua-20261010a,AgentServer=jiuwenclaw-core-amd64:0.0.40s)**:两模式各跑全量——K8s 三副本部署 + `e2e_multi_replica` **32/32** + 真镜像三件套门禁(integration_smoke `--with-sidecar --with-mounts`) **127/127**;load_test 60s 双模式对比:on=372.5rps/p50 7.8ms/**p99 12.7ms**,off=388.6rps/p50 8.2ms/**p99 14.7ms**——RTT 放大实测 ≈+2ms(p99),巡检 6/6 全过、ERROR=0。
- 实测数据:40 路并发 route(scope_cc=20/pod_cc=5)——修复惊群前 placed=20/EXHAUSTED=18/349 abort/2.60s;修复后 placed=20/full=20/**0 abort/0.15s**/不超卖,Lua 与 no-Lua 一致。

## 影响面

- 文档同步:design 两篇新增「无 Lua 模式语义映射表」(§15/§14);spec 三篇(config 表 redis_lua 行、no_lua 节、reconcile 步 0);CLAUDE.md 测试/环境段。
- 配置与兼容:默认 on 零变化;off 为新能力,存量部署无需动作。禁 EVAL 环境部署模板可加 `AGENT_RUNTIME_REDIS_LUA=off`(部署模板已带渲染项,example/env 默认 on)。
- 真环境门禁注意(2026-10-10 实录):①统一镜像 jiuwenclaw-core-amd64 的 start.sh 要求 `ROLE=agentserver`,门禁 `--agent-env` 三件套须追加该键(缺省 CrashLoopBackOff);②宿主冒烟 MySQL(30000/agent_runtime)存量 schema 漂移,已手工 ALTER 补 5 列(fs_group/command/args/data×2)+ 11 个 legacy NOT NULL 列补默认值。
- 遗留:a2a 测试待其 CI 环境跑(本机 venv 缺 a2a protobuf SDK,预置限制)。
