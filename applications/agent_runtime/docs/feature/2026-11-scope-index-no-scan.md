# RM scope 枚举去全库 SCAN(索引集合)+ tick 租约/开火判据修正

- 日期:2026-11-24
- 里程碑 / commit:维护期(现网事故修复)
- 涉及模块:resource_manager / session_manager / service-core / 框架 periodic / 测试 / 文档

## 背景与动机

现网(OpenShift,`no_lua` 模式,公用 Redis Enterprise)报 `config_sync` 耗时 100 多秒。
实测日志:

```
config_sync ok: result={...} duration_ms=188893.3
tick done: job=rm_autoscale ok=False aborted=True duration_ms=65420.8  ticks=548
tick done: job=rm_autoscale ok=True              duration_ms=184734.1 ticks=549
tick done: job=rm_reclaim   timed_out=True       duration_ms=60053.9  ticks=1039
tick done: job=rm_reconcile ok=True              duration_ms=201703.8 ticks=233
tick done: job=sys_sample   timed_out=True       duration_ms=30000.5  ticks=875
tick done: job=sys_eval     timed_out=True       duration_ms=120000.7 ticks=172
```

定位:六个入口共用 `ResourceState.known_scope_ids()`,实现是
`SCAN MATCH {prefix}resource:scope:*:config`。**`SCAN MATCH` 的代价 ∝ 整库键数而非
命中数**(Redis 无前缀索引,MATCH 是逐键后置过滤),客户现场在 Pod 内实测:

```
DBSIZE = 1128335      命中 = 6      耗时 = 390.26s
```

即 113 万键 ÷ count=200 = **5642 次网络往返**,其中 5641 次返回空结果集。
各业务用 `key_prefix` 隔离**只隔离命名,不隔离检索代价**,且 DBSIZE 由别的业务增长决定。

放大成事故的三件事:

1. `rm_autoscale` / `rm_reclaim` 间隔 **1 秒**,每拍都从游标 0 全库扫一遍;
2. 超时即被掐断、下一拍重新从游标 0 开始 → `rm_reclaim`(1036→1039 拍连续超时)、
   `sys_sample` **从未跑完过一次**,RM 回收与评估采样实际完全停摆,而这些循环又永久
   占着连接猛打 Redis,把普通命令延迟抬高 ~100 倍(`route` 24ms → 2886ms);
3. `config_sync` 扩散③ 要调同一个枚举,于是成为第 5~6 条并发扫描,耗时 188s;期间全程
   持 `lock:config_sync`,manager 的重试全部 409 `CONFIG_SYNC_BUSY`
   (日志实锤 `request_id=manager-a14ca485...`),用户侧表现为"改 Pod 模板超时"。

连带暴露三个韧性缺陷:
- `autoscale`/`reclaim` 的业务执行锁 TTL **写死 2s**,而单拍上限 60/370s;`watch`/
  `reconcile` 同类(15/60s vs 300s)——锁早过期,另一副本可并发进同一拍;
- 框架选主锁 TTL **恒等于调度间隔**,1s 任务拿 1s 租约、0.33s 续一次,无 Lua 模式的 CAS
  续期要 3~4 个往返,一次抖动就 `renew_not_owner` → `on_tick aborted`(日志实锤);
- 1Hz 任务每拍 `missed fire, skip tick`:醒来后那次 Redis `TIME` 对表的往返耗时被算进
  "睡过头"判据,RTT > 集合窗口(80ms)时整拍丢弃 → `sm_sweep` 的到期驱逐长期不执行。

不改的话:公用 Redis 环境下 RM 控制面常态不可用,且 DBSIZE 随别的业务增长只会更糟。

## 方案

1. **scope 枚举改索引集合,常态零扫描**(根治):新增 `resource:scopes:all` SET,
   `known_scope_ids()` = 一次 SMEMBERS,与库大小无关;声明表同理
   (`resource:scopes:declared`)。
2. **全库 SCAN 只保留回填/诊断路径**:`scan_scope_ids()` / `scan_declared_scope_ids()`,
   page size 200 → 2000;启动期 `reindex_scopes()` 后台跑一次(完成标记 + 互斥租约,
   一个 Redis 只付一次),存量库零停机迁移。索引漂移由 config_sync 每次对全部存量
   scope 重新 SADD 自愈(写点与权威源一致)。
3. **僵尸 scope 回收**:`scope:config` 原先无 TTL 也**没有任何删除点**——被删 scope 的
   池参数永久留库(现场 6 个 scope 里 5 个是这种僵尸,每次 sync 重推、每拍遍历)。
   config_sync 扩散③ 增推 `retired=1`,RM `reconcile` 在该 scope 排空干净后
   `forget_scope`(删 config + 出索引)。
   - **否决**「用 `min_idle==0` 当判据」:按需供给的**生效** scope 合法地 min_idle=0 且
     静息零 Pod,误删其 config 会让下次 route 的 acquire 拿 `no_config` 冷启失败。
   - **否决**「把禁用/过期也算退役」:它们仍在载荷里(可续期/可重新启用),恒写
     `retired=0`;只有"从载荷消失"(含 `enabled=false` 的缺席)才退役。
4. **锁 TTL 与单拍上限同源**:sweeper 导出 `*_TICK_TIMEOUT`,`main.TICK_TIMEOUTS` 取之,
   业务锁 TTL = 上限 + 10s。代价只是"进程被 SIGKILL 后该 job 最多停一个 TTL"
   (runner 每拍结束必 release,常态交接不受影响)。
5. **框架 periodic**:选主锁 TTL 取 `max(interval, 10s)` 下限(可用 `lock_ttl_sec=`
   显式覆盖);续期改固定节拍 + 瞬时错误重试一次(`not owner` 是确定性结论,不重试);
   `_missed_fire` 扣除对表往返耗时。
6. **运维止血(不发版)**:把 `AGENT_RUNTIME_{AUTOSCALE,RECLAIM}_INTERVAL` 从 1 抬到
   30~120、`RECONCILE_INTERVAL` 1800、`EVAL_*_INTERVAL` 3600,可把并发扫描压到近乎 0。
   长期仍建议给 agent_runtime 独占 Redis database(Redis Enterprise 不支持 `SELECT`
   到别的逻辑库,需 DBA 另开端点;割接会清空 `pods:all` 等状态,现存 Pod 需用
   `POST /api/session/cleanup` 按 label 收一遍,选低峰)——但它**不替代**本次修复:
   独占库里我们自己的键数同样会随会话/Pod 增长,10 万键时一次扫描仍要 ~1.25s。

## 实现

- `resource_manager/state.py`:新增 `RMKeys.scopes_index/declared_index/
  scopes_reindexed/scopes_reindex_lease`;`save_scope_config`、`bump_generation`
  改同槽单事务(SADD 索引 + HSET/HINCRBY,一次往返);新增 `forget_scope`、
  `reindex_scopes(force=)`、`scan_scope_ids`、`scan_declared_scope_ids`;
  `known_scope_ids`/`declared_scope_ids` 改 SMEMBERS;`add_declared`/
  `remove_declared`(删空即出索引)/`drop_declared` 维护声明索引。
  Lua 侧只 **读** `:config`(HGET),写点仍只有上述两个 → 索引无旁路。
- `resource_manager/orchestrator.py`:`update_pool_config` mapping 增 `retired`
  (载荷真值 → 1,否则恒 0)。
- `resource_manager/sweeper.py`:常量改为 `*_TICK_TIMEOUT` + `*_LOCK_TTL`(派生);
  autoscale/reclaim 的 `try_lock` 用新 TTL;`reconcile_once` 增 ⓪.2 步
  `_sweep_retired_scopes()`(retired=1 且候选集/占位/idle/声明表全空才 forget,
  per-scope 隔离),tick 汇总日志增 `forgotten=`。
- `session_manager/config_store.py`:扩散③ 的 pool 增 `retired: 1`(扩散①/refresh 不带)。
- `main.py`:`TICK_TIMEOUTS` 的四个 rm_\* 取 sweeper 常量;`start()` 起后台任务
  `_reindex_rm_scopes()`(按 `AGENT_RUNTIME_RM_SCOPE_REINDEX`;**不 await**,否则 390s
  的扫描会拖死就绪探针),`stop()` 对称 cancel。
- `config.py`:新增 `rm_scope_reindex` + `_env_scope_reindex`(auto|off|force,非法值 raise)。
- 框架 `service/.../periodic/`:`factory` 增 `lock_ttl_sec` 参数与 `_LOCK_TTL_FLOOR_SEC=10`
  下限;`system_context.create_single_leader_job` 透传;`lock._renew_loop` 固定节拍 +
  重试一次;`runner._missed_fire(..., slack=)` 扣除对表耗时,missed fire 日志增
  `clock_sync_ms=`。

红线遵守:跨模块仍只走 Facade(config_sync 经 `push_pool_config` 回调传 `retired`,
未新增回调);`forget_scope` 不碰 SM 键;索引与 config 同槽(`{resource_manager}` tag)
故 cluster 下事务成立;占位/候选集清理语义未动。

## 验证

- 单测:`667 → 672 passed`(9 个 failed 是改前即红的既有用例:容器 canonical golden ×6、
  config_store sidecar ×2、link_profile ×1,见外层仓库 CLAUDE.md 清单)。新增/改写:
  - `test_rm_state.py`:`test_known_scope_ids_reads_index_without_scanning`
    (把 `scan_iter` 换成会抛断言的桩 —— **枚举一旦退化为扫库即红**)、
    `test_reindex_scopes_seeds_index_once_then_skips`(回填一次 / 标记后 -1 / force 重扫)、
    `test_forget_scope_drops_config_and_index`、
    `test_declared_index_tracks_add_remove_drop`;
    原 cluster dict 游标分页回归锚(issue #4581)移到 `scan_scope_ids`。
  - `test_rm_business.py`:`test_retired_scope_forgotten_once_drained`(有 Pod 不动、
    排空后遗忘)、`test_active_scope_with_zero_min_idle_is_never_forgotten`
    (**红线反例**:min_idle=0 的生效 scope 删不得,删完还要能冷启)、
    `test_retired_flag_cleared_when_scope_revived`、
    `test_tick_lock_ttls_cover_tick_timeouts`(锁 TTL > 单拍上限且与 TICK_TIMEOUTS 同源)。
  - `test_config_store.py`:`test_deleted_scope_gets_min_idle_zero` 增 `retired==1` 断言;
    新增 `test_expired_scope_push_is_not_marked_retired`。
  - 框架新增 `service/tests/unit_tests/test_periodic_lease_and_missed_fire.py`(6 项):
    锁 TTL 下限/跟随长间隔/显式覆盖、续期抖动重试一次不判失锁、连续失败仍判失锁、
    对表耗时不算睡过头(含改前行为的回归锚)、真睡过头照旧跳拍。
- 现网实测(修复前基线,客户 Pod 内):`DBSIZE=1128335`,一次全库 SCAN **390.26s** 命中 6
  (= 5642 次往返 × ~69ms)。
- **真 Redis 等规模复现**(本机 docker redis 7-alpine 灌 112.8 万键,与现网同量级;
  `INFO commandstats` 计往返):

  | 路径 | 往返 | 耗时(本机 RTT≈0.02ms) | 折算现网 69ms/次 |
  |---|---|---|---|
  | `scan_scope_ids` count=200(改前) | 5642 | — | ≈389s(现场实测 390.26s) |
  | `scan_scope_ids` count=2000(现仅回填走) | 564 SCAN | 0.312s | ≈39s |
  | `known_scope_ids` 索引(改后) | **1 SMEMBERS** | **0.091ms** | ≈69ms |

  枚举加速 **3446x**,两条路径返回结果逐一致;`reindex_scopes` 二次调用返回 -1(跳过)。
- **真环境服务级验证**(新代码 + 真 Redis(112.8 万键,含直写的 6 个"升级前"存量
  scope:config + 1 个声明表)+ 真 mariadb 11.8 + chenhui 同款配置
  `DEPLOY_MODE=routing` `REDIS_LUA=off`;**未给 kubeconfig → 零集群写**):
  - 启动期回填按设计工作且**不阻塞就绪**:`Uvicorn running` 先出,随后
    `rm scope index rebuilt from full scan: scopes=6 declared=1 duration_ms=566`;
  - 稳态 60s 的 `cmdstat`:**`scan +0`**、`smembers +522`、`watch +508`、`exec +888`
    ——后台任务已彻底不扫库;
  - 7 个 job 计数器(`/visualization/overview`,运行 256s 后):1s 的 sm_sweep /
    rm_autoscale / rm_reclaim **ticks=256 全 ok**,timeout/abort/error 全 0,
    单拍 0.42~2.9ms;reconcile 7.04ms;**`missed fire` 0 条、`lock lost` 0 条**。
    对照现网同配置:reclaim/sys_sample 连续 1000+ 拍一次没跑完、autoscale 每
    ~3 分钟才成一次、sm_sweep 每拍 missed fire。
  - 注:本机 RTT(~0.02ms)远低于现网,**绝对 ms 不可直接对比**;可迁移的是与 RTT
    无关的结构性事实——往返数 5642→1、scan 调用 0、每拍必执行。
- **待补**:HLD 场景 A–L 冒烟 `./scripts/integration_smoke.sh`(含真镜像门禁)与多副本
  e2e 未跑——需要一个可建删 Pod 的命名空间(共享集群,建 ns 需授权)且需把本改动构成
  镜像分发到节点;发版前必须按 CLAUDE.md 的发布门禁补齐。

## 影响面

- 文档同步:`docs/spec/resource-manager.md`(键表 + facade 契约 + 四个 job 的锁 TTL +
  reconcile ⓪.2 步)、`docs/spec/session-manager.md`(扩散①/③ 的 retired 语义)、
  `docs/spec/service-core.md`(TICK_TIMEOUTS 同源、框架租约纪律、新 env)。
- 兼容性:**键名/语义不变,无 DB schema 变更,无需手工 ALTER**。存量 Redis 首次启动
  会后台扫一次库建索引(公用大库上数分钟,不阻断服务);若不希望付这次扫描,设
  `AGENT_RUNTIME_RM_SCOPE_REINDEX=off` —— 但在首次 config_sync 之前 autoscale/reclaim
  会看不到存量 scope。
- 行为变化:1s 间隔任务的选主租约从 1s 抬到 10s → 副本被 SIGKILL 后该 job 的接管时延
  由 ~1s 变 ~10s(幂等任务,可接受);退役 scope 的 config 会被删除,`/visualization`
  里不再长期看到陈年僵尸 scope。
- 遗留:① HLD 场景 A–L 冒烟/多副本 e2e 待跑(需可建删 Pod 的命名空间 + 镜像分发;
  索引与 job 健康度已在真 Redis/真 mariadb 上验证,见「验证」);② `sm_sweep` 侧的 `registered_pods()` 是 SET 枚举
  (不扫库)但规模增长后同样值得上限保护;③ 独占 Redis 的割接方案待排期。
