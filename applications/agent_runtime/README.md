# agent-runtime（会话编排服务）

旁路式会话编排服务：gateway 拿到 Pod SSE 地址后**直连** AgentServer Pod 收发，
本服务只在控制面（准入 / 路由亲和 / TTL 老化 / Pod 生命周期），不在数据通路上。

- **一个 App**（`/api/session`，端口 8091），两个模块：
  - `session_manager`：route / touch / config_sync / cleanup 四个对外端点
  - `resource_manager`：无 App/端口/prefix，纯进程内 Facade + 后台任务
- 两模块共享同一 Redis（前缀 `session_manager:` / `resource_manager:`）与同一
  DB（`service_config_template` / `routing_rule` 表）；跨模块只走 Facade，
  不直读对方 Redis key。
- 设计文档：`docs/design/`（语义权威 = HLD；冲突以 HLD 为准）。
- 代码说明：`docs/agent-runtime-code-guide.md`（模块结构 / 关键流程 / Lua 清单 / 测试与部署）。

## 运行

```bash
# local（fakeredis + SQLite + FakeK8s，仅供开发调试）
cp agent_runtime.local.env.example .env.development.local
./scripts/deploy.sh local

# server（真 Redis + MySQL + K8s）
cp agent_runtime.server.env.example .env.production.local
# 编辑 .env.production.local（Redis 须开 AOF/RDB；DB 用 MySQL）
./scripts/deploy.sh server
```

## 测试

> 全部 e2e 用例(场景/输入/预期输出)逐条说明:`docs/e2e-test-cases.md`。

```bash
cd applications/agent_runtime
uv sync --extra local      # 含 dev 组（fakeredis[lua]/pytest）
uv run pytest              # 114 个用例：状态层 Lua / config 层 / 组件全链路 / HTTP 冒烟 / 双实例多副本
```

### 集成冒烟测试（真环境，M6 用例固化）

服务以 server 模式启动后，对 HLD §6 场景 A–L 做端到端回归（真 deploy/删除 Pod、
真实 TTL 老化、并发队列；场景 N 待 AgentServer 支持 `GET /health` 后补验）：

```bash
cd applications/agent_runtime
./scripts/integration_smoke.sh          # 或：uv run --no-sync python scripts/e2e_hld_acceptance.py
./scripts/integration_smoke.sh --help   # 服务地址/Redis/命名空间/镜像/DB 均可覆盖
```

- 前置自检：服务在线、Redis AOF、kubectl、专用命名空间（缺则自动建）。
- **会 FLUSHDB 目标 Redis DB**（干净起点）：检测到非本服务前缀的外来 key 即中止
  （`--force-flush` 才放行），务必用独立 DB 编号。
- 退出码：0 全过 / 1 有失败 / 2 前置自检未过，可直接接入 CI。
- 经多副本 LB 亦可跑（实测 65/65）——前提：部署带
  `AGENT_RUNTIME_SCOPE_FULL_TIMEOUT`（deploy 模板已默认 8，须显著小于模板
  session_ttl，否则等待者 deadline 与会话到期碰撞产生混合结果）。
  排查实录与 cleanup 空目标的三个坑见 `docs/e2e-test-cases.md` §8.1。

## 多副本部署与测试（M7）

服务按设计是多副本无状态（态全在 Redis/DB、后台任务 Redis 选主全局单副本执行）。

### 进程内双实例测试（日常 pytest，离线）

`tests/integration/test_multi_replica.py`（12 用例）：同进程两个完整 App 共享
同一 fakeredis/SQLite/FakeK8s，确定性验证跨副本语义——准入闸门全局不超收、
deploy 锁窗口零重叠、PubSub 跨副本唤醒、幂等跨副本重放、配置失效传播、
每 (job,epoch) 恒一选主、sweeper 互斥。随 `uv run pytest` 一起跑。

### 宿主机多进程（快速联调）

```bash
./scripts/deploy_replicas.sh 2 .env.production.local 8091
# → 8091/8092 双进程就绪（/healthz 可查 instance_id），
#   Redis agent_runtime:job:* 选主键可见双实例互斥竞争；Ctrl-C 全清理
```

### K8s 多副本 + Service LB（生产形态，deploy/ 目录）

```bash
./deploy/build_image.sh <swr-tag> --push       # 构建镜像（上下文=仓库根）
cp deploy/agent_runtime.env.example deploy/agent_runtime.env && vi deploy/agent_runtime.env
./deploy/render_and_apply.sh deploy/agent_runtime.env --nodeport
# → Deployment(replicas=N, 反亲和, /healthz 探针, SA+RBAC×2) + ClusterIP Service(LB)
#   + NodePort(默认 30091，集群外入口)
```

红线：副本数只改 Deployment `replicas`；`OPENJIUWEN_SERVICE_DEPLOY_REPLICAS`
必须保持 1（模板已固定——框架该项 >1 会因缺分布式锁后端启动即失败）。
RBAC 需两份：服务自身 namespace + AgentServer 拉起的目标 namespace。

### 多副本 e2e 验收（真 LB 单入口）

```bash
uv run --no-sync python scripts/e2e_multi_replica.py \
    --base-url http://127.0.0.1:30091/api/session \
    --redis-url redis://127.0.0.1:30001/2 --namespace default
```

只打一个 LB 入口，实例身份从 Redis 选主键反查：选主互斥 / 突发不超收 /
幂等 / 配置传播 / **failover（流量中删副本 Pod → 恢复 + 接管）**。
普查不足 2 实例自动 DEGRADED（多副本专项 SKIP、退出码 0），同一脚本可对
单实例回归。

### 压测 / 浸泡

```bash
uv run --no-sync python scripts/load_test.py --base-url http://127.0.0.1:30091/api/session \
    --scenario route_touch --concurrency 8 --rps 100 --duration 3600 --report-interval 300
```

场景 `route` / `route_touch` / `queued`（刻意排队，SCOPE_QUEUE_FULL /
SCOPE_FULL_TIMEOUT 属预期）；输出 p50/p90/p99/max、吞吐、错误码直方图；
长 `--duration` 即浸泡（周期增量报告）。全程无 FLUSHDB、默认不调 cleanup
端点，资源按 run-id 命名空间化靠 TTL 老化。

## 结构

```
src/agent_runtime/
  errors.py            业务错误码契约（SCOPE_QUEUE_FULL / SCOPE_FULL_TIMEOUT / ...）
  util.py              scope_id 派生 / deploy 指纹 / bytes 解码
  spec_fields.py       template 字段分类（A 类 deploy 子集 / B 类策略）
  config.py            AGENT_RUNTIME_* 环境变量
  main.py              组装：两 SystemContext + Facade 互绑 + 后台任务 + App
  cli.py               命令行入口（deploy.sh 调用）
  session_manager/     状态层(state/lua) + route 编排 + sweeper + config 层 + Facade
  resource_manager/    状态层(state/lua) + acquire/reclaim 编排 + K8s(Real/Fake) + sweeper
tests/                 单测（状态层/config 层）+ 组件全链路 + HTTP 冒烟
```

## 场景覆盖（HLD §6 A–N）

| 场景 | 测试 |
|---|---|
| A 亲和续期 / B first-fit / C 扩 Pod / F 容量满 | `tests/integration/test_route_flow.py`、`tests/session_manager/test_sm_state.py` |
| D 老化回收 / E 保活 / G 死 Pod 清洗 | `tests/integration/test_route_flow.py` |
| H min_idle 热备 / I acquire / J 死 Pod 探测 | `tests/integration/test_route_flow.py`、`tests/resource_manager/test_rm_business.py` |
| K reclaim / L 孤儿对账 / M 配置热更新 / N 半死探测 | `tests/resource_manager/test_rm_business.py`、`tests/session_manager/test_config_store.py` |
| 多副本语义（选主互斥 / 跨副本闸门与唤醒 / failover） | `tests/integration/test_multi_replica.py`、`scripts/e2e_multi_replica.py` |

真 K8s / 真 Redis 单实例端到端（M6 冒烟）与真 LB 多副本端到端（M7）均已脚本化，见上。
