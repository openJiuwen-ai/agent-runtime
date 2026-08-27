# agent-runtime 端到端测试用例说明（e2e-test-cases）

- 日期:2026-08-18(M6 冒烟固化于 2026-08-15;M7 多副本补全于 2026-08-18)
- 读者:执行/评审端到端验收的工程师
- 配套:语义权威 = `../design/Agent-Runtime-HLD.md`(§6 场景、§5 键表);脚本本体在
  `applications/agent_runtime/scripts/`;本文回答"**每个 e2e 用例:场景、输入、预期输出**"。

---

## 1. 总览:端到端测试体系

| 层 | 入口 | 规模 | 依赖环境 | 退出码 |
|---|---|---|---|---|
| 进程内双实例 | `uv run pytest tests/integration/test_multi_replica.py` | 14 用例 | 无(离线,fakeredis) | pytest 标准 |
| 集成冒烟(M6) | `./scripts/integration_smoke.sh` | 74 项断言 | 单实例 server 模式 + 真 Redis/MySQL/K8s | 0/1/2 |
| 多副本 e2e(M7) | `uv run --no-sync python scripts/e2e_multi_replica.py` | 35 项断言 | K8s 多副本 + Service LB + 真 Redis | 0/1/2 |
| 压测/浸泡 | `uv run --no-sync python scripts/load_test.py` | 3 场景 | 任意入口(建议 LB) | 0/1 |

退出码约定(两个 e2e 脚本一致):**0**=全过(含 SKIP/DEGRADED);**1**=有 FAIL;
**2**=前置自检未过。可直接接 CI。

---

## 2. 通用约定

### 2.1 请求信封(全部用例的输入格式)

四个对外端点均为 `POST /api/session/{route|touch|config_sync|cleanup}`,请求体:

```json
{
  "type": "route",
  "metadata": {
    "request_id": "req-<uuid>",        // 必填;幂等键(60s 窗口)
    "session_id": "s1",                // route/touch 必填
    "user_id": "u1",                   // route 必填(四参非空校验)
    "bot_id": "b",
    "extra": {"group_id": "e2e-main"}  // route 必填;路由表达式左值
  },
  "rawdata": {}                        // config_sync/cleanup 的载荷在此
}
```

成功响应 `rawdata` 携带业务字段(`pod_id`/`pod_sse_url`/`touched`/`ok`/`cleaned`);
失败响应顶层 `error_code`(+可重试错误带 `retry_after`)。

### 2.2 错误码契约(断言依据)

| error_code | HTTP | retry_after | 语义 |
|---|---|---|---|
| `SCOPE_QUEUE_FULL` | 503 | ✅ | 等待队列满,快失败 |
| `SCOPE_FULL_TIMEOUT` | 504 | ✅ | 队列内等待超时 |
| `NO_POD_AVAILABLE` | 503 | ✅ | acquire 失败(MaxPodsReached/DeployFailed 映射) |
| `CONFIG_NOT_FOUND` | 503 | ❌ | 无匹配规则/模板禁用 |
| `VALIDATION` | 400 | ❌ | 参数错 |
| `CONFIG_SYNC_BUSY` | 409 | — | 上次热更新未完成/日落待回收 |

### 2.3 Redis 真相源(e2e 直接断言的键)

- `session_manager:scope:{sid}:sessions`(SET,SCARD=scope 闸门)/`:pods`(ZSET,first-fit 候选)
  /`:waiters`(SET);`session_manager:routing:snapshot`(STRING,路由快照)
- `session_manager:session:{sid}`(HASH)/`session_expiry`(ZSET)/`pods:registered`(SET)
- `resource_manager:resource:scope:{sid}:pods|idle|config|deploying`
- `resource_manager:resource:pods:all`、`resource:pod:{pod}:idle_since`
- `agent_runtime:job:*:winner:{epoch}` / `:candidates:{epoch}`(选主元数据,TTL≈3s)

### 2.4 前置自检与防误刷(两个 e2e 脚本共享 `e2e_lib`)

- 服务/LB 在线(`/healthz` 或 `/docs` 200)、Redis `PING` + `aof_enabled=1`、kubectl 可用、
  专用 namespace 存在(缺则自动创建)。
- **FLUSHDB 防误刷**:目标 Redis DB 存在 `session_manager:`/`resource_manager:`/`agent_runtime:`
  之外前缀的 key 即视为指错库,**中止**(除非显式 `--force-flush`)。务必独立 DB 编号。

---

## 3. 集成冒烟(M6):`scripts/e2e_hld_acceptance.py`(74 项)

### 3.0 环境与模板矩阵

- 前置:服务以 **server 模式单实例**运行(默认 `http://127.0.0.1:8091/api/session`,
  Redis `redis://127.0.0.1:30001/1`);AgentServer 替身镜像 `influxdb:1.8`
  (`:8086/health`=200,满足 readiness/watch 探测契约)。
- 起点清理:FLUSHDB + TRUNCATE 两张配置表 + 删验收 ns 的 agentserver Pod。

播种模板与 scope(阶段 1,经 config_sync **全量**下发 `{templates, scopes}` 一次请求;
全局锁,并发即 409):

| 模板 | 关键参数 | 用途 |
|---|---|---|
| `tpl-e2e` | cc=3 pc=2(session_ttl=30 pod_ttl=60) | 主场景(A/B/C/E/D/M) |
| `tpl-f` | cc=2 pc=1 | 容量满/队列(F) |
| `tpl-warm` | cc=2 pc=1 min_idle=1 ttl=90 | 热备(H)与 A 类日落(M) |
| `tpl-bad` | `agent_image=agent-runtime-e2e-missing:1` ready_timeout=25 | deploy 失败(I) |
| `tpl-nat` | cc=2 pc=2 session_ttl=15 pod_ttl=20 min_idle=0 | **自然老化专用**(阶段 5b,短 TTL 零回拨) |

scope:`e2e-main|e2e-f|e2e-warm|e2e-bad|e2e-nat` 各按 `group_id in [...]` 规则绑一模板
(**不播通配兜底**——使「未知 group → CONFIG_NOT_FOUND」可验收);
可选 DB 落库校验(mysql/psql 客户端在场时两表各 5 行,否则 SKIP)。

### 3.1 阶段与用例(场景 → 输入 → 预期)

**阶段 0 前置自检**(5 项):服务在线 200 / Redis AOF=1 / kubectl 可用 / ns 就绪 / 防误刷守卫通过。

| # | 场景 | 输入 | 预期输出/断言 |
|---|---|---|---|
| 1a | H0 零 Pod 基线 | —(清场后,配置未下发) | ns 内零 agentserver Pod;`routing:snapshot` 不存在(服务启动不拉 Pod) |
| 1 | —(种子) | 1×config_sync 全量 `{templates:5, scopes:5}` | 200,`templates_synced=5 scopes_synced=5`;`routing:snapshot` 已写;DB 行数 [5,5](可选) |
| 1b | H0 无请求预热 | —(种子后,零 route) | ~autoscale tick 后 `resource:scope:e2e-warm:idle` 有 1 热备 Pod 且真实存在于 K8s(**配置驱动预热**) |
| 2 | C 首次部署 | `route(s1, e2e-main)` | 200,`pod_id` 以 `agentserver-` 开头,耗时≈一次 deploy |
| 3 | C 物理真象 | —(上一步的 pod) | `kubectl get pod` 存在且 Ready |
| 4 | C SSE 直连地址 | — | `pod_sse_url` 以 `http://` 开头(指向 Pod IP) |
| 5 | C RM 池登记 | — | `resource:scope:{MAIN}:pods` ZCARD=1 |
| 6 | A 亲和续期 | `route(s1)` 再次 | 200 且同 `pod_id`(零冷启动) |
| 7 | A 会话不增 | — | `scope:{MAIN}:sessions` SCARD=1 |
| 8 | B first-fit | `route(s2)` | 200 且 `pod_id`=pod1(打包 2/2 满) |
| 9 | B per-Pod 闸门 | — | `pod:{MAIN}:{pod1}:sessions` SCARD=2 |
| 10 | C 扩 Pod | `route(s3)` | 200 且新 `pod_id`≠pod1(deploy pod2) |
| 11 | C 候选集 | — | `scope:{MAIN}:pods` ZCARD=2(接入序) |
| 12 | E 保活 | `touch(s1)`(间隔≥1.2s) | 200 `touched=true`;`session_expiry` 分数增大 |
| 13 | E 未命中 | `touch(nope)` | 200 `touched=false` |
| 14 | 幂等 | 同 `request_id` 两次 `route(s3)` | 两次 `pod_id` 一致;SCARD 仍=3(不重抢额度) |

**阶段 3:M(B 类)pod_ttl 热更新**(3 项)

| # | 输入 | 预期 |
|---|---|---|
| 15 | config_sync 全量(tpl-e2e `pod_ttl:120`) | 200 `ok=true` |
| 16 | —(1s 后) | RM `scope:config` 的 `pod_ttl`="120"(update_pool_config 主动推送) |
| 17 | — | `routing:snapshot` 已原子覆盖(下次 route 即见新值) |

**阶段 4:D 老化回收**(回拨 s1–s3 的 `session_expiry`/`expiry` 到过去,5 项)

| # | 输入 | 预期 |
|---|---|---|
| 18 | 时间回拨(加速,不真睡 TTL) | 30s 内 `scope:sessions` 清空(sweeper 1s tick) |
| 19 | — | 会话四处全清(session HASH/expiry/pod 集/scope 集) |
| 20 | — | 空 Pod pass → idle_consider → RM `idle` 暖池 2 个 |
| 21 | — | 不变量 5:`pods:registered` 仍 2 个(待 RM 回收后清) |
| 22 | — | 两个 Pod `phase`="idle" |

**阶段 5:K reclaim**(回拨 `idle_since` 到 pod_ttl 之前,4 项)

| # | 输入 | 预期 |
|---|---|---|
| 23 | `idle_since=now-121` | 20s 内 idle 池清空(reclaim 1s tick) |
| 24–25 | 每个 Pod | K8s 真删(`kubectl` NotFound)+ RM `pods:all` PURGE |
| 26 | — | notify_pod_dead 已清 `pods:registered`(归零) |

**阶段 5b:自然老化全链路(零回拨,5 项)**——tpl-nat(session_ttl=15/pod_ttl=20/min_idle=0),
不回拨任何时间,真等 TTL 走完 D→K;2026-08-26 缺陷①(idle_since 周期刷新致永不回收)的回归网:

| # | 输入 | 预期 |
|---|---|---|
| 5b-1 | `route(nat1, e2e-nat)` | 200 首会话 deploy |
| 5b-2 | 真等 session_ttl=15(不回拨) | sweeper 自然到期回收,`scope:sessions` 清空 |
| 5b-3 | — | 空 Pod pass → 转 idle 暖池,`idle_since` 计时起点存在 |
| 5b-4 | 真等 pod_ttl=20(不回拨) | 计时自然累积满 → reclaim 真删(K8s NotFound + PURGE) |

**阶段 6:I deploy 失败**(2 项)

| # | 输入 | 预期 |
|---|---|---|
| 27 | `route(b1, e2e-bad)`(镜像不可拉) | 503 `NO_POD_AVAILABLE`(约 ready_timeout=25s 后) |
| 28 | — | **红线**:`scope:{BAD}:deploying` SCARD=0(错误路径清占位) |

**阶段 7:F 容量满/队列**(cc=2/pc=1,max_waiters=4;5 项)

| # | 输入 | 预期 |
|---|---|---|
| 29–30 | `route(f1/f2, e2e-f)` 串行 | 各 200,2 Pod 占满 |
| 31 | 5 并发 `route(f-over-0..4)` | ≥1 个 503 `SCOPE_QUEUE_FULL`(队列满快失败) |
| 32 | 同上 | ≥2 个 504 `SCOPE_FULL_TIMEOUT`(队列内等待至 deadline) |
| 33 | — | `scope:{FSCOPE}:waiters` SCARD=0(等待者 finally 出队) |

**阶段 8:H min_idle 热备**(3 项)

| # | 输入 | 预期 |
|---|---|---|
| 34 | `route(w1, e2e-warm)` | 200 首会话 deploy |
| 35 | —(≤30s) | autoscale(1s tick)补位:idle=1 |
| 36 | — | 热备 Pod 在 K8s 真实存在 |

**阶段 9:G/J 死 Pod**(4 项)

| # | 输入 | 预期 |
|---|---|---|
| 37 | `kubectl delete pod <w1_pod>`(模拟宕机) | 删除指令成功 |
| 38 | —(≤40s) | watch(10s tick)发现 NotFound → `pods:all` PURGE |
| 39 | `touch(w1)` | `touched=false`(notify_pod_dead 已清洗会话) |
| 40 | — | `pods:registered` 无该 (scope,pod) 前缀 |

**阶段 10:M(A 类)deploy 字段日落**(3 项)

| # | 输入 | 预期 |
|---|---|---|
| 41 | config_sync 全量(tpl-warm `readiness_period:7`,A 类) | 200 `ok=true` |
| 42 | — | RM `scope:config` 的 `deploy_ver` 改变(新 Pod 用新 deploy 字段) |
| 43 | — | SM 候选集 ZREM 软摘除(老 Pod 不接新流量,自然回收) |

**阶段 11:N 半死探测**(1 项 SKIP):AgentServer 镜像对 `GET /health` 返回 426,
暂缓端到端(单测已覆盖:`tests/resource_manager/test_rm_business.py`)。

**阶段 11b:内部不变量巡检(4 项)**——2026-08-26 缺陷②④⑤的回归网(cleanup 清场前执行):

| # | 断言 | 预期(缺陷网) |
|---|---|---|
| IV-1 | `idle ⊆ pods:all` 且 idle 成员必有 `idle_since` | 无幽灵成员(缺陷②:TOCTOU 复活) |
| IV-2 | 静息时各 scope `deploying` SCARD=0 | 无泄漏占位(缺陷⑤:停机取消) |
| IV-3 | 快照模板 `deploy_ver()` == RM cfg `deploy_ver` 且 cfg 内 pod_spec 自洽 | SM/RM 两端同指纹(缺陷④:暖复用前提) |

**阶段 12:L 对账 + cleanup**(4 项)

| # | 输入 | 预期 |
|---|---|---|
| 44 | 一致性巡检 | `pods:all` 每个 Pod 在 K8s 均存在(无漂移) |
| 45 | `cleanup(namespace=验收ns)` | 200 `cleaned≥0`;kubectl 该 ns 无 agentserver Pod |
| 46 | —(12s 后) | watch/reconcile 兜底清空 Redis RM 编排态 |
| 47 | — | `session_manager:pod:*` 注册态全清 |

**阶段 13:错误契约**(5 项)

| # | 输入 | 预期 |
|---|---|---|
| 48 | `route(无匹配 scope 的 group)` | 503 `CONFIG_NOT_FOUND`,**无** `retry_after` |
| 49 | `route(session_id=null)` / `route(user_id=null)` | 400 `VALIDATION`(四参非空) |
| 50 | `touch(session_id=null)` | 400 `VALIDATION` |
| 51 | `config_sync(kind="nope")`(旧 kind/op 协议) | 400 `VALIDATION` |
| 52 | `cleanup(验收ns, label_selector=无匹配)` | 200 `cleaned=0`(空目标须用无匹配 selector,见 §8.1) |

> 断言逐条 `check()` 记名,汇总 74 项(个别为条件性/可选 SKIP,计入通过)。
> **注意**:M6 冒烟回归请对**单实例**执行——多副本后端冷突发语义不同(见 §6)。

---

## 4. 多副本 e2e(M7):`scripts/e2e_multi_replica.py`(35 项)

### 4.0 形态与前置

- **真 LB 单入口**:K8s Deployment 多副本 + ClusterIP/NodePort Service
  (默认 `http://127.0.0.1:30091/api/session`);脚本不打多地址。
- 实例身份从 Redis 选主键反查:`agent_runtime:job:{job}:candidates:{epoch}`(SET,成员=
  instance_id)与 `:winner:{epoch}`(SET NX,值=instance_id);后台 ElectionCensus
  以 0.3s 轮询采样(元数据 TTL≈3s)。
- 前置:Redis(默认 DB 2)、kubectl(需 deployment ns + agentserver ns 权限)、
  influxdb:1.8 替身镜像。
- **DEGRADED 语义**:普查窗口(`--census-window`,默认 15s)内选主键见到 <`--min-replicas`(2)
  个 instance_id → 打横幅,只跑 S1/S2/S5,多副本专项 SKIP,**exit 0**——同脚本可对单实例回归。

### 4.1 阶段与用例

**S0 前置 + 副本普查门**(5 项):LB `/healthz` 200 / Redis AOF / kubectl /
双 namespace 就绪 / 防误刷守卫;普查到 ≥2 实例 → 完整模式。

**S1 经 LB 播种**(1 项):`tpl-mr`(cc=3/pc=2)+`tpl-mr-f`(cc=2/pc=1)两模板 +
`mr-main`/`mr-f` 两 scope(group 规则),config_sync **全量一次**;起点 FLUSHDB + TRUNCATE + 删残留 Pod。

**S2 经 LB 基础流**(5 项):

| # | 场景 | 输入 | 预期 |
|---|---|---|---|
| 1 | 首次部署 | `route(mr-s1, mr-main)` 经 LB | 200;Pod 真实存在(kubectl 验证) |
| 2 | 跨副本亲和 | 再 `route(mr-s1)`(LB 可能落另一副本) | 同 `pod_id`(亲和态在共享 Redis) |
| 3 | 跨副本保活 | `touch(mr-s1)` | 200 `touched=true` |
| 4 | 共享态 | — | `scope:sessions` SCARD=1 |

**S3 选主互斥**(3 项 + 1 观测):

| # | 断言 | 预期 |
|---|---|---|
| 1 | 有效样本量 | 普查含 winner 的 (job,epoch) 样本 ≥3 |
| 2 | **互斥不变量** | 每样本 winner ∈ 该 epoch candidates(SET NX 保证) |
| 3 | 双实例参选 | 存在 candidates 含 ≥2 实例的样本 |
| — | winner 直方图 | SRANDMEMBER 随机轮换,仅记录打印(实测 9/7) |

**S4 并发突发不超收**(cc=2/pc=1,先串行占满再 8 并发,7 项):

| # | 输入 | 预期 |
|---|---|---|
| 1–2 | 串行 `route(mr-f1/mr-f2)` | 各 200,占满 |
| 3 | 8 并发 `route(mr-burst-*)` 经 LB | **0 个 200**(闸门跨副本全局生效) |
| 4 | 同上 | 4×503 `SCOPE_QUEUE_FULL` + 4×504 `SCOPE_FULL_TIMEOUT`(max_waiters=4;30s 超时属预期) |
| 5 | — | `scope:sessions` SCARD=2(不超收) |
| 6 | —(≤45s 轮询) | waiters 清空(残留时打印成员便于定位) |
| 7 | — | `deploying` SCARD=0(占位清空) |

**S5 幂等跨副本重放**(2 项):

| # | 输入 | 预期 |
|---|---|---|
| 1 | 同 `request_id="mr-req-idem"` 两次 route(LB 可能落不同副本) | 两次响应完全一致(幂等态在共享 Redis) |
| 2 | — | 会话数恰好 +1 |

**S6 配置传播**(4 项):

| # | 输入 | 预期 |
|---|---|---|
| 1 | 前置 | `routing:snapshot` 已存在 |
| 2 | config_sync 全量(tpl-mr `session_ttl:120`)经 LB | 200 |
| 3 | — | `routing:snapshot` 已原子覆盖(共享单键,任意副本改,全副本下一读即新值) |
| 4 | 更新后 `route(mr-s6)` | 200 且新会话 expiry−now ∈ [100,130](用了新 ttl) |

**S7 failover**(4 项):背景流量(route/touch 循环,错误只计数不判死)进行中——

| # | 输入 | 预期 |
|---|---|---|
| 1 | `kubectl delete pod <目标副本>`(目标=当前 sm_sweep leader,instance_id 前缀=Pod 名) | 删除指令成功 |
| 2 | —(≤`--failover-timeout` 240s) | Deployment 恢复 ≥2 ready + 普查出现**新** instance_id |
| 3 | —(恢复后 10s 缓冲) | LB `/healthz` 仍 200(服务不中断) |
| 4 | — | 选主互斥不变量在恢复后仍成立 |

**S8 一致性收尾**(1 项):RM `pods:all` ⊆ K8s(无漂移)。

---

## 5. 进程内双实例:`tests/integration/test_multi_replica.py`(14 用例)

同进程两个完整 App(各自 SystemContext + 5 个后台 Job)共享一组
fakeredis/SQLite/FakeK8s,`instance_id` 显式 `replica-a`/`replica-b`,
httpx ASGITransport 单事件循环并发驱动。**输入全部走完整 HTTP**,
等价两副本指向同一 Redis/DB/K8s 的确定性仿真。

| # | 用例 | 输入 | 预期 |
|---|---|---|---|
| 1 | 身份与共享态 | route 经 A,touch 经 B | instance_id 互异且 RM 镜像;B touch 到 A 建的会话 `touched=true` |
| 2 | 交替亲和 | 同 session A→B→A→B route | 恒同 Pod;SCARD=1 |
| 3 | 跨副本突发不超收 | cc=2/pc=1 占满后 8 并发交替 A/B | 0×200;4×503 队列满 + 4×504 超时;终态 SCARD=2、waiters=0、deploying=0 |
| 4 | deploy 锁串行化 + follower 复用 | SlowFakeK8s(deploy 0.4s),A/B 并发冷启动 + 追加 s3 | 并发对**恰好 1 次部署**(输家进等待室复用同 Pod);pod1 满后 s3 才第 2 次部署;窗口零重叠;占位/等待室清空 |
| 5 | 输家复用暖 Pod | 手持 deploy 锁 + 后台注册 idle Pod 后释放;A route | 返回他副本 Pod;本侧零部署;占位清空 |
| 6 | 跨副本唤醒 | A 占满→A 排队→回拨过期→**B** touch | B 的 touch 返回 `touched=false`(惰性驱逐);A 的等待者 <2s 被唤醒并占释放额度 |
| 7 | 幂等跨副本 | 同 request_id A 首发、B 重放 | 响应一致;仅一会话 |
| 8 | 配置失效传播 | B 改 session_ttl,A 再 route 新会话 | 缓存即 DEL;新会话 expiry=now+90 |
| 9 | 单选主验证 | 采样 sm_sweep/rm_autoscale 5.5s | 每 epoch winner∈candidates;candidates 并集=双实例(winner 轮换仅记录) |
| 10 | sweeper 互斥 | 手持 lock:sweep 后 A sweep_once | 直退不误扫;锁释放后补扫完成 |
| 11 | 并发收敛 | A/B sweep_once 并发 gather | 无异常;全部老化;锁正常释放;`pods:registered` 不变 |
| 12 | /healthz | 分别 GET 两 App | 200 + 各自 instance_id |
| 13 | follower 上限严格快失败 | cc=8/pc=2,4 并发冷启动(deploy 0.4s) | 2×200(同 Pod)+ 2×503 NO_POD_AVAILABLE(闸门拒);恰好 1 次部署;占位/等待室清空 |
| 14 | leader 失败 follower 不接管 | deploy 慢速失败(0.5s 后抛),2 并发 | 双 503 NO_POD_AVAILABLE;占位/等待室全清 |

---

## 6. 压测/浸泡:`scripts/load_test.py`

| 场景 | 输入形态 | 判定/预期 |
|---|---|---|
| `route` | 每 scope 50 并发容量,8 会话/scope 轮转 route | 全 200;p50/p90/p99 报告;冷启动 max≈deploy 等待 |
| `route_touch` | 同上 + 半数请求 touch 保活 | 同上(实测 2 副本 LB:16186 请求**零错误**,p50 7.3ms,p99 24.3ms) |
| `queued` | cc=2/pc=2 小容量模板 | 直方图出现 `SCOPE_QUEUE_FULL`/`SCOPE_FULL_TIMEOUT` **属预期**(排队路径被刻意打到),只报告不判败 |

- 速率:闭环(并发全速)或开环(`--rps` 令牌桶);`--duration` 长 → 浸泡
  (`--report-interval` 周期增量报告);Ctrl-C 优雅部分报告。
- 安全边界:全程只走 HTTP,无 FLUSHDB、默认不调 cleanup 端点(会删 ns 下全部
  AgentServer Pod);模板/规则/组按 run-id 命名空间化,靠 TTL 老化。
- 服务侧注意:每排队请求持一条 Redis pubsub 连接(`maxclients` 默认 10k)。

---

## 7. 环境与重现手册

### 7.1 当前环境约定

| 资源 | 约定 |
|---|---|
| Redis | 集群内 `redis`(NodePort **30001**);**DB 1**=宿主机单实例/双进程;**DB 2**=集群内多副本 |
| MySQL | NodePort **30000**;库 `agent_runtime`;Pod 来源授权 `'agent_runtime'@'10.244.%'` |
| 集群多副本 | `default` ns Deployment `agent-runtime`(2 副本)+ NodePort **30091**;镜像 `agent-runtime:smoke`(两节点本地) |
| 镜像构建 | `./deploy/build_image.sh <tag> [--push]`(build context=仓库根;SWR push 需可写凭据) |

### 7.2 重现命令(按层)

```bash
cd applications/agent_runtime

# ① 双实例(离线)
uv sync --extra local && uv run pytest tests/integration/test_multi_replica.py -v

# ② M6 冒烟(单实例)
./scripts/deploy_replicas.sh 1 .env.production.local 8091   # 保持运行
./scripts/integration_smoke.sh                              # 74 项

# ③ 宿主机双进程(观察选主互斥)
./scripts/deploy_replicas.sh 2 .env.production.local 8091
redis-cli -p 30001 -n 1 --scan --pattern 'agent_runtime:job:*:winner:*'

# ④ K8s 多副本部署(生产形态)
./deploy/build_image.sh agent-runtime:smoke
docker save agent-runtime:smoke | ssh root@192.168.1.64 "docker load"   # 或 push SWR
cp deploy/agent_runtime.env.example deploy/agent_runtime.env   # 改镜像/密码
./deploy/render_and_apply.sh deploy/agent_runtime.env --nodeport

# ⑤ 多副本 e2e(35 项,含 failover)
uv run --no-sync python scripts/e2e_multi_replica.py \
    --base-url http://127.0.0.1:30091/api/session \
    --redis-url redis://127.0.0.1:30001/2 --namespace agent-runtime-e2e

# ⑥ 压测/浸泡
uv run --no-sync python scripts/load_test.py \
    --base-url http://127.0.0.1:30091/api/session \
    --scenario route_touch --concurrency 6 --duration 45
```

---

## 8. 已知语义差异与暂缓项

### 8.1 「经多副本 LB 跑 M6 冒烟 63/65」排查实录(2026-08-18,已全部修复)

两处失败当初被初步归因为「多副本冷突发语义」,**深入排查后证明均另有根因**——
都是「该集群部署与单实例环境的配置差异」,修复后经 LB 稳定 **65/65**:

1. **F-队列超时 504 缺失**(实际分布 2×NO_POD_AVAILABLE + 2×200 + 1×QUEUE_FULL):
   集群部署漏设 `AGENT_RUNTIME_SCOPE_FULL_TIMEOUT`,走默认 **30s**,恰等于
   tpl-f 的 session_ttl=30s → 等待者 deadline ≈ 会话到期时刻 → sweeper 驱逐
   f1/f2 的 PUBLISH 在超时前唤醒全部等待者 → 竞态窗口内(候选集已 ZREM、
   idle_consider 未落)部分撞 max_pods(503)、部分落位(200)、无干净 504。
   **修复**:部署模板补该变量(默认 8s,须显著小于 session_ttl),已入模板红线注释。
2. **cleanup 空目标 500**:宿主机 admin 凭据对**不存在的 ns** list pods 返回空
   列表;in-cluster SA 的 namespaced RBAC 先行返回 **403**(ApiException 无处理
   → 裸 500)。**修复**:产品侧 cleanup 对 404 容忍为 cleaned=0(跨凭据形态行为
   对齐),403 保持 fail-fast(静默清零会掩盖 RBAC 配错);测试侧空目标改用
   **无匹配 label_selector**(确定性为 0)。

空目标用例的三个坑(均实测踩过):
- 不存在的 ns → in-cluster 403 / admin 空列表,跨凭据形态行为不一;
- 业务 ns(如 default)→ 同 label 的**真实 AgentServer 会被误删**(排查期间曾
  误删 default ns 2 个 gateway 管理的业务 Pod,其一 16s 内自愈重建——handoff
  §十一.6 教训的再次验证);
- 刚清空的验收 ns → min_idle 模板的 autoscale 1s 内重建热备,cleaned=1。

### 8.2 其余已知差异与暂缓

1. **多副本冷突发**:并发冷启动时占位先封顶 `max_pods`,多余请求立即 503
   `NO_POD_AVAILABLE`(retry_after=1)而非排队——多副本 e2e 的 S4 已按此语义预填;
   压测 queued 场景冷启动期同样可见。不阻塞 M6 冒烟(部署参数对齐后经 LB 65/65)。
2. ~~跨副本冷竞争双 Pod~~(**M8 已解决**):deploy 锁输家原会自建第 2 个空 Pod;
   现进 **follower 等待室**——原子准入上限 `pod_concurrency-1`(overflow 严格快失败)、
   等待有界(ready_timeout+余量)、leader 的 Pod 注册即直接复用、leader 失败不接管
   直接失败(`LUA_DEPLOY_FOLLOWER_GATE`,ZSET+deadline 防崩溃泄漏)。
   双实例用例 4 已收紧断言「冷竞争恰好 1 次部署」;实测冷启动尾延迟 30.5s→10.2s。
3. **场景 N(半死探测)**:待 AgentServer 原生支持 `GET /health` 后补端到端
   (机制已有单测)。
4. **config_sync 串行**:全局锁,任何脚本/客户端并发下发即 409 `CONFIG_SYNC_BUSY`。
