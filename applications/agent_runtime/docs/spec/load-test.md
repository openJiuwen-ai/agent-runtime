# load_test:场景化压测/浸泡工具(设计与指南)

- 日期:2026-09-14(工具 2026-09-08 定形于 feature 篇;本文为其 spec 与操作指南)
- 读者:跑压测/浸泡的工程师、给工具加场景或判定的人
- 脚本:`scripts/load_test.py`(单文件,asyncio + httpx + 标准库,**零额外依赖可分发**)
- 设计论证与被否方案:`../feature/2026-09-load-test-scenarios-checks.md`;容量语义背景:
  `../feature/2026-09-scope-full-fastfail.md`(queued 白名单)、
  `../feature/2026-09-config-refresh-sunset-gate.md`(refresh 409 背压)
- 语义权威:`../design/Agent-Runtime-HLD.md`(冲突以它为准)

---

## 1. 定位与安全红线

对**已部署的** agent-runtime(单实例或 LB 入口)打真实 HTTP 负载 + 配置面扰动,
从客户端、可视化接口、ERROR 日志三个视角判定,输出退出码可直接接 CI/门禁。
长时 `--duration` 即浸泡(周期增量报告,内存有界)。

红线(违反即返工,与 CLAUDE.md 一致):

| # | 红线 | 工具内落实 |
|---|---|---|
| 1 | 业务与配置操作**全程只经 HTTP API**,不碰 Redis/DB/K8s 写面 | 唯一例外:只读 `kubectl logs`(ERROR 日志感知) |
| 2 | 不 FLUSHDB、不调 cleanup 端点(会删目标 ns 下**全部** AgentServer Pod) | 模板/规则/会话按 run-id 命名空间化(`c-{run}-*`/`tpl-{run}-*`/`scope-{run}-*`),靠 TTL 老化 |
| 3 | config_sync/config_refresh 服务端共用串行锁,并发即 409 | 工具内 `config_plane_lock` 单发射者串行(churn/refresh 两控制器共享一把本地锁),**不自造 409** |
| 4 | 容量语义(503/409 的已知路径)不判缺陷 | `EXPECTED_ERRORS` 白名单 + warn 观测,见 §6 |

## 2. 架构与 run 生命周期

```
seed(config_sync 快照式:容器+模板+scope,容器 id 带 run 前缀不撞唯一约束)
  │
  ├─ N × _traffic_worker   route/touch 混合;闭环全速或 --rps 开环令牌桶;
  │                        成功 route 的 pod_id 喂 AffinityTracker(被动亲和记录)
  ├─ churn_controller      每 --churn-interval 一次 config_sync 翻转 B 类参数两态
  └─ refresh_controller    每 --refresh-interval 一次 config_refresh + 冷/暖探测
  │   (config 面场景才有控制器;mixed 下两者共享本地锁)
  │
cleanup(config_sync 清空全量——快照式播种后服务里的配置只属本 run,清空=只删本 run)
  → 静置 --settle(容忍 stats 5s 批量 flush)
  → post_run_sweep 收尾巡检(只读 /visualization/*)
  → ERROR 日志增量扫描(run 开始打快照:文件字节偏移 / kubectl --since-time)
  → 报告(checks 汇总 + 延迟分位数 + --json 机器可读块)
```

组件分节(单文件内,按节名找):参数 / 信封与载荷 / HTTP 基础 / 统计(`Stats`/
`AffinityTracker`)/ 限速(`_TokenBucket`)/ 可视化客户端(`VisClient`)/ 判定框架
(`CheckRecorder`)/ ERROR 日志(`FileLogSource`/`KubectlLogSource`/`discover_log_sources`)/
config 面控制器 / 流量 worker / 收尾巡检 / 主流程。

内存有界(浸泡):延迟样本三平行数组(latencies/ts/endpoints)保留最近 ~2M 条,
超出裁最旧 1/4(三数组同步裁保对齐);total 与错误直方图始终全程累计。

## 3. 场景矩阵(--scenario,6 个)

播种参数按场景差异化(`_scenario_template_params`);流量 = `groups × sessions_per_group`
个会话轮转,`route_touch`/`mixed` 下偶数位 worker 请求过半后转 touch 保活(`_pick_msg_type`)。

| 场景 | 模板参数(sc/pc/session_ttl/pod_ttl/min_idle) | 流量/扰动 | 预期错误码白名单 |
|---|---|---|---|
| `route` | 50/10/120/120/0 | 纯路由 | —(全 200) |
| `route_touch` | 50/10/120/120/0 | 路由 + 半数 touch 保活 | — |
| `queued` | 2/2(可调)/120/120/0 | 小容量模板刻意打满 | `503/SCOPE_FULL`、`503/NO_POD_AVAILABLE` |
| `config_churn` | 40/10/**600**/120↔180/0 | 流量中每 10s config_sync 翻转两态:`{sc:40,pod_ttl:120}↔{sc:50,pod_ttl:180}`(同 id 仅参数变=热更新) | — |
| `config_refresh` | 40/**4**(max_pods=10)/**600**/120/**1** | 流量中每 15s config_refresh + 每窗口 2 个一次性冷启动会话 | —(冷探测 503 记 warn) |
| `mixed` | 同 config_refresh | route_touch + churn + refresh 同场(共享 config 面串行锁) | `503/NO_POD_AVAILABLE` |

参数差异的理由:

- **churn/refresh 的 session_ttl=600**:防长 run 会话 TTL 过期造成亲和误报;
- **refresh/mixed 的 pod_concurrency=4**:max_pods=⌈sc/pc⌉=10,放宽容量闸门让冷探测
  多为真冷启动而非撞顶;**min_idle_pods=1**:否则新代暖 Pod 只被新会话触发,
  重建收敛断言不可靠;
- **queued 的 sc/pc=2/2**:刻意小容量,把"容量满快失败路径"打到(503 是目标,
  只报告不判败)。

### 3.1 churn_controller 断言(每轮)

| 断言 | 级别 |
|---|---|
| config_sync 200(409 CONFIG_SYNC_BUSY 记 warn) | fail/warn |
| affected_scopes 覆盖全部播种 scope | fail |
| 热更新传播:30s 内 `/visualization/scope` 的 `sm.capacity` 见新 sc/pod_ttl(旧 runtime 无该字段→一次性 warn 并回退 `/visualization/config` 模板参数断言,不静默) | fail |
| 路由快照 ver 严格递增 | fail |

### 3.2 refresh_controller 断言(每轮)

| 断言 | 级别 |
|---|---|
| 409 CONFIG_SYNC_BUSY = 日落闸门串行化背压:**代次必须冻结**(拒绝零副作用) | warn |
| config_refresh 200 且全 scope 覆盖(generations) | fail |
| generations 单调 +1(响应与 `/visualization/scope` 双源一致) | fail |
| 冷启动探测(一次性新会话,候选集已清空必走新代部署):503=已知容量语义记 warn,**非 200 非 503 即 fail** | warn/fail |
| 新代暖 Pod 重建收敛(≤120s,逐 scope 轮询 rm.pods 出现目标代次) | fail |
| 重建后暖探测 200(命中 min_idle 暖 Pod,兜底"恢复服务") | fail |
| 存量会话亲和:被动记录同 session 是否换 pod;换 pod 后核验旧 Pod 存活性——**旧 Pod 仍在池=真违规 fail;已消亡(pod_ttl 回收)=合法重放置 warn** | fail/warn |

## 4. 判定层(与场景正交,CheckRecorder 三级)

`fail`(计退出码 1)/ `warn`(仅报告,多为已知容量语义观测)/ `skip`(前置不满足)。
仿 `e2e_lib.check/summary_and_exit` 自实现,保持单文件零依赖。

### 4.1 客户端视角(Stats)

延迟分位数(p50/p90/p99/max)/ 错误码直方图(`"{status}/{error_code}"`)/
传输层错误率(超 `--max-error-rate` 退出码 1,默认 1.0=永不因业务错误判败)。
时间窗原语:`snapshot_window`/`snapshot_complement`(churn 抖动观察:窗口只罩
sync 请求本身 −1s/+2s,sync 的锁内写库+推 RM 都在响应前完成,长尾会把整个 run
摊进窗口、基线恒空)。

### 4.2 可视化接口断言(VisClient,只读 GET)

`/visualization/{config,scope,scopes,stats,recent_errors,evaluation}`(注意:无
`/api/session` 前缀)。**endpoints/recent_errors 是实例视角**——LB 后单次 GET 只见
一个副本,靠多次采样(`Connection: close` 促新连接分布)按 instance_id 分桶、
`(ts,request_id,error_code,endpoint)` 去重后聚合近似;scope/config/scopes/history
是 Redis/DB 全局态,无此问题。

### 4.3 ERROR 日志感知(--log-source auto|file|kubectl|none)

run 开始(seed 之前)打快照,结束扫增量,按 `" - ERROR - "` 行计数,超
`--log-error-max`(**默认 0**)即退出码 1。依据:业务失败(503/409)只打 INFO 汇总,
ERROR 行 = 真异常,默认阈值 0 是安全硬门。

| 源 | 形态 | 精度 |
|---|---|---|
| file | 宿主机 `deploy_replicas.sh`:`logs/replica-<port>.log` 字节偏移增量(文件变小=重启/轮转,从头扫并标注) | **精确判定面** |
| kubectl | K8s 形态:`kubectl logs --since-time`(只读;`--log-namespace` 是 **runtime 服务** ns,与 AgentServer Pod 的 ns 是两个东西) | 尽力——Pod 重启丢日志 |
| auto | `--log-file` > 模块根 `.replicas.json` 反查副本日志文件 > kubectl > 无 | — |

辅助:`--log-error-allow`(ERROR 白名单正则)、`--log-scope run`(只计行尾含本
run id 的请求行)、`--log-sample`(采样展示条数)。

### 4.4 收尾巡检(post_run_sweep,流量停止 + 静置后)

| 巡检 | 级别 |
|---|---|
| 静息 deploying 收敛(60s 内全部 scope deploying==0;仿 e2e 阶段 11b) | fail |
| recent_errors 多实例聚合无白名单外错误码(本 run 冷/暖探测请求剔除,由专属 check 观测) | fail |
| 客户端观测错误码在服务端 stats 可见(交叉验证;实例视角+5s flush 滞后,只 warn) | warn |
| 自评估无 critical findings(`latest=null` 属正常→skip) | warn/skip |

### 4.5 退出码

**0**=全过;**1**=任一 fail 级 check 或传输层错误率超限;**2**=环境不可用
(`healthz` 探测失败/播种失败)。`--json` 末尾输出机器可读块:checks/error_logs/
config_events/affinity_violations(全量,供检查窗口外可见性)/cold_start。

## 5. 使用指南

### 5.1 环境前置

| 项 | 约定 |
|---|---|
| 目标入口 | LB NodePort `http://127.0.0.1:30091/api/session`(建议经 LB;单实例直连亦可) |
| runtime 服务 ns | `agent-runtime-e2e`(kubectl 日志源用) |
| AgentServer Pod ns | `agent-runtime-e2e-wmq`(`--namespace`,须已存在) |
| Redis | `redis://127.0.0.1:30001/2`(集群内多副本用 db 2——归属约定见 e2e-test-cases §7.1) |
| 镜像契约 | 默认 influxdb:1.8 替身(快检);**真镜像门禁必须带三件套**(同 e2e 门禁) |
| 部署版本 | `kubectl -n agent-runtime-e2e get deploy -o wide` 核对镜像 tag;旧镜像缺 `sm.capacity`/evaluation 字段会走回退/skip(设计内) |

### 5.2 典型命令

```bash
cd applications/agent_runtime

# ① 快检(60s,替身镜像,route+touch;发布后常规回归)
uv run --no-sync python scripts/load_test.py \
    --base-url http://127.0.0.1:30091/api/session \
    --scenario route_touch --concurrency 8 --duration 60

# ② 开环定速(令牌桶,测指定 rps 下的延迟;0=闭环全速)
uv run --no-sync python scripts/load_test.py --scenario route \
    --rps 500 --duration 120

# ③ 容量满快失败(小容量模板,503 属预期只报告)
uv run --no-sync python scripts/load_test.py --scenario queued --duration 30

# ④ 热更新(流量中翻转 B 类参数;把 sync 窗口 p99 抖动升级为门禁)
uv run --no-sync python scripts/load_test.py --scenario config_churn \
    --duration 300 --churn-jitter-budget-ms 5 --churn-jitter-fail

# ⑤ 强制刷新(注意日落闸门:流量钉住会话时整个 run 可能仅首轮成功,
#    多轮滚动生命周期由 test_force_refresh.py R5 覆盖——见 §6.1)
uv run --no-sync python scripts/load_test.py --scenario config_refresh \
    --duration 300

# ⑥ 混合收官(K8s 联调,kubectl 感知 ERROR 日志)
uv run --no-sync python scripts/load_test.py --scenario mixed \
    --duration 300 --groups 2 --log-source kubectl --json

# ⑦ 真实 AgentServer 镜像(三件套契约,同 e2e 真镜像门禁;ready_timeout 放大)
uv run --no-sync python scripts/load_test.py --scenario mixed --duration 3600 \
    --agent-image swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-amd64:<tag> \
    --health-path /api/v1/health --sse-path /api/v1/events/stream --ready-timeout 240 \
    --agent-env '{"AGENT_HTTP_ENABLED":"true","AGENT_HTTP_HOST":"0.0.0.0","AGENT_HTTP_PORT":"8086"}'

# ⑧ 浸泡(12h:mixed + 真镜像;--report-interval 周期增量报告,样本自动裁剪)
#    实测基准见 §5.4
```

### 5.3 参数速查(默认值;全表见 `--help`)

| 组 | 参数(默认) | 说明 |
|---|---|---|
| 流量 | `--concurrency 4` / `--rps 0`(闭环) / `--duration 30` / `--warmup 3`(不计统计) | worker 数 / 开环令牌桶 / 时长 / 预热 |
| 规模 | `--groups 4` / `--sessions-per-group 8` | scope 数与每 scope 会话数 |
| 容器契约 | `--agent-image influxdb:1.8` / `--health-path /health` / `--sse-path /sse` / `--agent-env -` / `--ready-timeout 60` | 真镜像带三件套 + 240 |
| 收尾 | `--cleanup config`(删本 run 模板/规则) / `--settle 6` / `--no-postcheck` | 会话与 Pod 永远留 TTL 老化 |
| churn | `--churn-interval 10` / `--churn-propagate-timeout 30` / `--churn-jitter-budget-ms 0`(仅报告) / `--churn-jitter-fail` | 抖动预算>0 才成 check |
| refresh | `--refresh-interval 15` / `--refresh-cold-probes 2` / `--refresh-rebuild-budget 120` / `--refresh-cold-budget-ms 45000` | 冷启动预算是 warn 级 |
| 日志 | `--log-source auto` / `--log-error-max 0` / `--log-error-allow` / `--log-scope all` | 见 §4.3 |
| 判定 | `--allow-error`(追加白名单,如 `503/SCOPE_FULL`) / `--max-error-rate 1.0` | |

自检警告(duration 短于扰动周期会提示):config 面场景 `--duration < 2×interval`
可能没有扰动发生;refresh/mixed `--duration < refresh-rebuild-budget` 收敛断言可能超时。

### 5.4 结果解读与实测基准

- **先看退出码与 `[final] checks: N pass / M fail / K warn`**;FAIL 行单独重列。
- **warn 不一定是问题**:预期内错误码出现(容量语义观测)、冷启动 503/延迟超
  预算、409 背压、合法重放置、stats 交叉验证缺失——逐条对应 §6 的已知语义。
- **浸泡周期报告**:`[soak] n=… rps=… p50/p90/p99/max`,看趋势漂移。
- 实测基准(2–3 副本 LB,供对照):

| run | 结果 | 关键数据 |
|---|---|---|
| mixed 180s(替身) | 77/0/1 | 11.6 万请求 p50=5.3ms p99=9.4ms;14 sync + 2 refresh 零 409 |
| 600s mixed | 258/0/1 | 39 万请求(653rps)p99=10.1ms;冷启动 18/18(p50 6.1s) |
| **12h 浸泡(真镜像 0.0.16s,3 副本)** | **3310/0/1** | 29.18M 请求(674.5rps)p50=5.4/p99=10.3ms 全程无漂移;719 sync(p50 112ms)+ 71 refresh 零 409,代次 0→71;冷启动 142/142(p50 12.0s/max 16.1s);503/NO_POD_AVAILABLE 1057 次(0.00%,warn);亲和 574 次换 pod 全为合法重放置;ERROR 日志 0 |

## 6. 已知语义(读数时不要误判为缺陷)

1. **refresh 409 CONFIG_SYNC_BUSY(2026-09 日落闸门)**:上一轮 refresh 的老代 Pod
   回收完成前再刷 → 409 背压、代次冻结,属闸门语义。**流量钉住会话时整个 run 可能
   仅首轮成功**——多轮滚动生命周期由 `test_force_refresh.py` R5 离线覆盖,压测只验
   首轮 + 背压零副作用。
2. **503 NO_POD_AVAILABLE(白名单)**:max_pods=⌈sc/pc⌉ 含待回收老代,连续刷新/
   churn 翻转 sc 时容量闸门收紧,存量会话重放置撞顶——容量保护而非缺陷。是否需要
   产品侧缓解(max_pods 排除待回收老代)是开放问题(feature 篇有记录)。
3. **亲和"换 pod"≠违规**:pod_ttl 到期回收(pod_ttl < session_ttl 时必然偶发)后
   会话重放置是合法的——工具自动核验旧 Pod 存活性分流 warn/fail(2026-09-11 实测
   一例 FAIL 经 K8s 事件时间戳实锤为 pod_ttl=180s 合法重放置后加的存活性核验)。
4. **实例视角近似**:endpoints/recent_errors/stats 经 LB 只见单副本,聚合采样是
   近似——硬门放在客户端精确计数与 ERROR 日志上,服务端面只做 warn 交叉验证。
5. **kubectl 日志源是尽力源**:Pod 重启丢日志;精确判定用宿主机文件日志源
   (`deploy_replicas.sh` 形态,auto 会自动发现)。
6. **queued 的 503 是目标不是故障**:该场景就是刻意打容量满快失败路径。
7. **旧 runtime 镜像**:无 `sm.capacity`/history/evaluation 字段 → 传播断言回退
   DB 视角、自评估巡检 skip,均有 warn/提示,不静默。

## 7. 改这里(维护指引)

| 想做什么 | 改哪里 |
|---|---|
| 加场景 | `SCENARIOS` + `_scenario_template_params`(播种差异)+ `EXPECTED_ERRORS`(白名单)+ `_pick_msg_type`(流量形态)+ main 中控制器挂载;config 面场景须接 `config_plane_lock`(不自造 409) |
| 加判定 | fail/warn 用 `CheckRecorder.record(..., severity=...)`;服务侧只读断言加进 `post_run_sweep`;容量语义新路径进 `EXPECTED_ERRORS`/`--allow-error`,并同步 §6 |
| 加日志源 | 新 `*LogSource` 类(须有 `kind/describe/snapshot/collect`)+ `discover_log_sources` 接线;保持只读 |
| 改判定阈值 | 默认值都在 `_parse_args`;`--log-error-max` 默认 0 是安全硬门,放宽须给理由 |
| 播种载荷 | `_seed_payload`/`_main_container`(信封与三段式契约见 `../api/config-plane-api.md`);容器表 canonical 15 键由服务端水合,工具只发主容器必填面 |

约定:动服务语义的连带(如新的预期错误码)须同步本篇 §3/§6 与 feature 记录;
工具自身较大改动按 `../feature/_TEMPLATE.md` 新建一篇并登记其 README 索引
(参照 `2026-09-load-test-scenarios-checks.md`)。
