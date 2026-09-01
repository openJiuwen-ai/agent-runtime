# 健壮性加固:三路审阅后封堵 14 项系统性短板(孤儿 Pod×2/无事务写库/零超时/故障期 500)

- 日期:2026-09-01
- commit:C1 `3b0ccf6a` / C2 `2affb7dc` / C3 `f45e3c03` / C4 `587a60f8` / C5 `c9ced5d3` / C6 `f3df7a7c` / C7 `f27846e6` / C8 `65cbd558`
- 涉及模块:resource_manager / session_manager / service-core / 测试 / 文档

## 背景与动机

维护期对服务做了三路并行健壮性审阅(服务核心+SM / RM / 横切面),P1 逐条人工复核属实。总体判断:已知故障模式防御扎实(原子 Lua、占位三清、tick 兜底、启动 fail-fast、415 用例),但三类系统性短板:

1. **"成功与失败的交界处"**:物理 Pod 已建但控制面写失败 → 孤儿 ×2 条路径;config_sync 六步独立提交中途失败 → 半同步 DB,重启 `ensure_snapshot()` 固化混合态成路由快照;
2. **时间上限缺失**:K8s 调用全程零超时(API server 挂起 = route 无限悬挂)、串行化锁 TTL 60s 无续期、route 的 need_acquire 分支无总时限(理论可达 max_pods×ready_timeout);
3. **基础设施异常未纳入错误契约**:Redis/DB 抖动一律 internal 500(不可重试),幂等缓存写失败甚至吞掉已成功的路由响应;故障注入测试整体缺位。

**范围与需求方确认记录**:修 6 条 P1 + 8 条高性价比 P2;契约变更只做 route 总时限一项——/healthz 依赖感知(失联摘流)与 cleanup/config_sync 端点防护**明确不做**,记录为已知限制另行排期;每条修复配故障注入用例;P3 留档不动。

## 方案(14 条,分 8 批提交)

| # | 批 | 内容 | 关键决策 |
|---|---|---|---|
| 1+13 | C1 | K8s 四调用补 `_request_timeout`(CREATE 30/READ 10/LIST 15/DELETE 60,复用既有未引用常量);start() 锁内双检、close() 锁内摘引用锁外收尾、调用点快照 None→DeployFailed | 顺手修 close() 漏置 `_loaded=False`(get_pod 在 close 后 AttributeError 的根因);close 后惰性调用经 start() 自愈重建 |
| 2+3 | C2 | REGISTER 失败用已到手 `info` 兜底删孤儿(两级推导);`_purge_and_notify` delete 非 404 失败本拍放弃(下拍重试) | 互为镜像的两条泄漏路径都发生在"物理操作成功、控制面写失败"的接缝;对账只做 Redis→K8s 单向,K8s 侧孤儿无人认领 |
| 10+11 | C3 | no_config 有界(5 次+0.2s 退避);watch/reconcile 逐 Pod、autoscale/reclaim 逐 scope 异常隔离 | `all_pod_ids`/`known_scope_ids` 是 sorted 确定序——单点异常上抛会使排序靠后者永远得不到处理(半死 Pod 探测盲区) |
| 4+5 | C4 | config_sync 写库**单事务**(`_db_session`+`_upsert_row_tx/_delete_tx`,语义等同框架 transaction());锁 token 改 uuid + 看门狗续期(TTL//3,持有上限 600s) | **单事务而非步骤重排幂等化**(`ensure_snapshot()` 每次启动无条件重建,重排关不掉混合态固化窗口——被否方案);`CONFIG_SYNC_LOCK_TTL` 语义从"处理超时上限"变"基线+续期+上限";unlock 失败只记日志不吞成功 |
| 7+8 | C5 | `_INFRA_EXCEPTIONS`(redis 连接级 + sqlalchemy 连接级)→ 503 `STATE_UNAVAILABLE`(retry_after=1);`guard.succeed` 失败不吞成功响应 | **app 层翻译,不动共享框架**(框架同服务 echo 应用);**有意收窄**——redis ResponseError(Lua 逻辑错)/ProgrammingError(schema 错)仍 500,那是该暴露的真 bug;防御式 import 兜传递依赖缺失 |
| 9 | C6 | LUA_TOUCH/ROUTE_PLACE 补 EVICT 式残骸自卫(guard 集合 scope_id/pod_id/**expiry**) | 复核发现 expiry 缺失同样触发 "compare nil with number",只修 scope/pod 是修一半;ROUTE_PLACE 自清后**落穿**全新放置(不 return rubble);不改键名,cluster 同槽不受影响 |
| 6+12 | C7 | rm_sysctx 显式 `_owns_db/_owns_redis=False`;stop() 逐步兜底对称化 | 框架缺省"传了 db 即拥有"→ 二次 connect(旧池泄漏)+ dispose 共享 engine;非拥有态保留双前缀 readiness 探活 |
| 14 | C8 | route 主循环每圈**推导式总预算**校验(`scope_full_timeout + template.ready_timeout + ROUTE_BUDGET_MARGIN_SEC(10s)`);`_wait_for_capacity` finally 三连 await 逐级保护 | **契约变更(已获批:need_acquire 加总时限)**。首版直接复用 `scope_full_timeout` 当总预算,**真环境门禁实测否决**(88/100:部署模板 8s 是队列语义的值,真镜像冷部署 15-25s → 首个请求必 504)——改为推导式:总预算封 need_acquire 无上界循环而非冷启动本身,排队 deadline 语义不变、不新增配置项;超预算 504 后 RM acquire 照常完成落 idem 缓存,同 request_id 重试幂等回放 |

**C8 冷启动相容性论证**:超预算 504 后 RM `acquire` 照常完成并落 idem 缓存(TTL 60s),gateway 同 request_id 重试即幂等回放结果——不浪费已完成的部署。

## 实现

- 代码:`resource_manager/{k8s,orchestrator,sweeper}.py`、`session_manager/{config_store,state,lua_scripts,orchestrator,handlers}.py`、`errors.py`、`main.py`
- 红线保持:config_sync 事务先于快照/推送(失败零 Redis 副作用);deploy 失败路径三清纪律不变;占位清理含取消路径不变
- 新测试文件:`test_k8s_io_timeouts.py`、`test_infra_faults.py`、`test_main_lifecycle.py`;既有 `test_db_write_failure_skips_snapshot_and_push` 注入点从 `db_handler.update` 迁到 session 级(事务化后原注入点假绿——同提交迁移)
- FakeK8s 新旋钮:`delete_failures`(非 404 形态)

## 验证

- 单测:pytest **415 → 437**(全部通过;每条修复配 1 个故障注入用例,含:register 失败不留孤儿、delete 失败不 PURGE 下拍重试、整批回滚、看门狗续期/并发 409/uuid token、Redis 断连 503 信封、幂等写失败不吞成功、残骸落穿放置、no_config 有界、逐 Pod/逐 scope 隔离、connect 各恰一次、rm stop 抛错不断链、route 总预算、finally 不吞原始异常)
- 真环境:发布门禁冒烟(integration_smoke.sh 三件套)待随本批发版执行(见"影响面")
- Redis Cluster:C6 只改 Lua 逻辑不动键名/KEYS/ARGV,hash tag 同槽不受影响;如需复核跑 `scripts/verify_redis_cluster.py`

## 影响面

同步文档:spec×3(session-manager/resource-manager/service-core)+ HLD(§3.1 错误码表加 `STATE_UNAVAILABLE`、过载参数表 `scope_full_timeout` 语义)+ design×2(SM 两 Lua 全文、RM 清理顺序)+ e2e-test-cases(错误码表)。CLAUDE.md 用例计数 414→437。

**遗留开放问题(本轮明确不做)**:
- /healthz 依赖感知(Redis/DB 失联摘流)与 cleanup/config_sync 端点防护——涉及部署形态与安全边界,另行排期;
- `_purge_and_notify` 的 notify 链:PURGE 成功 + notify 失败时死绑定残留到会话自然到期(reconcile 遍历源看不到已 PURGE 的 Pod);闭环需 notify 前移到 PURGE 之前(幂等性允许),属行为重排;
- P3 清单约 40 条(幂等闸静默放行、eval 空表兜底成 scope_full、list 静默截断、等待队列深度/饱和度指标缺口、故障注入测试其余 9 类缺失场景、`_probe_gap_warned` 键不匹配、probe_health 连接池、锁/超时常量统一推导等)。
