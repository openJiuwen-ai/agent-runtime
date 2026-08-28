# 全量审计 → e2e 实锤 → 修复:16 项缺陷闭环

- 日期:2026-08-27
- 里程碑 / commit:待提交
- 涉及模块:session_manager / resource_manager / 测试 / 冒烟脚本 / 文档

## 背景与动机

2026-08-27 对全服务(约 5100 行 src)做了一次以「复盘三类根因」(替身冻结契约假设 /
加速手法短路真实机制 / 生命周期边界零覆盖)为镜头的五维并行审计(状态层原子性 /
停机并发多副本 / 外部契约 / 测试缺口 / 业务语义)。静态读码产出 ~40 条假设后,
**先写实锋试验再修**(用户定的流程):`tests/integration/test_audit_repro.py`
16 条用例,全部走真实流程(config_sync / route / 真实 tick)+ 小 TTL 自然到期,
**零指针回拨、零直改 Redis 键**;竞态窗口仅用调度/故障注入控制时序(包装注入点、
按 facade 真实顺序驱动状态原语),被测业务逻辑零改动。

实锤结果:**16 条假设 16 条成立,零证伪**。随后以这 16 条为驱动完成修复,
修复后全部转绿,存量 277 用例零回归(终态 293/293)。

## 修复清单(按机制分组,非逐条打补丁)

1. **等待队列/占位全部 deadline 化(ZSET)**——`scope:{sid}:waiters` 与
   `resource:scope:{sid}:deploying` 从 SET 迁移为 ZSET(score=deadline 秒级时间戳),
   闸门先 `ZREMRANGEBYSCORE` 清崩溃遗留再 ZADD 先行+超限自退(照抄
   deploy_followers 的既有纪律)。修:C9(崩溃副本遗留 waiter 永久占名额→
   恒 SCOPE_QUEUE_FULL)、C5 的硬崩半边(deploying 占位无 TTL 无对账)。
2. **等待循环重仲裁**——`_wait_for_capacity` 等待者成员资格全程保持(中途删/加
   的空窗会让 max_waiters 漏收),≤500ms 轮询超时无信号时经 `re_arbitrate`
   就地重跑 ROUTE_PLACE——docstring 宣称的「安全轮询双保险」真正落地。
   修:C7(publish 早于 subscribe 的丢信号 → 等待者空等满 30s 才 504)。
3. **refresh 存活守卫**——LUA_ROUTE_PLACE 亲和续期前提 pod:info 存在;注册已清
   的绑定判死、惰性回收后走重新放置。修:C8(notify_pod_dead 窗口内新落的会话
   无限热循环,每圈续期 expiry,sweeper 救不了;fakeredis 下连 wait_for 超时回调
   都被饿死)。顺带删除 route() 里恒 False 的 `waitering` 死代码。
4. **暖池版本感知**——autoscale 的 skip_warm 只数**当前版本** idle(旧版永不可
   复用,不能拿来满足 min_idle);reclaim 的 excess = 旧版本 idle(恒为 excess)
   + 当前版本超出底数部分;底数只保护当前版本最早的 min_idle 个。修:C2
   (A 类变更后旧暖 Pod 被底数永久保护 → 暖池钉死旧版 + 蹲占 max_pods →
   恒 NO_POD_AVAILABLE,与 C1 形成双重锁死)。
5. **日落中间态检查改按版本判定**——registered∖candidates 中 deploy_ver ≠ 新版本
   的才是真日落残留;该集合差同时是 idle_consider 的合法中间态(HLD §5.1),
   按形状判定会把正常空闲 Pod 误拒。修:C1(min_idle≥1 时 config_sync 永久 409,
   连推 min_idle=0 解锁的操作本身也 409,无配置面逃生通道)。
6. **候选集版本收敛(声明式)**——扩散② 从「diff 驱动的 one-shot」改为对每个
   存活 scope 每次下发都重算(写 DB 后中途失败/同载荷重试 diff==none 时,
   软摘除不再被跳过)。修:C12(重试后旧版本 Pod 无限期继续接新流量)。
7. **drain 收敛以 RM 为真源**——扩散③ 目标集 = RM 已知 scope(新增
   `rm_facade.known_scope_ids()`)∪ DB 旧 scope − 本批;DB 删行后失忆,
   RM config 键才是幻影预热的真源。修:C11(被删 scope 的 min_idle=0 推送
   失败一次后永不补推 → 不可路由 scope 永久烧容量)。
8. **探测参数随 Pod 烘焙**——LUA_REGISTER 落 sse_port/health_path 进 pod:info,
   watch 探测优先用 Pod 自己的参数(旧 Pod 回退 scope 当前配置)。修:C3
   (health_path/sse_port A 类变更后 20s 内误杀全部带活跃会话的老 Pod——
   与缺陷③同根,当时只修了 readiness 一半)。
9. **失败路径不留孤儿物理 Pod**——RealK8s.deploy 在 create 之后的任何失败/取消
   先 best-effort 删除再抛,DeployFailed 携带 pod_id/namespace;服务层
   `_deploy_and_register` 兜底删除 + REGISTER 步纳入 except BaseException 保护。
   FakeK8s 新增 `fail_after_create` 旋钮(create 成功但永不 Ready 的真形态)。
   修:C4a/C4b/C5。
10. **幂等缓存存活校验**——acquire idem 命中时校验 Pod 仍在(PURGE 过即弃缓存)。
    修:C6(同 request_id 重试回放死 Pod,SM 侧复活注册 + pods:registered 幽灵)。
11. **config_sync 校验补齐**——int 字段严格校验(畸形 "abc" → 400,不裸抛
    ValueError 成 500)、策略字段下界(cc/pc/ttl ≥1、min_idle ≥0、sse_port 域)、
    `Template.__post_init__` 路径归一(sse_path/health_path 补前导 `/`)。
    修:C10a/C10b/C13(pod_concurrency=0 是拒绝服务配置;缺 `/` 会拼出
    `http://ip:8080api/...` 非法端口 URL → 健康 Pod 被探死无限重部署)。
12. **发布门禁脚本契约参数落地**——`e2e_hld_acceptance.py` 新增
    `--health-path/--sse-path/--agent-env`(或对应 env 变量),模板带
    health_path/agent_env。修:审计发现的门禁自伤(`--image` 只换镜像,
    按文档跑真镜像门禁 readiness 探 /health 打真镜像 /api/v1/health → 阶段 2
    起全红——2026-08-26 建立的门禁在脚本里跑不起来)。

## 关键测试口径(与既定方法论一致)

- 实锤用例断言的都是**期望的正确行为**:修复前 FAIL = 缺陷实锤,修复后全绿转回归网。
- C8(自旋)用独立线程 + 硬 join 模拟「请求必须有界完成」:热循环在 fakeredis 上
  无真实挂起点,同循环的 wait_for 超时回调会被饿死——这本身就是缺陷烈度的证据
  (生产真 Redis 下由框架 300s 请求超时兜底,期间持续打 Redis)。
- `test_config_sync_rejects_when_sunset_pending` 的构造法同步修正:旧法手工 ZREM
  当前版本 Pod,固化的正是被修掉的误判;现按真实日落残留形态构造
  (软摘除 + pod:info 记旧版本号)。

## 验证

- pytest:**293/293 全绿**(277 存量 + 16 实锤转正;修复过程全量回归三轮,
  中间发现等待循环第一版引入「成员资格空窗漏收」回归并重构为全程保持)。
- 真环境冒烟(2026-08-28,修复后代码重启本机 8091 实例,PG 后端 + 真 Redis/K8s
  + influxdb 替身):**75/75 PASS**。连带修复三处脚本侧键类型失配(ZSET 迁移后
  scard→zcard)与一处巡检时序缺陷(11b「deploying 静息全空」由单次快照改为
  有界收敛等待——min_idle≥1 的 scope 随时有 ~10-12s 在途预热占位,快照必误报;
  当场实测还验证了失败 deploy 的占位清理在真环境生效:09:46:15 一次 autoscale
  deploy 失败,占位即清,下一拍重试成功)。
- 真镜像门禁:待发版前跑(新契约参数:`--image <真镜像> --health-path
  /api/v1/health --sse-path /api/v1/events/stream --agent-env
  '{"AGENT_HTTP_ENABLED":"true","AGENT_HTTP_HOST":"0.0.0.0","AGENT_HTTP_PORT":"8080"}'`)。

## 影响面与运维须知

- **Redis 键类型变更**:`session_manager:scope:*:waiters` 与
  `resource_manager:scope:*:deploying` SET → ZSET;升级需 FLUSHDB 或手工 DEL
  旧键(生产切换本就建议 FLUSHDB,冒烟脚本自带)。
- **LUA_REGISTER argv 追加** sse_port/health_path 两参;旧版本服务与新版混跑
  期间(滚动升级)旧实例写的 pod:info 无这两字段 → 探测回退 scope 当前配置
  (行为同旧版,不劣化)。
- 文档同步:spec 两件(键表/Lua 表/编排流程/k8s 契约/sweeper 语义)、HLD
  (§5.1/§5.2 键表、场景 N 探测参数按 Pod)、CLAUDE.md(用例数/门禁用法)。

## 遗留(下批次)

- K8s→Redis 反向对账(未 REGISTER 的物理孤儿兜底)——deploy 层清理已消掉主要
  泄漏源,进程硬崩于 create 与 REGISTER 之间的窗口仍靠运维 cleanup。
- uvicorn `timeout_graceful_shutdown` 与部署模板 `terminationGracePeriodSeconds`
  对齐(停机走不完会跳过所有 finally 清理——占位/等待已 deadline 自愈,但
  config_sync 锁内中断、follower 清理等仍依赖进程内 finally)。
- route 全程不重 resolve(对已删 scope 白等满 timeout)——P2,待需求方定优先级。
