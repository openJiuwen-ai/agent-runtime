# CLAUDE.md

本仓库实现**会话编排服务**(旁路式:gateway 直连 AgentServer Pod,服务只做控制面)。**M0–M6 已全部完成**(含 server 模式真环境验收),维护期。

## 文档体系与开发前必读(路径均相对本模块根)

文档三分:`docs/design/`(设计论证与 Lua 全文)、`docs/spec/`(**模块规格,AI 向**:代码在哪/怎么协作/改哪里)、`docs/feature/`(改动史)。

**文档同步义务(与代码改动同一提交完成):**
- 改动涉及 spec 覆盖的内容(模块行为 / Redis 键 / Lua / 接口 / 配置 / 错误码)→ **同步更新对应 spec 文档**(`docs/spec/` 按模块对号入座)。
- **较大改动**(新功能 / 行为变化 / 重构 / 里程碑)→ 按 `docs/feature/_TEMPLATE.md` 新建一份记录并登记其 README 索引;**小的修复(局部 bugfix、注释/文案)不用**。

1. `docs/spec/README.md` —— spec 索引 + 一页纸架构(先读)
2. `docs/spec/session-manager.md` / `docs/spec/resource-manager.md` / `docs/spec/service-core.md` —— 三模块规格(改代码前读对应模块);e2e 用例逐条说明见 `docs/spec/e2e-test-cases.md`(场景/输入/预期输出)
3. `docs/design/Agent-Runtime-HLD.md` —— 架构总览 / 接口契约 / 场景 A–N / Redis 键表(**语义权威,冲突以它为准**;§9 实现与验收状态)
4. `docs/design/session-manager-design.md` —— SM 详细设计(7 个 Lua 全文)
5. `docs/design/resource-manager-design.md` —— RM 详细设计(6 个 Lua 全文)

## 红线规则(违反即返工)


## 测试

```bash
cd applications/agent_runtime
uv sync --extra local
uv run pytest                 # 483 个用例:状态层 Lua(含 EVICT/TOUCH/ROUTE_PLACE 残骸自卫) / 路由匹配纯函数(含表达式解析) / config 层(含三段式契约与容器表水合、config_refresh 强制刷新、写库单事务+锁看门狗) / 容器规范形(container_spec) / envFrom / 组件全链路(含 sidecars 多容器与卷挂载) / HTTP 冒烟 / corner case / 双实例多副本 / 停机韧性 / 审计实锤回归(test_audit_repro) / 强制刷新自然老化(test_force_refresh) / 健壮性故障注入(test_k8s_io_timeouts / test_infra_faults / test_main_lifecycle,见 docs/feature/2026-09-robustness-hardening.md) / 系统自评估(tests/evaluation/:规则引擎+LLM 降级+采样报告全链路,见 docs/spec/evaluation.md)
```

- 构造 `ServiceManager` 必须传 `deploy_mode="subprocess"`(默认 k8s 会挂死测试)。
- fakeredis 陷阱:消费组 id=`"0"` bug;所有门面共享同一 client;EVAL(Lua)依赖 lupa,缺失自动 skip。
- 双实例测试(`tests/integration/test_multi_replica.py` + `_dual_harness.py`):同进程两 App 共享一组 fakeredis/SQLite/FakeK8s,httpx ASGITransport 单事件循环驱动;lifespan 必须先手动驱动(否则 RestAdapter 惰性二建 sysctx 绕过后台 Job)。
- 旧 SDK 已知失败用例(非回归)见外层 jiuwenclaw 仓库 CLAUDE.md 末尾清单。

### 集成冒烟(真环境,部署后回归)

```bash
cd applications/agent_runtime
./scripts/integration_smoke.sh     # HLD 场景 A–L 端到端;会 FLUSHDB 目标 Redis DB(有防误刷保护)
```

- 参数/前置见 `--help` 与 README;场景 N 待 AgentServer 支持 `GET /health` 后补验。
- **发布门禁:发版前必须用真实 AgentServer 镜像跑一遍**(2026-08-26 教训:influxdb 替身与代码共享同一契约假设,探测路径/env/路径前缀类缺陷在替身世界里不可见):
  `./scripts/integration_smoke.sh --image swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-amd64:<tag> --sidecar-image swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-sandbox-amd64:<tag> --health-path /api/v1/health --sse-path /api/v1/events/stream --agent-env '{"AGENT_HTTP_ENABLED":"true","AGENT_HTTP_HOST":"0.0.0.0","AGENT_HTTP_PORT":"8086"}' --with-sidecar --with-mounts`
  (冒烟脚本参数直通 e2e_hld_acceptance.py;替身 influxdb:1.8 仅用于快速回归,默认契约 /health+8086;真镜像门禁必须带三件套契约参数——脚本模板的 health_path/sse_path/agent_env 由这三个参数注入,漏带则 readiness 永不通过、阶段 2 起全红;`--with-sidecar --sidecar-image` 开双真镜像 sidecar 全规格,`--with-mounts` 开全量真实规格阶段——主容器 cm/hp/pvc 三挂载 + PVC 静态预置 + 逐字段断言,2026-08-28 起为门禁标配)
- 冒烟内置回归网:阶段 1b(无请求预热)、阶段 5b(自然老化零回拨)、阶段 11b(内部不变量巡检:idle⊆pods:all / idle_since 存在 / 静息 deploying=0 / 快照 deploy_ver==RM cfg)——2026-08-26 真环境实测缺陷①②④⑤的固化。
- 经多副本 LB 亦可跑(实测 65/65);历史排查实录见 `docs/spec/e2e-test-cases.md` §8.1。
- cleanup 空目标必须用**无匹配 label_selector**,不得指向业务 ns(同 label 真实 AgentServer 会被误删)或不存在的 ns(in-cluster SA 对其 403 而非空列表)。

### 多副本(真环境)

```bash
# 宿主机双进程(快速):8091/8092 共享 Redis/DB,选主键互斥竞争
./scripts/deploy_replicas.sh 2 .env.production.local 8091

# ---- 镜像构建(K8s 部署前置;build context=仓库根,Dockerfile 内 COPY foundation/ service/)----
./deploy/build_image.sh agent-runtime:<新tag>                # 本地 tag
./deploy/build_image.sh swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agent-runtime-amd64:0.0.1 --push    # 推 SWR
# 本地 tag 无仓库可拉,而模板 imagePullPolicy=IfNotPresent → 必须分发到**每个可调度节点**
# (双节点都要:ecs-38b3-0001=192.168.1.64、ecs-38b3-0002=192.168.1.90 本机;反亲和会把副本分散过去):
docker save agent-runtime:<tag> | ssh root@192.168.1.64 docker load    # 另一节点;本机 docker load 同理
# 然后改 deploy/agent_runtime.env 的 AGENT_RUNTIME_IMAGE=<新tag>(每次构建换新 tag——同 tag 节点不会重拉)

# K8s 多副本 + Service LB(生产形态):deploy/ 目录(模板+Dockerfile+渲染部署)
./deploy/render_and_apply.sh deploy/agent_runtime.env --nodeport
# 多副本 e2e(真 LB 单入口,含 failover;单实例自动 DEGRADED)
uv run --no-sync python scripts/e2e_multi_replica.py --base-url http://127.0.0.1:30091/api/session \
    --redis-url redis://127.0.0.1:30001/2 --namespace agent-runtime-e2e
# 压测/浸泡(零依赖,场景化;无 FLUSHDB、不动 cleanup 端点)
uv run --no-sync python scripts/load_test.py --base-url http://127.0.0.1:30091/api/session --duration 60
```

## 环境

- Python 3.11–3.13;Redis 须开 AOF/RDB(单实例/哨兵 `redis://`,**Redis Cluster 用 `redis+cluster://`**,键前缀已 hash tag 同槽化,见 `docs/feature/2026-08-redis-cluster.md`;cluster 无库号,URL 禁带 `/N`);DB 用 MySQL/PostgreSQL(禁 SQLite 回退——server 模式;local 模式调试可用)。
- **存量库升级:历次 schema 变更须先按 `docs/feature/` 对应篇目手工 ALTER 再发版**(框架建表只 create_all 不补列)——容器表拆分三列(`2026-08-container-table-split.md`)、routing_scope `enabled`/`expires_at`(`2026-09-routing-scope-enabled-expires.md`)、模板表策略四列 RENAME(`2026-09-template-table-runtime-terms.md`);具体 SQL 见各篇。
- 框架:`service/openjiuwen_runtime/service`(App/Envelope/SystemContext);App 范式参考 `applications/echo/echo_server.py`(**不是** a2a_service)。
- 部署:`applications/agent_runtime/scripts/deploy.sh local|server`(server 读 `.env.production.local`)。
- 本仓库是嵌套 git 仓库(独立于外层 jiuwenclaw),提交分开做。
