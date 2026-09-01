# config_refresh:全 scope Pod 强制刷新(代次日落 + 按存量配置重建)

- 日期:2026-09-01
- 里程碑 / commit:—(待提交时回填)
- 涉及模块:session_manager / resource_manager / 测试 / 文档

## 背景与动机

运维面需要"强制刷新"能力:**不改任何配置**,让所有 scope 的现有 Pod 走一轮优雅日落并按存量配置重建。动机是 Pod 实际状态可能与配置**漂移**——configMap/PVC 内容变化、密钥轮换、怀疑 Pod 状态异常——这些不在 `deploy_ver` 指纹覆盖内(指纹只覆盖 DEPLOY_VER_FIELDS 子集)。此前唯一的全量重建路径是 `cleanup` 批删(打断在跑会话,中断存量)。

**缺口与陷阱**:日落机制完全由 `deploy_ver` 比较驱动,配置不变时没有任何路径能把"当前版本"的 Pod 标为待日落;且单纯把 Pod ZREM 出 SM 候选集(版本不变)后,reclaim 的 min_idle 底数保护仍把它们算"当前版本 warm"→ 既不回收、autoscale 也不重建(认为 warm≥min_idle)→ 永久蹲占 max_pods。

## 方案

定案要点:

1. **新增独立端点 `POST /api/session/config_refresh`,无载荷**(rawdata 非空 → 400 VALIDATION)。**确认过**(与需求方):优雅日落(老 Pod 停接新会话、存量会话自然跑完、不断在跑 SSE)+ 独立端点(config_sync 保持三段式严格载荷不受污染)。
2. **核心机制 = RM 代次(generation)日落标记**:`scope:config` 新增 `generation` 字段,唯一写点 = config_refresh 的 HINCRBY(update_pool_config 的 mapping 永不含它,config_sync 推送永不重置);"当前版本"判定从 `deploy_ver` 相等收紧为 `deploy_ver 相等 ∧ generation 相等`;两侧同缺(空串)视为一致——从未刷新过的 scope 零行为变化,升级前存量 Pod 判 stale(刷新即日落,预期)。
3. **REGISTER 服务端烙印**:LUA_REGISTER 在 Redis 服务端读 scope:config 当前 generation 写进 pod:info——注册与 bump 在 Redis 单线程上原子排队,消除"Python 读旧写旧"竞态(deploy 中途刷新,晚于 bump 注册的 Pod 天然属新代,不误伤)。
4. **锁内三步,顺序红线 bump → push → ZREM**:① HINCRBY(严格)② 重推池参数+pod_spec(值未变;失败仅告警,良性)③ 候选集全量 ZREM(严格)。唯一危险序是"ZREM 而未 bump"(搁浅态:被摘却仍是当前代 warm,底数保护永久蹲占);bump 在前的任何中途失败都收敛于"老 Pod 暂时继续接新流量",重试即收敛。
5. **错误码复用 CONFIG_SYNC_BUSY(409)**:与 config_sync 共用 `lock:config_sync` 双向互斥,同忙等语义(退避重试),契约面零膨胀;error_message 文案区分。**确认过**。
6. **守卫不扩展**:config_sync 的日落中间态守卫保持 deploy_ver-only——老代 Pod 版本与当前配置相等 → 不可见 → 刷新后 B 类/同版本下发不 409(避免刷新使配置面长时间 409,同 C1 病理);A 类(换版本)照旧 409 到排空完成(与 M 期 A-叠-A 一致)。老代回收由 reclaim 代次感知保证,无需 SM 读 RM generation 的新回调(模块边界零破坏)。
7. **重建全自动**:autoscale warm 底数按 ver∧gen 匹配 → 归零 → 用缓存 pod_spec_json 重建(配置零变化,仅换代);reclaim 把老代 idle 恒判 excess 按 pod_ttl 回收。无任何新触发逻辑。
8. **scope 范围 = DB 存活 scope**(幻影 scope 归 config_sync 扩散③ drain 路径);模板缺失的悬挂 scope 跳过 + WARNING(同 match_scope 容错)。

被否掉的备选(防重踩):

- **generation 掺进 deploy_ver 指纹输入**:破坏 SM `Template.deploy_ver()`/RM `_deploy_ver` 两端字段集 parity,且 `_diff_class` 会把每次刷新误判为 A 类(污染 affected_scopes、触发 409 守卫)。
- **DEL 重建 scope:config 当代次**:窗口内 `has_scope_config` 为假 → LUA_ACQUIRE `no_config` → route 503。
- **复用 `pod:info.phase`**:纯信息性字段(created/idle),不参与任何判定,语义不符。
- **改 max_pods 口径排除老代 Pod**(缓解排空期挤压):违背 max_pods 物理封顶语义,瞬时超配。
- **ZREM 先于 bump**:见定案 4,搁浅态。
- **复用 config_sync 加 force_refresh 标志位**:要放松刚收紧的三段式独占校验(2026-08-31 wire 收紧),污染契约单一形态。

## 实现

- `resource_manager/lua_scripts.py`:LUA_ACQUIRE idle 过滤加 `generation` 比较(cfg 侧 HGET 或 '' 归一);LUA_REGISTER 服务端烙印 generation(ARGV 不变,`state.register_pod` 签名零改动)。
- `resource_manager/state.py`:`bump_generation(scope_id) -> int`(HINCRBY);键 docstring 补字段。
- `resource_manager/orchestrator.py` / `facade.py`:`bump_generation` 透传出口;`update_pool_config` docstring 注明 mapping 永不含 generation。
- `resource_manager/sweeper.py`:`_current_version_idle` 双条件(ver ∧ gen),autoscale/reclaim 调用点零改动。
- `session_manager/config_store.py`:`GenerationBump` 回调 + `config_refresh()` / `_config_refresh_locked()` / `_soft_remove_all_pods()`(锁骨架仿 config_sync)。
- `session_manager/handlers.py` + `main.py`:第 5 个端点注册 + `bump_generation` 注入;多处"4 个端点"表述改 5。
- Redis Cluster 合规:无新键/无键名变化(generation 是两个既有 HASH 的新字段);改动 Lua 经真 cluster `verify_redis_cluster.py` 16/16 通过(含新 [4b] 代次段:bump 前注册烙空、bump 后注册烙新代、acquire 跳老代选新代)。
- 运营注意(同步进 HLD 场景 M-R):**非幂等但收敛**(成功后勿自动重试);排空期(≈ reconcile 30s + pod_ttl)舰队级容量挤压,新会话可能 503——低峰执行或先调小 pod_ttl;滚动升级完成后再刷新(混布旧副本无代次判定)。

## 验证

- 单测/集成:pytest **415 passed**(原 394 + 新 21):
  - RM 代次 5 个(test_rm_business.py):bump 单调且推送不重置 / REGISTER 烙印 / acquire 跳老代选新代 + 正向对照 / `_current_version_idle` 代次划分(小 TTL 真等,零回拨)。
  - SM 层 8 个(test_config_store.py):日落+代次 / 亲和保持 / 带载荷 400 零副作用 / 锁互斥 409 / 空 DB noop / 不动 DB 与快照 / 悬挂 scope 跳过 / 守卫不扩展钉死(B 类放行)。
  - 自然老化网 4 个(test_force_refresh.py R1–R4,审计网方法论):全量周期(亲和→自然 idle→reclaim 回收→新代重建)/ 重复刷新收敛 / 刷新后守卫交互(B 放行 A 409 到排空)/ 重建用缓存 pod_spec。
  - HTTP 冒烟:`test_http_all_five_endpoints` 改名并插 config_refresh 步骤;新增带载荷 400 / 占锁 409 两用例。
  - EVICT 残骸自卫 1 个(test_sm_state.py,见下"门禁连带修复"②)。
- 真环境(2026-09-01,单实例 server 模式 + 真 Redis/MySQL/K8s):`integration_smoke.sh` 全门禁形态(agentserver/sandbox **0.0.9s 真镜像 + 三件套契约参数 + --with-sidecar --with-mounts`)——**121/121 全绿**,含场景 M-R 阶段 10b 全 8 项(7 scope 代次日落、候选集全清、存量会话亲和同 Pod、autoscale 重建烙新代暖 Pod、真实新部署、老代排空态)与 51b 带载荷 400。
- Redis Cluster:本地三主 cluster 上 `verify_redis_cluster.py --wipe` **16/16 通过**(新增 5 项代次断言;LUA_EVICT 残骸自卫改动后复验同过)。

### 门禁连带修复(2026-09-01 真镜像门禁首跑暴露,非本功能回归)

1. **e2e `_sidecar_standin()` 探针端口硬编码**:真 sidecar 镜像分支下容器端口切 8321 而探针仍烤死 8096 → 三段式校验 400,整个冒烟 37/69 连锁红。修:探针/容器端口同源(同 `_jiuwenbox_container` 模式)。替身模式两端口同为 8096 故历史无感。
2. **服务侧 LUA_EVICT 残骸自卫**:e2e stage4 原用"回拨 expiry(zadd+hset 直改)"加速,hset 落在已被自然驱逐的会话上会重建出**仅剩 expiry 的残骸哈希**,LUA_EVICT 对其崩溃循环(761 次/tick 级)→ sweeper 到期 pass 永久瘫痪。服务侧修复:残骸哈希(缺 scope/pod)自清两处返回 rubble + WARNING,**单坏键不得使到期 pass 崩溃**;e2e 侧 stage4 改自然到期(零回拨/零直改,对齐 5b 与 e2e 覆盖硬标准)。
3. **e2e envFrom prefix 断言与载荷自相矛盾**:载荷故意一条带 prefix 一条不带,旧断言要求"全有 prefix"恒 FAIL;修为逐条一致断言。
4. **PVC 写入探针被复用 PV 里的陈旧 root 文件挡**:真镜像非 root(uid 1000)打不开替身 root 时代遗留的同名 644 `probe` 文件(目录本身可写,mkdir 都过);修:探针文件名带 uuid 后缀。另 stage0 增 hostPath 数据目录放权(按 PVC 实绑 PV 反查路径,`_node_exec` 本机/ssh 单串执行)。

## 影响面

- 文档同步:HLD(§3.1 接口表/错误码枚举/§5.2 键表/§6 场景表 + 场景 M-R 时序小节/鉴权段五接口)、resource-manager-design(§2.1/§2.2.1/新 §2.2.2/§3 键表/LUA_ACQUIRE/LUA_REGISTER 伪码/sweeper 表)、session-manager-design(§4.3 串行化/新 §4.3b/§13.1 facade 契约/§14 handler 清单)、spec×5(session-manager/resource-manager/service-core/README/e2e-test-cases:§2.1 五端点/§2.2 错误语义/阶段 10b/§5.3 新表/计数 75→84)、CLAUDE.md(pytest 394→414)。
- 兼容性:纯加法——存量库/存量 Pod 无需迁移(缺省 generation 视为同代);旧副本与新副本混布期间刷新语义削弱(见运营注意),升级完成后正常。
- 遗留:排空期舰队级容量挤压未做代码层缓解(有意,见被否方案);`_sunset_pending_pods` 对"老代+版本相同"Pod 不可见属设计决策(R3 钉死),若未来需要"刷新完成度"观测,可加只读诊断而非扩展守卫。
