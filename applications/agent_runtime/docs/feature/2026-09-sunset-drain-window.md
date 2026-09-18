# 日落优雅排空窗口:带会话 Pod 保留 session_ttl 再回收 + max_pods surge 余量

- 日期:2026-09-17
- 里程碑 / commit:M 维护期(排布于同日 follower_reuse 泄漏修复之后)
- 涉及模块:resource_manager(orchestrator / sweeper / state / lua_scripts)、session_manager(models / config_store 注释)、e2e 脚本兼容性确认

## 背景与动机

2026-09-17 wangchang 联调实录(两份日志):测试在**会话进行中**触发配置刷新,正在服务对话的 Pod 在日落后 2~5s 被 reclaim 回收,会话被硬切(`notify_pod_dead` invalidate),下一条消息重放置并付全额冷启动(16~22s,min_idle=0 无热备)。排查确认这是 2026-09-15 的显式决策("含扩散后仍带会话的 Pod:会话被硬切重放置,决策接受",彼时动机是小 max_pods 下的容量收敛)。本篇按测试反馈推翻该取舍的一半:**保留收敛性,不再硬切活跃会话**。

关键前提(排查中确认):SM 的 route 亲和分支与 touch **只查 `pod:info` 存在、不查候选集**;日落只 ZREM 候选集不删 info → 排空窗口内已绑定会话天然继续服务,无需改 SM。

## 方案

确认过的决策(用户拍板):

1. **drain 超时 = SESSION_TTL**(非新配置项):日落时刻 + 模板 session_ttl = 排空截止。窗口内 stale Pod 不回收;过窗即收(仍不蹲 pod_ttl——零复用价值不变)。持续 touch 的活跃会话在窗口内全程连续,过窗仍有会话才硬切(有界优雅)。
2. **surge 余量暂时写死为 1**(`DRAIN_SURGE_MARGIN`):排空纪元活跃期间容量上限 max_pods+1(acquire/placeholder 两个 Lua 闸门 + autoscale Python 闸门三处同步)。多 Pod 日落时也只让一个补位先行(已知局限)。
3. **闸门语义不变**:排空窗口内同类再触发维持 409 CONFIG_SYNC_BUSY(refresh 闸门看代次、sync 守卫看版本,各自只对自家排空 Pod 敏感);等待窗从 ~30s 拉长到 ≤ session_ttl + tick,有界(reclaim 必收)。
4. **A 类 sync(保存模板)与 refresh 统一进排空窗口**——两类"落后"(ver/gen)一视同仁,堵住另一个"对话中杀 Pod"入口。

被否备选:

- **允许排空期内再刷=放弃排空强切**:要多一条终止路径且多代补位竞争 surge,否;维持 409 等排空。
- **surge 常开(+1 恒生效)**:永久突破 max_pods 契约,否;仅排空纪元期间生效。

## 实现

- **纪元键** `resource:scope:{sid}:drain_until`(STR,值=截止秒级时间戳,EX = window+60s 崩溃兜底)。存在 ⟺ 排空纪元活跃。
- **唯一戳记点 = `update_pool_config`**(refresh 的 ②push 与 A 类 sync 扩散 push 都经过):推送后注册表存在 ver/gen 落后 Pod 且纪元未活跃 → SET(首因下发定窗;**纪元已活跃不重戳**——B 类纯池参数下发不延长;排空中的 refresh 本就被闸门拦住)。判据 `state.has_lagged_pods()`(读注册表不读候选集,与软摘除顺序无关)。session_ttl 随 pool_config 下发进 scope:config(`Template.pool_config()` 增字段)。
- **三处容量闸门同步 +SURGE_MARGIN=1**:`LUA_ACQUIRE`、`LUA_PLACEHOLDER`(EXISTS drain_until → cap+1,Lua 内硬编码并与 Python 常量注释互指)、`sweeper._autoscale_decide`。
- **reclaim 排空闸门**:stale 回收条件 = `drain 键缺失(兜底立即,保收敛)或 now ≥ 截止`;"reclaim pending" 日志 due/pending 计数修正并附 `drain_wait=`;**排空收尾**——本拍收过 stale 且 `has_lagged_pods()` 为假 → DEL drain_until 精确终止 surge(不留 TTL 悬挂的超额窗口)。
- SM 侧零代码改动(亲和/touch 语义本就兼容);config_store 闸门注释更新;sweeper 旧"免老化即刻回收(2026-09-15 决策接受)"注释块改写。
- e2e 冒烟(e2e_hld_acceptance.py 阶段 10/10b/5)确认兼容:亲和/重建/ZREM 断言不受影响,老代排空态断言本就容忍"已被自然回收"。

## 验证

- 单测 542 passed / 9 skipped(全量)。改写:test_force_refresh R2/R3/R5/R8(R8 文件的重复定义顺带去重;R8 语义反转为"窗口内不收,过窗收+纪元收尾")、test_config_store a_class_passes(同款反转)。
- 新增 `tests/integration/test_sunset_drain.py` 4 例:D1 窗口内会话连续(route 亲和+touch+不回收)/ D2 surge 头寸(max_pods=1 且唯一 Pod 排空中,新会话仍可部署补位——旧语义此处 503)/ D3 B 类下发不延长窗口 / D4 drain 键缺失兜底立即回收(闸门收敛不破坏)。
- Redis Cluster 兼容(动 Lua 红线):verify_redis_cluster.py 增 [4d] 三查(无 surge 满槽对照 / 纪元激活 need_deploy / 收尾回落 max_reached),真三主 cluster 23/23。
- 真环境冒烟(integration_smoke.sh,发布门禁)待发版时按 CLAUDE.md 标配跑;阶段 10b 重建等待 120s 上限对 surge 重建充分。

## 影响面

- spec 同步:resource-manager.md(文件表/Lua 表/键表/facade 表/autoscale/reclaim/update_pool_config)、session-manager.md(config_refresh 闸门时长与两闸门维度说明)。
- 行为变化对外可见三点:① 会话中的 Pod 不再被秒杀(窗口 ≤ session_ttl);② 排空期内同类配置操作 409 时间变长(≤ session_ttl,带 retry 语义);③ 排空期间集群瞬时 Pod 数可达 max_pods+1。
- 运维适配:改模板攒完一波再刷(34s 连刷会先撞 409);冷启动敏感场景维持 min_idle≥1。
- 遗留开放问题:surge 余量写死 1,多 Pod 同批日落时第二个补位要等第一个排空收尾;若未来要按日落 Pod 数动态给余量,需三处闸门同步改造。`2026-09-sunset-drain-semantics-fix.md` 的"硬切决策"自本篇起被取代(其闸门/回收框架保留)。
