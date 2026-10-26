# routing 供给模式:外部预建 Pod 只分配不建删(零 K8s 权限)

- 日期:2026-10-26
- 涉及模块:resource_manager / session_manager / service-core / 测试 / 部署 / 文档

## 背景与动机

一类客户环境不授予任何 K8s 权限(create/delete/list/watch/get 全无),agent-runtime 的全托管形态(自建/自删 agent-server Pod)完全无法落地。客户自己有权限、也有意愿用 StatefulSet 预拉起 AgentServer,希望 agent-runtime 只做"分配":注册进来的 Pod 进现有分配池,路由/亲和/保活/驱逐全复用,runtime 绝不创建/删除物理 Pod,且一次 K8s API 调用都不发生(SA 零 pods 权限,Role 可不建)。

与 deploy 工具层既有的 `AGENT_RUNTIME_ENABLED=false`(静态 AgentServer,完全绕过 runtime)是两种形态:本特性是中间态——Pod 外部供给,但**分配仍归 runtime**(路由/闸门/探活/日落全套复用)。

## 方案(与需求方多轮确认的定案)

- 开关 `AGENT_RUNTIME_DEPLOY_MODE=full|routing`,**仅显式两值、非法启动 raise**(仿 REDIS_LUA 严格解析:模式配错静默回 full 会让受限环境首个 deploy/delete 才撞 403)。缺省 full,存量行为逐字节不变。确认过:不混合(纯外部)、不考虑 full↔routing 互切、不考虑多副本异模式。
- **注册只收 host 域名**(确认过,否掉 pod_ip 形态):headless service 稳定域名是 StatefulSet 同名重建后唯一能自动跟随的地址形态;IP 形态重建换 IP 无法自愈、需要重注册,已否决。
- **接口纯必填字段**(确认过,可选参数全删):`register_pods {scope_id, pods:[{name, host}]}`;端口/路径/健康路径不入载荷,由 scope 模板推导(模板即运行契约,客户 StatefulSet 须与之一致;scope 无模板 400)。`deregister_pods {scope_id, pods}` 无 force——注销是显式管理动作,直接驱逐会话并注销。
- **就绪判定 = 注册时 HTTP 探测一次**(确认过;否掉"不校验直接入池"——会把死地址喂给 gateway):通过入池,失败转 pending 由 30s 收养循环补。
- **池空不同步补收养**(确认过;否掉 route 侧同步探测——实现重且引入探测时延):无可用 Pod 直接 503 NO_POD_AVAILABLE,恢复靠后台循环。
- 判死阈值不新增环境变量(确认过):固定连续 2 次(≈20s),周期/超时沿用既有 `AGENT_RUNTIME_WATCH_INTERVAL`/`AGENT_RUNTIME_HEALTH_PROBE_TIMEOUT`。
- **豁免谓词 = declared HASH 成员资格**(`resource:scope:{sid}:declared`),非 info 标志:scope 删除(config_store 扩散③ drop_declared)DEL 键 → 外部 Pod 失去版本豁免 → 回归既有 gen-lag 日落排空(purge 仍不物理删),与 full 模式删除行为对齐,零新增排空代码。`info.external=1` 仅承载"永不物理删/watch 跳相位"。
- **五个版本/代次豁免点**(漏一个即 config_refresh 永久 409 或池被钉死):LUA_ACQUIRE 复用过滤(HEXISTS declared)/reclaim excess-stale 分类(has_lagged_pods)/sunset_pending_pods(refresh 前置闸门)/SM `_soft_remove_stale_pods`(A 类软摘,经 ctor 回调 declared_pods);`_soft_remove_all_pods`(refresh)不豁免——摘出后下一 route reuse+重注册回集。
- **SM 判满哨兵**:`LUA_ROUTE_PLACE` 第 7 步按候选集 ZCARD(≈忙 Pod 数,空闲 Pod 被 ZREM 出候选集)与 max_pods 比较量,routing 模式传 `ROUTING_MAX_PODS_SENTINEL=10**9` 旁路(容量治理移交声明集);sc/pc 闸门照常。
- mTLS enforce × routing 启动 raise(证书物料无法注入外部 Pod)。

## 实现

- `config.py`:`_env_deploy_mode` 严格解析 + `deploy_mode` 字段;构造注入(SM/RM orchestrator + RM sweeper ctor,main._bind_modules 接线),config summary 打 `deploy_mode=%s`。
- RM:`RMKeys.scope_declared` + declared CRUD/`declared_scope_ids`(SCAN,同 known_scope_ids 纪律);`LUA_ACQUIRE` 加 HEXISTS 豁免、`LUA_REGISTER` 尾参 external(no_lua.py 同步——注意 fakeredis `hexists` 返回 bool,`to_int(bool)` 会经 str 转换失败回退 0 吞掉真值,须用 `bool()`);`ResourceOrchestrator` 新增 `_external_contract/_adopt_one/register_pods/deregister_pods/adopt_declared`,acquire need_deploy 分支 routing 清占位+MaxPodsReached(红线:占位清理保持),sunset_pending_pods/cleanup 豁免与拒绝;`ResourceSweeper` autoscale 空转/reclaim 剔除 declared/watch 外部跳相位(纯探测)/reconcile 挂 adopt 阶段/`_purge_and_notify` external 跳 k8s.delete(deregister-only——容忍 403 会触发"本拍放弃"卡死解注册)。
- SM:handlers +2(register_pods/deregister_pods,仿 cleanup 委托);route 循环哨兵 + MaxPodsReached 文案模式化;config_store ctor 回调 declared_pods/drop_declared(warn-only,同 push 先例)+ `_soft_remove_stale_pods` 豁免点⑤ + 扩散③ `_drop_declared_or_warn`。
- 观测:`/visualization/scope` 加 deploy_mode + declared 清单(adopted/pending);`_scopes` 摘要 declared_count。
- 部署:`deploy/agent_runtime.template.yaml` env 块 + Role 注释(routing 可不建);外层 jiuwenswarm-ee global_vars/templates/runtime.template.yaml/.env.example 透传。

## 验证

- 单测/集成:666 passed(基线 640 + 新增 26;`uv run pytest`);新增 `tests/integration/test_routing_provision.py` 15 例(注册分流/幂等/校验、超派生 max_pods 分配、池空快失败零 deploy、探测判死→驱逐→同名重建收养→再分配、pending 收养、watch 零 get、注销驱逐零物理删、cleanup 拒绝、refresh 不卡 409 且 Pod 存活、A 类变更候选不摘、scope 删除排空零物理删、哨兵状态层、no-Lua 核心链)+ `test_rm_state_no_lua.py` 4 例(双模式:declared CRUD/ACQUIRE 豁免/external 标记/PURGE 不动声明/has_lagged 豁免)+ `test_config.py` 3 例。
- 全程断言 `k8s.deployed_specs == []` 与 `k8s.deleted == []`(零 K8s 调用等价约束)。
- 已知无关失败:`test_concurrent_route_no_oversell_no_lua` 在本分支与 HEAD 基线同样失败(既有问题,非本特性回归)。
- 真环境验收(SA 零 pods 权限 + StatefulSet/headless service 预拉 + register_pods 全链)待部署环境执行。

## 影响面

- 文档同步:HLD(端点表+2/键表+declared/场景 O)、resource-manager-design(伪码)、spec/{service-core,resource-manager,session-manager}.md、api/config-plane-api.md(§2b)、e2e-test-cases(登记中)。
- 兼容:缺省 full 零行为变化;新键 declared 仅 routing 模式写入;无存量库 schema 变更。
- **存量部署注意**:本地 `deploy/agent_runtime.env` 须补 `AGENT_RUNTIME_DEPLOY_MODE=full`(不补则 render_and_apply.sh 残留 `<<AGENT_RUNTIME_DEPLOY_MODE>>` fail-fast,同 2026-10-health-probe-timeout 先例);外层 jiuwenswarm-ee 有默认值无需手补。
- 限制:gateway/runtime 须可解析/直达注册域名;客户 StatefulSet 端口/路径须与模板一致;mTLS enforce 组合不支持;镜像升级由客户滚动 StatefulSet(走判死→重建→收养)。
