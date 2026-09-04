# 场景 F 快失败:拆除有界等待队列(scope 满 → 立即 503 SCOPE_FULL)

- 日期:2026-09-04
- 涉及模块:session_manager / service-core(errors/config/visualization/metrics)/ 测试 / e2e 脚本 / 部署 / 文档

## 背景与动机

排查外部报告「redis-py 的 RedisCluster 不支持 pubsub」是否影响本服务,实锤结论:**是,且必炸**。

- redis-py 7.1.0 实测:`redis.asyncio.cluster.RedisCluster` **整模块没有任何 pubsub 实现**
  (同步版 `redis.cluster.RedisCluster` 有,异步版 0 处)。
- `service/openjiuwen_runtime/service/bootstrap.py` 对 `redis+cluster://` 恰好构造异步集群客户端;
  `orchestrator._wait_for_capacity` 里 `self.state.redis.pubsub()` 是 src 下唯一 pubsub 调用点
  ——真集群部署下场景 F(scope 满 → 有界等待)一进来就 `AttributeError`,等待请求 500,
  `finally` 里 `pubsub.unsubscribe` 再炸一次。
- 既有验证矩阵恰好全部漏过这个组合:`verify_redis_cluster.py`(19 项)无任何 pubsub 检查;
  真环境 e2e 全用单实例 `redis://…/2`(带库号,不可能是 cluster);单测/集成用 fakeredis(有 pubsub)。
- 另一重考量:本服务是旁路控制面,过载背压的退避责任本就在 gateway(SM 设计 §8.3 指数退避
  + full jitter 契约不变);服务端排队等待占用连接/协程/pubsub 订阅,与控制面定位不匹配。

## 方案

- **scope 满 → 立即 503 `SCOPE_FULL`(带 retry_after=1)**,不排队、不订阅、拒绝路径零额外
  Redis 写入;Lua 闸门(ROUTE_PLACE)即唯一仲裁,被拒者毫秒级返回。——「确认过」
- **新错误码 `SCOPE_FULL` 替代并删除 `SCOPE_FULL_TIMEOUT`(504)与 `SCOPE_QUEUE_FULL`(503)
  两码**;504 自此从错误码表消失,gateway 契约按 error_code 识别可重试(读码不读状态码,
  §8.1 信号表已同步)。——「确认过」
- **等待机制整体拆除,不留死代码**:waiter ZSET 键、free pubsub 通道、`LUA_WAITER_GATE`
  脚本、state 层 4 个 waiter 方法、3 处 Lua `PUBLISH :free`、visualization 的 `waiters`
  字段、`AGENT_RUNTIME_SCOPE_FULL_TIMEOUT` 配置全链(src/deploy/.env/scripts)。——「确认过」
- route 总预算保留但重推导:原 `scope_full_timeout + ready_timeout + 10s 余量` 中队列分量
  删除,改为 **`ready_timeout + 10s 余量`**(总预算封的是 need_acquire 冷部署循环,与等待
  无关);超限错误由 504 ScopeFullTimeout 改为 **503 `NO_POD_AVAILABLE`**(acquire 侧
  粗化惯例,与 MaxPodsReached/DeployFailed 映射一致,WARNING 留真因)。
- **被否备选:优雅退化纯轮询**(检测客户端无 `pubsub` 方法时退化 ≤500ms 轮询重仲裁)——
  否决理由:500ms 轮询唤醒延迟仍不达标,且为低频过载场景保留 waiters ZSET/闸门/崩溃自清
  整套复杂度不值;gateway 已有指数退避,快失败语义更简单更可预测。

## 实现

- `errors.py`:删两旧码/两旧类;新增 `SCOPE_FULL`(503)+ `ScopeFull`;`DEFAULT_RETRY_AFTER`
  从 orchestrator 移入错误契约模块。
- `session_manager/orchestrator.py`:route 的 `scope_full` 分支改立即 `raise ScopeFull`
  (DEBUG 留痕——metrics 中间件已有带 error_code 的每请求 INFO,过载风暴下不双份刷屏);
  删 `_wait_for_capacity` 全文(唯一 pubsub 调用点随之消失)、`scope_full_timeout`
  参数/默认值/`deadline`;总预算推导与超限错误按上述方案改。**while 主循环保留**——
  `need_acquire` 重跑与 sse 缺失重试两个重入口仍在。
- `session_manager/lua_scripts.py`:删 `LUA_WAITER_GATE`;删 ROUTE_PLACE(惰性回收)/
  EVICT/TOUCH(惰性驱逐)3 处 `PUBLISH :free`(脚本内拼通道名,KEYS/ARGV 账目不受影响,
  `eval` 的 KEYS[1] 路由锚不变);清单 7→6 个。
- `session_manager/state.py`:删 `SMKeys.scope_waiters`/`scope_free_channel` 与
  `waiter_count`/`try_add_waiter`/`add_waiter`/`remove_waiter`;`prefix` 属性保留
  (EVAL 路由锚承重,仅改注释);`route_place` 空 EVAL 兜底仍 fail-closed 返回
  `scope_full`——对外表现从「进等待」变为「立即 503」,eval 层 WARNING 留痕不变。
- `config.py` 删字段与 `_env_float`(唯一调用方);`main.py` 删接线与启动日志项;
  `visualization_api.py` 删 `waiters` 字段与 config 载荷项;`metrics.py` 慢分诊注释
  收窄为冷部署。
- 部署:`deploy/agent_runtime.env`、`env.example`、`template.yaml`(含红线注释)、
  `.env.production.local`/`.env.production.pg.local` 全部删该 env;`rendered/` 重渲染。
- RM 侧零改动(follower 等待室是独立机制,纯 asyncio 轮询);仅 RM lua_scripts 注释里
  「同 LUA_WAITER_GATE 纪律」改为自洽措辞。

## 验证

- 单测:442 → **433 全过**(删 10 个围绕等待机制的用例:F 队列 4 个/C7/C9/
  touch 唤醒/收尾保护/跨副本唤醒/惰性 PUBLISH;新增 1 个拆除净空回归
  `test_teardown_clean_no_waiter_or_free_keys`;改写 `test_route_scope_full_at_max_pods_fast_fail`
  断言 <1s 快失败 + retry_after + 503 契约 + 无 waiters 键;总预算用例改
  NoPodAvailable + 新推导式)。
- e2e 脚本:阶段 7 改「串行占满 + 5 并发(2 亲和续期 + 3 新)= 恰好 2×200 + 3×503
  SCOPE_FULL,<1s」——**用亲和续期凑 200 是刻意的设计**:并发冷 `need_acquire` 输家
  在 pc=1 下得 NO_POD_AVAILABLE 而非 SCOPE_FULL,纯新会话突发断言不了确定分布;
  `e2e_multi_replica` S4 同型(2×200 + 6×503);`load_test` queued 预期改 SCOPE_FULL、
  删「每排队请求持一条 pubsub 连线注意 maxclients」注;`verify_redis_cluster`
  删等待队列检查项(16→15,计数动态打印),真 3 主 cluster 实测 **15/15**。
- 真环境(2026-09-04 实测):
  - **真镜像门禁 122/122 全过**(AgentServer/sandbox `0.0.12s` 三件套 +
    `--with-sidecar --with-mounts`,PG 落库校验,ns agent-runtime-e2e-wmq,
    2 副本经 NodePort LB,镜像 `agent-runtime:scopefull-20260904`)。阶段 7 新断言
    确定性通过:2×200(亲和续期)+ 3×503 SCOPE_FULL、**快失败 0.02s**、retry_after
    全带、无 waiters 键。计数对账:上次门禁 126 = 本树 122 − 阶段 7 新增 1 项 +
    develop 收官的 13c 自评估五项(develop1 无该特性,非回归)。
  - **verify_redis_cluster 15/15**(本机 docker 起 3 主 cluster,redis+cluster://;
    编辑过的 route_place/touch/evict Lua 在真集群通过;删项前为 16)。

## 影响面

- 文档同步(同提交):HLD(错误枚举/过载参数表/§4.1.1 流程图/键表/场景 F 重写/场景 D,G
  去 PUBLISH/§7.3 重写/§9 注记 + 新增 §9.2)、SM 设计(脚本全集 7→6、EVICT 全文去
  PUBLISH、§5.2 伪代码、§7 错误与参数、§8.1 信号表、§8.2 重写、§8.6 BLPOP 项删、
  §12/§13)、spec README/session-manager/service-core/e2e-test-cases(阶段 7/S4/§5.1
  重排/§5.2 C7C9 退役/§8.1 历史化/§8.3 措辞)、模块 README/CLAUDE.md(计数 433、
  fakeredis 陷阱行、LB 前提段)。
- **取代关系**:`2026-08-redis-cluster.md` 的「PUBLISH 在 cluster 为全节点广播,
  等待-唤醒功能不变」断言随机制拆除失效(该断言当时也只是宣称,未实测——见上背景);
  `2026-08-27-audit-e2e-repro-fixes.md` 修复项 1(waiters deadline 化)/2(等待循环
  重仲裁)superseded,缺陷面已不存在(C9 的 deploy 占位 deadline 半边在 RM 侧保留)。
- 兼容性:存量部署残留 `AGENT_RUNTIME_SCOPE_FULL_TIMEOUT` env **无害**(不再读取);
  残留 `{session_manager}:scope:{sid}:waiters` ZSET(无 TTL)同样无害(无任何读写方),
  可选一次性清理:`redis-cli --scan --pattern '{session_manager}:scope:*:waiters' |
  xargs -n100 redis-cli DEL`(键带 hash tag 单 slot,cluster 下同样适用)。
- gateway:识别新码 `SCOPE_FULL`(503,可重试,带 retry_after);不再出现 504。
- 后续(部署侧):wmq 联调环境(hostPath 直挂工作树)重启即生效——复跑
  `integration_smoke.sh`(阶段 7 新断言)、`e2e_multi_replica.py`(S4 新预期)、
  `verify_redis_cluster.py`(18 项)。
