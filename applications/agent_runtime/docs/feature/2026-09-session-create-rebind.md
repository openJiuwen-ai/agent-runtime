# session.create 临时 key 原子改绑(修复双计数与亲和断裂)

- 日期:2026-09-20
- 涉及模块:session_manager / 测试 / 文档(gateway 侧配套改动在 swarm 仓 EE 包)

## 背景与动机

ns wmq 现网实证(gateway 19002 + Redis db2 断言):gateway 的 `session.create` 以**临时 key**
route 占槽——web WS 主路径 `sess_<ts>_<rand>`、HTTP `bind_http_session` 的 `webhttp_<uuid>`、
TUI/cron/fallback 空 id 时 `identity_from_envelope` 的 `{group}:{bot}:{user}` 组合串——
AgentServer 返回真实 session id 后,首条 `chat.send` 按真实 id 再次 route:

- **双计数**:`SCARD scope:{scope}:sessions` 多出临时 key 一个成员,残留至 `session_ttl`
  (默认 60s)过期才释放。实测一次 create 后集合里同时存在 `web_impltest2_*`(真实)与
  `webhttp_543f23f0f3ad`(临时)。
- **亲和断裂**:真实 id 是全新 placement(first-fit 按接入序),与 create 落点无关;而
  会话 metadata.json 已写在 create 实际服务的 Pod 上——chat.send 落别的 Pod 即隐式重建,
  项目绑定等首次锁定字段丢失。

架构约束:runtime 是 bypass 控制面(响应不过境)→ "runtime 拦响应改绑"不可行;runtime
原无按 session 驱逐/改绑 API。

## 方案

- **gateway 触发 + runtime 原子改绑**(与需求方确认过):`RuntimeRoutedAgentClient` 是唯一
  同时知道临时 key(route 身份)与真实 id(create 响应 payload)、且控制响应时序的地方。
  create 响应返回上层之前先 `await rebind(from, to)`——竞态封死,客户端拿到真实 id 时
  改绑已完成,chat.send 不可能抢先。
- **to 空 = 驱逐**:create 失败(如 BAD_REQUEST)也调 rebind,立即释放槽位,消掉 60s 残留。
- **幂等由 Lua 语义保证**,不走 request_id 结果缓存:from 不存在→noop;to 已有绑定→
  overtaken(首条 chat.send 抢先放置,保留 to 的生效绑定仅清 from);重放无副作用,且
  避免缓存写失败把已生效改绑变成错误响应。
- **from 已过期未被 sweeper 收走仍搬移**:慢 create(pod 冷启动 12s+create)下亲和仍成立;
  sweeper 先收走则 noop(真实 id 由首条 chat.send 全新放置,不劣于现状)。
- **被否方案**:gateway 短路 session.create(本地生成 id + chat.send 隐式创建)。隐式创建
  链路已实测可用(全新 id 直接 chat.send 全流程通,单槽位、metadata 落对 Pod),但需迁移
  id 生成/project 校验/create_token 幂等 + chat.send 绑定注入,prewarm/team/cron 语义搬家,
  改动与测试面大——留作后续演进。否决主因:契约变化大 vs rebind 零契约变化。
- 已知限制(接受):create 进行期间临时 key 仍占槽(实测 0.24s,冷启动时≈pod 启动+create),
  vs 修复前恒 60s;同身份并发 create 共享组合槽时第二个 rebind 落 noop(不劣于现状)。

## 实现

- `session_manager/lua_scripts.py`:`LUA_REBIND`(第 7 个 Lua)——四处不变量整体 from→to
  (SADD/SREM 两集合、HASH 重建、ZSET 换名),TTL 刷新(session_ttl 就地读 from 哈希),
  复用 Pod 清 idle_notified(同 ROUTE_PLACE 提交步);返回 action ∈
  noop/rubble/evicted/overtaken/rebound。所有键共享 `{session_manager}` hash tag,cluster
  同槽原子合法(KEYS[1] 路由锚约定沿用 `SessionState.eval`)。
- `state.py`:`SessionState.rebind(from, to, now, default_ttl)`,eval 空返回兜底 noop
  (fail-safe,对齐 evict);rubble 留痕 WARNING。
- `orchestrator.py`:`SessionOrchestrator.rebind(request_id, from, to)`——from/to 过
  `key_unsafe` 同槽性校验(均进键名),to 空=驱逐;INFO 留痕(生命周期事件)。
- `handlers.py`:`POST /api/session/rebind`(第 6 个端点),`metadata.session_id`=from、
  `rawdata.to`=真实 id;`_INFRA_EXCEPTIONS` → 503 姿态与其余端点一致。
- gateway 侧(swarm 仓 dev-stable,EE 包):`RuntimeSessionRouteClient.rebind` +
  `RuntimeRoutedAgentClient.send_request` 钩子(rebind 先于响应返回;超时 ~2s 可经
  `GATEWAY_RUNTIME_SESSION_REBIND_TIMEOUT` 调整;异常降级=现状)。无开关——降级路径
  已保证旧 runtime 滚动期行为不劣于现状,回滚即回退 gateway 镜像。

## 验证

- 单测(pytest **573 passed, 9 skipped**,基线 508+9 skip 后历次演进):
  - `test_sm_state.py` LUA_REBIND 语义矩阵 8 例:四处搬移+TTL 刷新 / noop(from 缺失、
    to==from)/ 驱逐 / overtaken 保留 to / dead-pod 退化为驱逐 / 残骸自卫 / 过期未扫复活
    搬移 / session_ttl 就地读(120 非 60)。
  - `test_route_flow.py` 回归 4 例:临时 key→真实 id 单槽位+同 Pod 亲和(ns wmq 实证缺陷
    的复现用例)/ 幂等重放 / 驱逐释放额度(sc=1 顶死后新会话立即可 route)/ InvalidParams
    (from 空、from/to 含花括号)。
  - `test_http_smoke.py`:端点契约(rebound→重放 noop→真实 id touch 保活→缺 from 400
    VALIDATION)。
- 真环境:`verify_redis_cluster.py` **27/27**(新增 [3c] rebind 搬移+真实 id touch 两步,
  3 节点真 cluster,`--wipe`)。
- 现网实证(ns wmq,修复前对照):create 后 `scope:default:sessions` 含临时 key 残留、
  隐式创建单槽位正确——见「背景与动机」;修复后 e2e 断言(scopes=={真实 id}、亲和、失败
  驱逐、时序)在 gateway 侧钩子合入后执行(计划项)。

## 影响面

- 文档:HLD 无键表变化(无新键);`docs/design/session-manager-design.md` §2.2b(接口)+
  §5.1(LUA_REBIND 全文,脚本全集 6→7);`docs/spec/session-manager.md`(端点 5→6、Lua 6→7、
  rebind 参数校验例外);本篇。
- 兼容性:无新 Redis 键、无 DB schema 变化;上线顺序 **runtime 先发版**(新端点),gateway
  钩子后发——旧 gateway 不调 rebind = 现状;反向(新 gateway + 旧 runtime)时 rebind 404
  走降级路径(节流告警 + 临时 key 等 TTL),回滚 = 回退 gateway 镜像。
- 遗留:gateway 侧钩子实现(swarm 仓)与 e2e 计划项(6 项)见本仓 plan;组合 key 并发共享
  的第二会话靠首条 chat 重新放置(记录在案,不劣于现状)。
