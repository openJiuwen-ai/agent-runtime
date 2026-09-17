# scope 亲和保持:规则变更不即时迁移有效旧会话 + 路由性排除日落

- 日期:2026-09-17
- 里程碑 / commit:M 维护期(排布于同日日落排空窗口之后,依赖其排空纪元)
- 涉及模块:session_manager(lua_scripts ROUTE_PLACE / orchestrator route / config_store)、resource_manager 注释、e2e 脚本兼容性待发版冒烟
- 关联 issue:[#152](https://gitcode.com/openJiuwen/agent-runtime/issues/152)(0.0.27s 回归:优先级重排/禁用后旧 session 随新规则迁移);关联 #150/#151(日落硬切,已由排空窗口修复)

## 背景与根因

#152 回归:scope 优先级重排(team.index 20→5)或禁用模板后,未到期旧 session 被立即迁到新规则选出的池。根因与 #151(日落硬切)不同源,**Pod 全程没死,是路由器自己改判了归属**:

- `orchestrator.route()` 每次请求先用当前快照重算 first-fit(resolve);
- `LUA_ROUTE_PLACE` 亲和续期要求「新算出的 scope == 绑定 scope」,不等则内联 EVICT + 重放置进新 scope。

重算规则服务于新放置是对的,错在让它参与仲裁存量绑定。

## 方案(用户拍板)

统一规则:**亲和只认「绑定 + Pod 存活」;路由性排除变更经 gen bump 走排空窗口有界回收**。

| 变更 | 旧会话行为 |
|---|---|
| 优先级重排(原 scope 仍命中) | 完全不迁移——原 scope 不日落,亲和 while-valid 持续(路径 A 零打扰) |
| 禁用 / 失权(expr 变化)/ 删 scope | 与 #151 排空窗口同一哲学:新会话立即走新规则;旧会话窗口内继续原池,bump gen → 日落 → drain_until;过窗回收后 rebind 落新规则 |
| Pod 死亡(任何原因) | 立即按新规则重放置(既有语义,rebind 化) |
| 同 session 换身份、配置不变 | 保持原绑定(测试口径"行为保持至 TTL";会话归属是 gateway 职责) |

诚实边界:reclaim 只遍历 idle Pod,busy Pod 不强杀——排空窗口的真实保证是「会话停止活跃 session_ttl 后 Pod 即回收」,持续活跃会话不被打断,与 #151 排空窗口逐字相同(彼处 feature 文档"过窗硬切"指 Pod 转闲置即收)。

## 实现

- **LUA_ROUTE_PLACE 重构分支 3/4**:三条件命中(scope 匹配 ∧ 未过期 ∧ pod:info 存在)才 refresh;其余(过期/Pod 注册消失/scope 不匹配竞态)内联 EVICT 后返回新 action `rebind`——**曾见绑定的调用内不再落放置**,杜绝向已禁用/删除 scope 重部署(Python TOCTOU 由 Lua 原子判定消除)。
- **route() 编排**:resolve 后读 `session:{sid}`(已有 HGETALL),有绑定则仲裁目标改为绑定 scope;session_ttl 取 `ConfigStore.scope_template()`(新 helper,快照缓存直查,不判 enabled/expires_at——持有不重算规则);scope/模板已从快照消失则回退哈希留存的 session_ttl。`rebind` → 切回 first-fit 重试(total_deadline 兜底)。need_acquire 处加防御归位(hash 被并发 DEL 的缝隙,防把 resolved 模板 pod_spec 推进持有 scope 的 RM 配置)。
- **config_sync 路由性排除日落**:diff 增 `routing_excluded` = expr 变化 ∪ 生效→失效(is_active);**不含 index 重排**(不排除命中)与引用切换(既有 ver 日落)。rebuild_snapshot 后、扩散① push 前:逐 scope `_bump_or_warn`(新,失败 warn 不 raise——DB 已提交,丢 bump 退化为软界)+ `_soft_remove_all_pods`(防 drain 期新会话 first-fit 落上老代 Pod)。**时序红线 bump 先于 push**——RM update_pool_config 凭 gen lag 戳排空纪元,反序则永远不戳(drain_until 非空是时序正确的唯一可观测证据)。
- **删除 scope(扩散③)**:本批新删(∈old_scopes)先 bump 再推 min_idle=0(仅首删一次;rm_known 陈年旧删每拍只重推不重复 bump);无旧模板兜底字典补 session_ttl(纪元计窗依赖)。
- 409 闸门**不改**:sync 守卫按版本(对 gen lag 失明)与 R3/D3 断言一致——路由性排空的 sync 期间可再 sync(二次 bump 不重戳纪元,首因定窗)。
- 响应增 `routing_sunset` 字段(可观测)。

## 验证

- 全量 554 passed / 9 skipped(基线 542 + 12 新增)。
- 单测:`test_sm_state` rebind 三态(过期/Pod 死/竞态不匹配)+ 过期用例语义反转;`test_config_store` 增 5 例(重排零打扰含路由断言 / expr 变化 bump+纪元+幂等 / 禁用 bump+兜底 / 删除 bump 一次+载荷含 session_ttl / B 类参数不 bump)。
- 集成:`test_scope_affinity_hold.py` H1-H5(重排零打扰 / 禁用→排空→过窗回收→rebind 落兜底 / 失权软摘 / 删除日落 / rebind 回归);`test_corner_cases` 跨 scope 迁移用例**反转为亲和保持**。
- verify_redis_cluster.py(动 Lua 红线)SM 段补 rebind 路径检查;真三主 cluster 复跑(见下)。
- 真环境 integration_smoke.sh 随发版门禁跑(与排空窗口同批)。

## 影响面

- spec 同步:session-manager(route 流程/Lua 表/config_sync 编排)、resource-manager(bump_generation 调用方/update_pool_config 戳记条件/reclaim 删除段);design/session-manager-design.md ROUTE_PLACE 伪码与调用方流程同步。
- 行为变化对外可见:① 规则变更不再即时迁移存量会话(对话连续性);② 禁用/失权后旧会话的旧池访问至多延续 session_ttl(窗口 = 变更时刻 + session_ttl);③ route 对「曾见绑定」的调用多一次往返(rebind 后重试,罕见路径)。
- 已知限(接受并留痕):a) DB 提交后崩溃窗口丢 bump → 该 scope 退化为软界(会话自然结束回收),同扩散① push_or_warn 先例;b) expires_at 时间性过期(无 sync 事件)不触发 bump,软界——会话停止活跃后 Pod 按 pod_ttl 收敛;c) 排空期内新会话不落老代 Pod(候选软摘),但持有旧会话的 Pod 在窗口内被 autoscale 排除在 warm 底数外(既有日落语义)。

## 取代关系

2026-09-sunset-drain-window.md 的排空机制(纪元/surge/回收闸门)零改动复用;本篇把「谁触发日落」从 ver(refresh/A 类)扩展到 gen(路由性排除)。
