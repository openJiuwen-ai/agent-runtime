# 日落排空语义修复:sync 闸门基准改当前生效版本 + reclaim 落后 Pod 免老化即刻回收

- 日期:2026-09-15
- 里程碑:M 维护期
- 涉及模块:session_manager(config_store)/ resource_manager(sweeper)/ 测试 / 文档

## 背景与动机

cyz 联调环境实测:web 端「先下发成功 → 修改配置 → 再下发」**必 409 CONFIG_SYNC_BUSY**,
detail=`still has sunset pods pending reclaim`,且永不自愈(暖 Pod idle 33.6min ≫
pod_ttl=180s 仍不回收)。根因是**版本口径不一致**:

- config_sync 前置日落闸门拿 Pod `deploy_ver` 比**新载荷**版本——版本变更下发时,
  当前代空闲 Pod 必然 ≠ 新版,被误判成「日落遗留」;
- 而 reclaim 的 min_idle 底数保护比**当前 cfg** 版本——该 Pod ver==cfg 判 warm,
  全额保护**永不回收**;
- 两侧各执一词:闸门等一个回收永远不会执行的 Pod → **配置面永久 409**。
  这不是边缘场景:安静 scope(min_idle≥1、暖 Pod serve 过一次后空着)的稳态
  恰好就是 registered∖candidates 中间态,低流量 scope 改配置必然命中。

对照组:config_refresh 的闸门(`orchestrator.sunset_pending_pods`)比**当前代次**
——「落后于当前代次 = 上轮已承诺排空」本来就是正确口径,无死锁。

## 方案

定案要点(与需求方逐条确认过):

1. **① 闸门基准修复**:`_sunset_pending_pods` 比较基准从新载荷 ver 改为
   **当前生效版本**(`old_templates[old_scopes[sid].template_id].deploy_ver()`)。
   语义 =「上一次落库已承诺排空的版本是否排空完成」:ver ≠ 当前 cfg 的离集 Pod
   才是真遗留(有界等待);ver == 当前 cfg 的是合法空闲中间态,放行,落库后由
   扩散②软摘 + reclaim 收敛。新 scope / legacy 无版本 → 放行。
   推演:ver2 下发(初始 ver1)必然成功;ver3 下发只拦未排空的 ver1 遗留;
   全是 ver2(当前代)则放行。
2. **③ reclaim 两级回收**:stale(ver/gen 落后)idle Pod **免老化即刻回收**——
   acquire 的 want_ver+generation 过滤已判死刑,零复用价值,蹲满剩余 pod_ttl
   只白占 max_pods 槽位并拖长闸门等待(同时缓解连续 refresh 撞顶 503 的占槽
   窗口);当前版本超额保留 `aged ≥ pod_ttl` 抗复用抖动(真镜像冷启动 p50≈12s)。
3. **② 会话保护被否决(确认过)**:reconcile 会把扩散②摘出、仍在服务的旧版 Pod
   release 进 idle 池,reclaim 即刻回收 = 会话被硬切重放置(丢 Pod 本地上下文)。
   曾给出 A(reclaim 查会话)/B(reconcile 判 stale 加会话条件)/drain deadline
   三案,**需求方决策:不做保护,接受硬切**——换取排空窗口有界且极短(约一拍)。
   后人若要恢复亲和保活,从 B 案入手(恢复「idle 池=无会话」不变量)。

被否掉的备选:闸门维持比新载荷版本(= 维持死锁);闸门 409 时顺带标记日落
(违反「拒绝时 DB/Redis 零副作用」红线);同步删除塞进 sync 临界区(跨层且
拉长锁持有)。

## 实现

- `session_manager/config_store.py`:闸门调用点基准改 `old_templates[old_scopes
  [sid].template_id].deploy_ver()`;`_sunset_pending_pods(scope_id, cur_deploy_ver)`
  改参名+空基准早退;注释重写口径推导。**扩散② `_soft_remove_stale_pods`
  仍比新版本**——那是「本次要承诺的排空」,语义正确不动。
- `resource_manager/sweeper.py`:`reclaim_once` 循环改两级判定(`pod_id in
  stale_set or aged ≥ pod_ttl`);reclaim pending 留痕的 due 口径同步
  (stale 全部即刻到龄);模块 docstring 更新。
- 无新增键 / Lua / 接口;红线零触碰(闸门仍先于写库、拒绝零副作用)。

## 验证

- 单测:**528 passed, 9 skipped**(原 525 基线 +3):
  - 新增 `test_config_sync_a_class_passes_with_current_version_idle_pod`
    (cyz 病理回归:当前代空闲 Pod + 版本变更 → 放行 + 落库后即刻回收闭环);
  - 新增 `test_R6_reclaim_stale_immediate_no_aging`(pod_ttl=60 未到龄,老代
    同拍回收)/ `test_R7_reclaim_warm_overflow_still_ages`(当前版超额仍须
    aged ≥ pod_ttl,底数保留);
  - 改写 `test_R3_refresh_then_sync_guard_semantics`:A 类从「409 等排空」改
    断言「放行 + 老代即刻回收」(原断言即死锁病理本身);
  - `test_config_sync_rejects_when_sunset_pending`(真版本遗留仍 409)、
    `test_config_sync_not_blocked_by_refresh_sunset_pods`、audit_repro C12 等
    原语义用例零改动通过。
- 真环境(2026-09-15,镜像 `agent-runtime:sunsetfix-20260915a`,ns
  agent-runtime-e2e ×3 副本,NodePort 30091):
  - **真镜像集成冒烟 127/127 PASS**(agentserver 0.0.16s + sandbox 0.0.18s
    三件套契约,--with-sidecar --with-mounts 全规格);
  - **config_churn 180s:74/0/0**(100,484 请求,17 次 sync 全 200,
    ERROR 日志 0,亲和 0 违规);
  - **cyz 病理现场复现 ALL PASS**:min_idle=1 scope route 后静止 95s →
    死亡组合就位(registered∖candidates ∩ idle,ver==cfg)→ A 类下发
    **200(0.0s,修复前永久 409)** → 老 Pod **2.2s 回收**(pod_ttl=600,
    ③ 免老化)→ 重发 200 + 新代暖池重建;
  - config_refresh 180s:27/2——**非回归**(两次复跑一致;卡闸 Pod 全程
    被 touch 活会话占住,永不进 idle 池,③ 不在其路径;闸门代次版未改动)。
    根因是 refresh 闸门既有背压语义撞上场景参数:15s 连刷 ≪ session_ttl=600s
    长活会话 → 闸门等会话排空属设计行为(12h 浸泡零 409 因刷新间隔 ≈
    session_ttl)。场景零 409/503 预期对此节放过紧,待场景白名单或设计定夺。

## 影响面

- 文档:spec(session-manager config_sync 流程与 refresh 交互条目 /
  resource-manager reclaim_once 行)已同步;HLD §5.1 中间态描述未动
  (集合语义未变,变的只是版本口径)。
- 行为变化(运维感知):版本变更下发对安静暖池 scope 从「永久 409」变为
  「放行,老代 Pod ~1 分钟内回收」;**仍在服务的旧版 Pod 上的会话会被硬切**
  (重放置到新 Pod,丢 Pod 本地上下文)——已确认接受。
- 兼容:无 schema/键/Lua 变更,无存量库前置 SQL。
- 遗留:若未来要恢复「版本变更不切活会话」,按②B 方案实现;e2e 用例
  (`e2e-test-cases.md`)的 409 串行化相关条目按新语义复核待做;config_refresh
  压测场景对「连刷背压 + 容量 503」的预期需与闸门设计对齐(白名单化或调
  cadence,参照 mixed 的 NO_POD_AVAILABLE 白名单先例);真镜像 tag 甄别:
  `0.0.34r` 是 WS 契约构建(仅 ws://127.0.0.1:18092,8086 HTTP 入口无),
  三件套 HTTP 契约用 `0.0.16s`。
