# scope 重构:config_sync 全量下发 + 规则化路由匹配 + 无请求预热

- 日期:2026-08-26
- 里程碑 / commit:M-scope(待提交)
- 涉及模块:session_manager / resource_manager(仅文档)/ service-core(main)/ 测试 / 脚本 / 文档

## 背景与动机

原 scope 由 `(group_id, bot_id)` 二元组派生(`md5(group+\x00+bot)`),路由靠 `routing_rule`
表的四档通配优先级(精确 > (g,\*) > (\*,b) > (\*,\*))。两个硬伤:

1. **表达能力不足**:匹配维度只有 group/bot 两列,无法按 user_id 路由,无法表达
   `not in` 排除、多值集合、组合条件;user_id 粒度策略在 HLD 里挂着 follow-up。
2. **无请求不预热**:RM 的 `resource:scope:{sid}:config` 只在首次 acquire 时写入
   (RM `orchestrator.py` 首见 scope 分支),而 autoscale 的遍历源恰是 SCAN 这些键——
   **从未被请求过的 scope 永远不会有 min_idle 热备**,「配置即容量」的运营语义落不了地。

需求定案(与需求方逐条确认):

- scope 不再由二元组派生,config_sync **全量下发** `{templates, scopes}`;
- scope = `{scope_id, index, template_id, routing_rules}`,scope↔模板多对一;
- 匹配:`(index ASC, scope_id ASC)` **first-fit**;规则间 OR、表达式间 AND;
  表达式 = `field(user_id|group_id|bot_id) op(in|not_in) values(集合)`;空规则=通配兜底;
- **无请求时为每个 scope 预备 min_idle 热备 Pod**(min_idle 取自其模板,per-scope 独立保有);
- 下发方保证含通配 scope;服务端缺失仅 WARNING 放行。

## 方案(确认过的决策)

1. **完全取代旧协议**:config_sync 只接受 `{templates, scopes}` 全量快照;旧
   `kind/op`(template/routing_rule × create/update/delete/sync)载荷 400 拒绝。不做并存过渡。
2. **缺通配 scope 仅告警放行**(不拒绝下发);运行时无匹配 → 503 CONFIG_NOT_FOUND 沿用。
3. **user_id 必填**:route 入参校验升级为 session_id/user_id/group_id/bot_id 四项非空
   (缺 → 400);求值层仍保留 `None→""` 防御。
4. **删除 scope 自然回收**:推 `min_idle=0` 停预热,存量会话到期止,空闲 Pod 按 pod_ttl
   由 reclaim 排空;**不做强制驱逐**(备选:立即 EVICT+PURGE,被否——会切断在线会话)。
5. **匹配缓存 = 单键全量快照** `session_manager:routing:snapshot`(STRING,无 TTL):
   config_sync 写 DB 后整体重建并原子 SET;route 每请求 1 GET + 进程内按原文 memo 免重复
   解析;缺失/损坏由首次 resolve 从 DB 重建;启动期 `ensure_snapshot()` 无条件重建消冷启动窗口。
   **被否备选**:按 (user,group,bot) 三元组哈希做 per-match 缓存——键空间近乎无界只能靠
   TTL 过期(最终一致窗口)、多副本各自回填不一致、失效靠 SCAN;快照方案把失效语义整体归零。
6. **eager 预热的关键约束**:config_sync 对每个存活 scope 推 RM
   `update_pool_config(scope, pool, pod_spec)` **必须始终带 pod_spec**——RM 侧只有带
   pod_spec 才落 `pod_spec_json`/`deploy_ver`,而 autoscale 无 spec 会 `skip_no_spec`。
   旧 B 类推 `pod_spec=None` 的行为在无请求 scope 上就是预热失败的坑。
7. **受影响 scope 从 DB 确定性求出**(引用变更模板 ∪ 引用切换),不再 SCAN SM 缓存反查
   (旧法只能覆盖「被 route 过的 scope」——正是硬伤 2 的同源盲区)。
8. **日落中间态检查(409)移到写 DB 之前**:新表使受影响 scope 可在写库前求出;拒绝时
   DB/Redis 均未被改动(旧实现先写库后检查,拒绝会留下 DB 已改未扩散的中间态)。
9. **scope 换引用模板也按 A 类日落**:新旧模板自身都未变、但 scope 的有效模板
   deploy_ver 不同 → 同样软摘除老 Pod(旧模型无此场景)。

## 实现

- **新增 `session_manager/routing.py`**(纯函数,零 Redis/DB 依赖):
  `MatchExpression/RoutingRule/RoutingScopeDef/RoutingSnapshot`、`parse_*`(wire 校验,
  `SCOPE_ID_RE=^[0-9A-Za-z._-]{1,128}$` 禁 `:`/`*`/空白——Redis 键与 `pods:registered`
  的 `{scope}:{pod}` 切分依赖)、`build_snapshot/snapshot_to/from_json`、
  `match_scope`(first-fit,跳过模板缺失/禁用的 scope)、`template_to/from_json`。
- **`config_store.py` 重写**:新表 `ROUTING_SCOPE_TABLE_DEF`(scope_id unique /
  match_index 避 SQL 保留字 / routing_rules JSON)取代 `routing_rule`;
  `resolve(user_id, group_id, bot_id) -> (scope_id, Template)`;config_sync 编排:
  锁外校验(400)→ 抢锁(忙 409)→ diff(模板 changed/引用切换/sunset_scopes)→ 日落检查
  (先于写库)→ 写 DB(红线:失败即中止,不 SET 快照不推送)→ rebuild_snapshot →
  eager 推送(带 pod_spec)→ A 类软摘除 → 被删 scope 推 min_idle=0 → 响应含
  `wildcard_present`。删除 `_load_cache/_write_cache/_invalidate_all_scope_caches/
  _cached_scopes_of_templates/_sync_template/_sync_routing_rule` 等全部 per-scope 缓存机制。
- **`models.py`**:删 `ScopeConfig`(快照取代);**`state.py`**:删 `scope_config` 键,加
  `routing_snapshot` 键;**`util.py`**:删 `scope_id_of`;**`orchestrator.py`**:四参校验 +
  `resolve(user,group,bot)` 新签名;**`handlers.py`**:config_sync 日志改记载荷规模;
  **`main.py`**:表定义替换 + `start()` 里 `ensure_snapshot()`(失败降级首次 route 重建);
  **`debug_api.py`**:/debug/config 出 `routing_scopes` + `routing_snapshot` 观测,
  /debug/scope 的 `sm.resolve_cache` → `sm.routing`(快照内定义)。
- **RM 与全部 Lua 零改动**:scope_id 全程是 opaque 字符串;预热靠 config_sync 主动写
  `resource:scope:{sid}:config`(autoscale 1s tick 即补热备)——这是 gap 2 的最小闭合。
- Lua/Redis 键兼容性:`scope:{sid}:*` 键形不变;scope_id 从 md5 hex 变为下发字符串,
  靠入口正则维持键解析安全。

## 验证

- 单测:**114 → 152 用例全绿**。新增 `tests/session_manager/test_routing.py`(16 用例:
  in/not_in/空 values/AND/OR/通配/index+scope_id 排序/first-fit/禁用跳过/None→""/
  scope_id 字符集参数化/校验矩阵/快照 roundtrip);重写 `test_config_store.py`(17 用例,
  含 **eager 预热验收:config_sync 后直接 `autoscale_once` 断言 FakeK8s 长出 min_idle 热备
  Pod——从未 route**、409 日落检查断言 DB 未动、红线 monkeypatch 断言快照原文未变);
  integration 新增多 scope 路由矩阵(index 顺序/user_id/not_in/通配兜底)与缺 user_id 400。
- 真环境(2026-08-26 实测,宿主机 server 模式 + 真 Redis/MySQL/K8s):
  - 部署后零配置 ⇒ **零 AgentServer Pod**(启动期 ensure_snapshot 写空快照,键存在但
    scopes=0,无配置语义不变);
  - 手工下发兜底 scope(min_idle=1)⇒ **零 route 请求,~12s 恰好 1 个热备 Pod Ready**;
  - `./scripts/integration_smoke.sh` **64/64 PASS**(含新 H0 阶段;1 SKIP 场景 N 暂缓);
  - **scope 删除自然排空实测**:全量下发只留兜底 scope ⇒ 4 旧 scope 的 min_idle 推 0,
    旧暖备 Pod 被 reclaim 真删(K8s NotFound + PURGE)。
- grep 残留:`scope_id_of|routing_rule|ROUTING_RULE_TABLE` 在 src/tests/scripts 零命中。

## 真环境验收中发现并修复的两个存量缺陷(2026-08-26)

均在「删除 scope 自然排空」实测中暴露(暖 Pod 9 分钟未被回收),根因都在
`LUA_RELEASE`,修复后单测补齐(`test_rm_state.py` 2 用例):

1. **idle_since 被周期重放无限刷新**:`LUA_RELEASE` 的 `SET idle_since=now` 每次调用
   都刷新计时,而 autoscale 暖 Pod 从未走 acquire → 不在 SM 候选集 → **reconcile stale
   每 30s 重放 release** → reclaim 的 `aged≥pod_ttl` 永不达成 → 超出 min_idle 的空闲 Pod
   永不自然回收(A 类日落的"老 Pod 按 pod_ttl 回收"同样不闭环;此前 e2e 靠手动回拨
   idle_since 掩盖)。修复:**仅首次转入 idle(SADD=1)才起计时**;acquire 弹出后再转
   idle 属新空闲期重新计时(语义不变)。
2. **PURGE 与重放 release 的 TOCTOU 幽灵成员**:reconcile 的 view 快照先枚举到 Pod →
   期间被 PURGE(idle SREM + info DEL)→ 迟到的 release `SADD` 把已回收 Pod 复活成
   idle 幽灵成员——虚增 idle 计数(min_idle>0 的 scope 会少预热)且永不被回收
   (idle_since 缺失,reclaim 跳过)。修复:**info 已清(PURGE 终态)的 release 直接
   no-op**。

## 真实 AgentServer 镜像(jiuwenclaw-agentserver-amd64:0.0.5s)接入(2026-08-26)

以真实镜像替换 influxdb 替身做端到端验收,带出一次**模板契约扩展**与**三个新缺陷修复**:

**契约扩展**(真实 AgentServer 的 HTTP 入口与旧约定不同):
- HTTP 入口默认关闭且绑 127.0.0.1:18092(WS)——须 env `AGENT_HTTP_ENABLED=true` +
  `AGENT_HTTP_HOST=0.0.0.0` + `AGENT_HTTP_PORT` 开启;
- 健康端点在 `/api/v1/health`(非裸 `/health`),SSE 在 `/api/v1/events/stream`(非 `/sse`)。
- 模板新增 **`agent_env`(容器 env 注入)** 与 **`health_path`(健康端点路径)** 两个
  A 类 deploy 字段(spec_fields/k8s `_build_pod_body`/DB 列;`probe_health` 与 readiness
  同源取 health_path);`sse_path` 用既存字段配 `/api/v1/events/stream`。
- 注意:框架 `init_table` 只 `create_all` 不补列(`_sync_missing_columns` 无调用方)——
  已有库加列需手动 ALTER(本次已对 MySQL 执行)。

**缺陷 3——场景 N 探测路径写死**:`probe_health` 固定 `GET /health`,真实镜像 404 →
连续 2 次判半死 → PURGE → autoscale 重部署 → **无限拉起循环**(readiness 绿灯、RM 探测
红灯互相矛盾)。修复:探测路径与 readiness 同源(模板 health_path)。

**缺陷 4——deploy_ver 指纹对嵌套 dict 键序敏感**:`fingerprint` 用 `repr(dict)`,
`agent_env` 这类嵌套 dict 经 MySQL JSON 列回读会**重排键序** → 同一模板算出两个不同
deploy_ver(下发侧 vs 快照侧)→ SM 的 want_ver 与池内 Pod 版本永不相等 → **暖 Pod 复用
被跳过**(全部走 need_deploy,直到 max_pods 堵死)。修复:规范化 JSON 序列化
(`json.dumps(sort_keys=True)`)。算法切换是全局性 deploy_ver 一次性变更,过渡期由
A 类版本过滤自然隔离旧版本 Pod。

**缺陷 5——停机取消泄漏 deploying 占位**:优雅停机取消在飞的 autoscale tick 时
`except Exception` 接不住 `CancelledError`(BaseException),占位不清 → 泄漏的 warm
占位计入 max_pods 把池永久堵死(route 恒 NO_POD_AVAILABLE;两次停机泄两个占位即堵死
max_pods=2 的池)。修复:`_deploy_and_register` 的占位清理改 `except BaseException`
(清后重抛 CancelledError)。

**验收结果**(真实镜像端到端):config_sync 下发(env/health_path/sse_path 对齐)→
autoscale 预热真实 AgentServer(~12s Ready,此前无限循环已根治)→ route 返回
`http://<pod_ip>:8080/api/v1/events/stream` → 同 session 亲和复访同 Pod → session_ttl
到期老化 → 空闲回收/热备补齐自稳。单测 152 全绿。

## 测试补强落地(2026-08-27,复盘五项)

针对「替身镜像冻结契约假设 / 加速手法短路真实机制 / 生命周期边界零覆盖」三类根因:

1. **fingerprint 键序无关单测**(`tests/test_util.py`,3 用例):嵌套 dict 打乱键序
   (模拟 MySQL JSON 列回读)指纹必须不变——缺陷④回归网。
2. **停机韧性单测**(`tests/resource_manager/test_resilience.py`,2 用例):慢速
   FakeK8s 制造取消窗口,autoscale 部署中途 cancel → 占位必清、锁必释,下一拍
   补位成功——缺陷⑤回归网。
3. **冒烟阶段 5b 自然老化**(5 项):tpl-nat(session_ttl=15/pod_ttl=20/min_idle=0)
   **零回拨**真等 TTL 走完 D→K 全链路——缺陷①回归网(在场则"计时自然累积满
   pod_ttl→reclaim"永不成立,当场 FAIL)。
4. **冒烟阶段 11b 不变量巡检**(4 项,cleanup 前):idle⊆pods:all 且成员必有
   idle_since / 静息 deploying=0 / 快照模板 deploy_ver==RM cfg 且 cfg 内自洽——
   缺陷②④⑤回归网。
5. **真镜像发布门禁**(约定,记入 CLAUDE.md):发版前用真实 AgentServer 镜像跑
   `integration_smoke.sh --image <真镜像>`;influxdb 替身仅留快速回归。

验证:pytest **157/157**;真环境冒烟(PG 后端)**74/74**(新增 5b/11b 全过,
psql 落库校验分支补了空输出诊断)。

## 影响面

- 文档同步:HLD(§2.3 名词/§3.1 契约与匹配语义/§5.1-5.2 键表/场景 H·M/§7.5)、
  spec(session-manager 全节/resource-manager autoscale·reclaim/README 一页纸/
  e2e-test-cases 场景 M 与 H0)、CLAUDE.md 用例数。
- **行为变化(运营须知)**:所有 min_idle>0 的 scope 在 config_sync 后即预热——集群
  Pod 总量 = Σ per-scope min_idle(多 scope 共享模板不共享热备),下发前请评估容量。
- **契约破坏**:config_sync 载荷格式不兼容(下发方 manager/gateway 需按新契约适配,
  本仓库内仅测试/脚本调用,已全部更新);route 新增 user_id 必填。
- 旧数据残留:DB `routing_rule` 表与旧 md5 scope 的 Redis 键成死数据(不读不写,无害);
  生产切换建议 FLUSHDB(冒烟脚本本就做)。
- 遗留:被删 scope 的 RM config 键残留(min_idle=0 后 no-op,复活时覆盖;后续可选:
  reclaim 在「min_idle=0 且 idle 空」时 DEL 键);千级以上 scope 的快照体积再评估分片。
