# M8:deploy 锁输家改为 follower 等待室——跨副本冷竞争零多余 Pod

- 日期:2026-08-24
- 里程碑 / commit:M8 / `58fc3301`
- 涉及模块:resource_manager(acquire 链路)、session_manager(models.pool_config)、双实例测试

## 背景与动机

多副本部署下,冷突发(多个 route 同时到达、scope 无 Pod)会发生跨副本冷竞争:多副本同时进 acquire 的 need_deploy 分支,抢 `lock:rm:deploy:{scope}` 选主。

改前的输家行为:清占位后短暂自旋(0.3s)重跑 ACQUIRE,等锁空闲后**自己再 deploy 一个 Pod**。后果:

- 输家自建的第 2 个 Pod 在 `max_pods` 内合法,但大概率是空 Pod,要经 SM 空 Pod pass → idle_consider → reclaim 链路自愈——资源浪费,冷突发尾延迟高(实测 30.5s);
- 该问题作为开放问题记录在(重组时已删除的)开发交接文档 §十一.1 与 `docs/spec/e2e-test-cases.md` §8.2,本次关闭。

## 方案(定案,与需求方逐条确认)

- **等待有界**:follower 等待上界 = `ready_timeout + 10s` 余量(注册开销)。
- **leader 失败,follower 不接管、直接失败**:同镜像同环境下 follower 接管大概率也失败,接管只会放大故障;失败判定 = deploy 锁空闲且无新 Pod 注册进展。
- **等待室在 Redis**(ZSET + deadline score),准入原子;**防崩溃泄漏**用 `ZREMRANGEBYSCORE(deadline)` 兜底——裸 SET 只靠 finally 出队,进程崩溃即泄漏(SM waiter 集合的既有教训,这里不重蹈)。
- **overflow 严格快失败**:准入上限 `pod_concurrency - 1`(leader 会话之外新 Pod 恰剩这些槽);超限抛 MaxPodsReached→503 NO_POD_AVAILABLE;`pod_concurrency=1` 极端场景不做特殊处理。
- **错误路径双清**:deploying 占位 + deploy_followers 成员都进 finally。

被否方案:输家接管 deploy(见上,放大故障);进程内等待(多副本下无意义,等待室必须共享)。

## 实现

- `LUA_DEPLOY_FOLLOWER_GATE`(RM 第 6 个 Lua):先清过期成员 → ZADD 先行 → `ZCARD > pc-1` 自退——纪律同 SM 的 `LUA_WAITER_GATE`(禁止「先查后加」)。新键 `resource_manager:resource:scope:{sid}:deploy_followers`(ZSET,request_id→deadline)。
- `orchestrator.py:acquire` 锁忙分支:清占位 → `_follow_leader` 轮询——发现新 Pod 注册且 pod:info 有 sse_url → **直接复用返回**(与 reuse 分支同构);SM 侧重跑仲裁即可,**SM 零改动**、不破「RM 不读 SM 容量键」红线;锁空闲无进展 → DeployFailed;deadline → MaxPodsReached。
- `Template.pool_config()` / `resource:scope:{sid}:config` 携带 `pod_concurrency`——**仅用于推导 follower 上限 pc-1**,per-Pod 容量闸门仍在 SM 侧(红线不变)。

## 验证

- 单测:pytest 106 → **114**(follower 闸门单测 6 + 双实例 2),连跑 3 次稳定。
- 双实例用例 4 收紧:并发冷启动断言从「1 ≤ 部署次数 ≤ 2」收紧为「**恰好 1 次部署**」。
- 真环境(2 副本经 LB):M6 冒烟 **65/65**,多副本 e2e **35/35**。
- 性能实测:冷启动尾延迟 **30.5s → 10.2s**(冷突发从串行多部署变为 1 次部署 + follower 复用),零错误。

## 影响面

- 文档同步:HLD 键表+读图;RM 设计 §2.1/§3/§5(Lua 5→6 含全文);code-guide Lua 表+流程;e2e-test-cases §8.2 开放问题关闭。
- 测试计数基准更新为 114(模块 CLAUDE.md 同步)。
- 注:2026-08-24 文档重组后,原 code-guide 已拆分为 `docs/spec/` 各模块 spec,本文件为该改动的正式记录。
