# 删除两级 enabled 字段:禁用 = 删除,生命周期 = 存在性 + expires_at

- 日期:2026-09-18
- 里程碑 / commit:M 维护期(排布于 scope 亲和保持之后)
- 涉及模块:session_manager(config_store/routing/models)、visualization、evaluation;DB 两张表 DROP COLUMN
- 关联 issue:[#154](https://gitcode.com/openJiuwen/agent-runtime/issues/154)(禁用共享模板后引用 scope 暖 Pod 永不收缩);关联 #152(禁用路径 B 语义,载体从开关改为删除)

## 背景与决策

#154 根因:template 级 `enabled` 是半死态——准入侧看它(match_scope 跳过)、暖侧不看(扩散①仍推 min_idle=1)→ autoscale 持续补暖 ∧ reclaim min_idle 底数保护当前版暖 Pod → 死锁,150s(pod_ttl=30 的 5 倍)不收缩。追问定案更进一步:**scope 级 `enabled` 同样冗余**——

1. 快照式替换协议里「off」的自然表达是**载荷缺席**;布尔开关要求发送方每次忠实重申,且自身带病(refresh 对已停 scope 全量重推 min_idle=1 = 重焐热,#154 同族,本次一并修)。
2. 保留理由(可逆性/定义保留)不成立:Manager 是配置真源、每次全量下发,重新组合一条 scope 比重组 template 更便宜;scope 删除零连坐(没有被引用方)。
3. **无真实消费方**:部署种子 JSON 两级 enabled 均不发;Manager Web UI(现存 build)无停用开关;唯一构造 `enabled=false` 的是测试用例。
4. 唯一负担得起生命周期语义的保留字段是 **`expires_at`**(时间性状态,「将来关」无法用缺席表达);`is_active()` 退化为纯过期判定,各面照常消费(含时间性过期的暖侧收敛)。

**「禁用 = 删除」在机制上闭环**(依赖 #152 已实装的删除路径):删 scope 走扩散③(bump + min_idle=0 + 排空窗口,存量会话有界优雅);删 template 被 parse 守卫强制同批处理引用(引用不在本批模板集 → 400)。取代"#154 修复 = 统一生效判定(scope.is_active ∧ template.enabled)"方案——两个维度直接消失,`effective = is_active()`。

## 残留防御语义(迁移安全,两级同款)

载荷项带 `enabled:false` → **视为缺席,从本批剔除 + WARNING**:

- scope 缺席 → 扩散③目标集自动命中 → bump + min_idle=0 + 排空(优雅,零新代码);
- template 缺席:容器引用照收(不因剔除产生「容器未被引用」的误导 400),仅阶段 3 跳过构造 → 引用它的 scope 得到直指问题的 400「引用不在本批模板集」。

不选纯忽略(同 max_pods/message_timeout 未知键先例):会让存量禁用配置**静默重开准入**。`enabled:true` 残留 → 静默忽略(构造层未知键)。

## 实现

- 删 `Template.enabled`(models)+ `RoutingScopeDef.enabled`(routing);`is_active()` 纯过期判定;`parse_scope` 返回 `None` 哨兵表达剔除;match_scope 只跳「模板缺失(悬挂引用)+ 过期」;快照 JSON 随 dataclass 字段自动收敛,旧快照残留键反序列化忽略(向后兼容)。
- config_store:两级白名单/TABLE_DEF/`_COLUMN_OF`/row 读写删字段;`_parse_payload` 两级剔除 + WARNING(模板引用照收);`routing_excluded` 判据收敛为 expr 变化 ∪ expires_at 缩到已过;**refresh ② 修复**:过期 scope 只日落不保温(min_idle=0 + 无 pod_spec)。
- visualization:`_scope` phase 收敛(过期 + 模板悬挂 → disabled);`template_enabled`/`scope_enabled` 键删。
- evaluation:`S-DISABLED-TEMPLATE-REF` finding 随触发条件消失;`ScopeConfigView` 删两字段;`PHASE_DISABLED` 注释更新。

## DB 迁移(2026-09-18 e2e 门禁实测修正:DEFAULT 补齐是发版**前置**,非可选)

**发版前置 ALTER(必须)**:存量 `enabled` 列 NOT NULL——`service_config_template` 的该列当年补列时**漏带 DEFAULT**(e2e PG 实测:新代码 INSERT 不再写该列 → NotNullViolation → config_sync 500 全链雪崩)。发版前对所有存量库执行:

```sql
ALTER TABLE service_config_template ALTER COLUMN enabled SET DEFAULT TRUE;  -- 缺 DEFAULT 的库,必须
ALTER TABLE routing_scope          ALTER COLUMN enabled SET DEFAULT TRUE;  -- 防御(多数库已有)
```

SET DEFAULT 而非立即 DROP:保持旧镜像回滚兼容(旧代码显式读写该列)。e2e PG(30025/agent_runtime)已于 2026-09-18 执行;**联调 MySQL(runtime_wmq)发版时同查同补**——MySQL 语法 `ALTER TABLE service_config_template MODIFY enabled BOOLEAN NOT NULL DEFAULT TRUE;`。

数据前置检查(非空须先人工处置——删模板并处理引用/接受重开准入,否则升级后按启用处理):

```sql
SELECT template_id FROM service_config_template WHERE enabled = false;
SELECT scope_id    FROM routing_scope     WHERE enabled = false;
```

发版稳定后 DROP(框架不 DROP;与 message_timeout 列 DROP 同批执行时一并提醒):

```sql
ALTER TABLE service_config_template DROP COLUMN enabled;   -- MySQL 无 IF EXISTS,重跑报 1091 忽略
ALTER TABLE routing_scope DROP COLUMN enabled;             -- PostgreSQL: DROP COLUMN IF EXISTS enabled;
```

注意:2026-09-routing-scope-enabled-expires.md 中的 `ADD COLUMN enabled` 升级义务自本篇起作废(只补 `expires_at`)。

## 验证

- 全量 555 passed / 9 skipped(基线 554 + 残留回归净增);
- **真环境门禁(2026-09-18,镜像 agent-runtime:drainhold-20260918a,含本篇+排空窗口+亲和保持三提交)**:integration_smoke 真镜像三件套(agentserver 0.0.14s + sandbox 0.0.27s,双容器+全量挂载)**127/127**;多副本 e2e(3 副本 LB+failover)**32/32**。首跑 42/76 暴露上述 DEFAULT 缺口,补 ALTER 后全绿;
- 用例改写:test_routing(match_scope 拆「模板缺失」「过期」两例 + parse 剔除哨兵 + 快照旧键容忍)、test_config_store(scope 生命周期用例改「缺席/过期」口径 + expires_at 翻转自证 + DB 直插行删键)、test_corner_cases(禁用模板用例改为「被引用 → 400 / 无引用 → 剔除 ok」)、test_scope_affinity_hold H2 与 disable 用例自动走删除路径(断言零改动,docstring 更新)、evaluation 测试(make_view 字段收敛 + disabled finding 用例反转);
- 集群校验免跑:不动键名/Lua/选主(快照 JSON 字段属 STRING 值域,与槽位无关);
- e2e 冒烟随发版门禁(与 #151/#152/#153 同批)。

## 影响面与对外口径

- 契约:HLD 字段表/匹配语义/载荷示例删 enabled;Manager 侧知会停止发送(残留防御已兜底,无兼容性风险);
- 行为变化:①「禁用」操作在 runtime 消失——等价表达 = 从载荷删除(优雅排空);② 存量 `enabled=false` 行升级后当启用(前置检查兜底);③ refresh 不再把过期 scope 的池重新焐热;
- 测试重校准(#154 case-05、#152 case-10/11):改用「删除」复验;原 `enabled=false` 载荷形状得到剔除+WARNING(scope)或 400(template 被引用)的明确迁移信号;
- `expires_at` 保留(计划性停用);scope 过期/删除均停保温 + 排空。
