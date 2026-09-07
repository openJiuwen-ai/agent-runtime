# 系统自评估与建议能力(观测补齐 + 规则引擎 + LLM 分析)

- 日期:2026-09-03
- 涉及模块:evaluation(新子包)/ session_manager / resource_manager / service-core / visualization / 测试 / 部署 / 文档

## 背景与动机

scope 重构(2026-08)与数次重构后,可视化面暴露三处结构性缺口:

1. **容量推导链在 scope 维度断了**:`/visualization/scopes` 与 `/visualization/scope`
   看不到 scope_concurrency/session_ttl/max_waiters 闸门(只在模板列表里),
   而 `max_pods=⌈sc/pc⌉` 是派生值——看不到"这个 scope 的容量为什么是这个数"。
2. **全部实时快照,零历史**:无趋势、无时序存储;stats 进程内重启清零、多副本
   各答各的;扩缩容/冷启动/回收事件只有日志没有计数。
3. **只有原料没有结论**:配置矛盾(如 `min_idle_pods > max_pods`,config 层只
   校验下界不拦,运行时表现为 autoscale 永远 skip_max 补不满)无人判定。

用户需求:让 runtime 具备"系统自评估 → 出建议 → 人审 → 应用"的自演进闭环,
评估建议可由大模型给出。与需求方确认过的决策(2026-09-03):

- **三层全做**:观测补齐 + 确定性规则评估 + LLM 分析;
- **LLM 接入形态 = 服务内置评估 job**(env 未配置自动禁用、零影响);
- **闭环程度 = 只出建议,人审后经 Claw Manager 应用**——不动写通道。

## 方案

定案要点:

| 决策点 | 定论 | 理由 |
|---|---|---|
| 评估数据源 | **Redis 聚合,不用进程内存** | 评估 job 全局单副本选主,读不到其他副本的内存桶;route/touch 经 LB 分散在全部副本 |
| 热路径埋点 | **进程内存缓冲 + 每副本异步批量 flush(5s)** | route 是关键路径,不逐请求写 Redis;崩溃至多丢 5s 计数 |
| 后台事件 | **状态变迁直写 HINCRBY**(deployed/deploy_error/reclaimed/pod_dead;skip_* 不写) | 选主 job 全局单执行无重复;低频直写无缓冲复杂度 |
| 采样/评估 | **两个独立 job**:sys_sample(30s)+ sys_eval(300s) | tick 超时语义分离(30s/120s 盖住 LLM 调用);评估失败不污染采样序列 |
| 时序底物 | **单条采样含池态+计数器快照** | 计数器单调,相邻差分即速率;免独立历史计数键 |
| LLM | OpenAI 兼容 chat completions;base_url+model 均非空才启用;**LLM 只做汇总/补充,不做原始数据分析**;输出字段白名单过滤;任何失败降级纯规则报告 | 防幻觉:数字结论全部来自规则引擎;防越界:补充建议只许引用 B 类策略字段 |
| 键前缀 | 新 hash tag `{agent_runtime:eval}`,全单键命令、零 Lua | cluster 单槽;见键表论证 |

**被否掉的备选**(防重踩):

- 热路径逐请求 HINCRBY:route 每请求多两次 Redis 往返,过载时(max_reached
  每请求一次)计数写入本身成为放大器——否决。
- eval job 内分频采样(300s 内每 30s 采样一次):评估 tick 超时/异常会打断
  采样连续性,动态规则的差分速率出现空洞——否决,独立 job。
- MetricsRegistry 扩 scope 维度桶:其语义是「per-endpoint、单调快照、命中
  实例视角」,与 drain-and-reset 冲突;且单进程视角对评估无用——否决,
  新建 ScopeTelemetryBuffer + Redis 聚合。
- touch 按 scope 分桶:需额外 HGET session:{id} 反查 scope(热路径加一跳),
  且 touch 不产生容量压力信号——不做。
- runtime 自动应用建议(真自演进闭环):配置写回只有 Manager→全量 config_sync
  一条单向通道,LLM 幻觉直接改生产容量 + 下次 sync 会拍回——否决,首期只读。

## 实现

新子包 `src/agent_runtime/evaluation/`(对齐 SM/RM 的 state 门面模式):

- `state.py`:`{agent_runtime:eval}` 键门面(全单键命令)——`ct:scope:{sid}`
  计数 HASH / `sample:scope:{sid}` 采样 ZSET(25h TTL)/ `report:latest` +
  `report:history`(30d TTL + 容量 200)。
- `collector.py`:`ScopeTelemetryBuffer`(route/acquire 热路径内存缓冲,1024
  scope LRU 上限,绝不抛)+ `EvaluationCollector`(scope 清单 = RM 键 ∪ 路由
  快照,phase 分类 active/disabled/orphan_rm/missing_rm_cfg;sample_once)。
- `rules.py`:纯函数。静态 7 条(min_idle>max_pods 矛盾 critical、
  Σmin_idle 集群预算、⌈sc/pc⌉ 取整浪费、禁用模板被引用、expires_at 过期/
  临期、RM 孤儿 config 幻影预热、RM config 缺失)+ 动态 5 条(并发顶格占比、
  容量错误速率、冷启动率、暖池松弛、回收-重建 churn)。Finding 带 A/B 类
  标注与重建代价。(rebase 到场景 F 快失败后:删 S-TIMEOUT-TTL-RATIO/
  D-WAITER-PRESSURE 两规则,容量错误计数改单一 SCOPE_FULL 码,采样去 w 键)
- `llm.py`:OpenAI 兼容 client(短连接,transport 可注入测试);prompt 构造
  白名单投影 + 48KB 体积护栏;`parse_llm_analysis` 剥围栏→结构校验→
  additional_findings 逐项策略字段白名单(越界整条丢弃)。
- `evaluator.py`:evaluate_once = 清单→静态规则→逐 scope 采样窗口(24h)动态
  规则→LLM 叠加(可选)→报告落 Redis。报告含 caveats(A 类代价 + 人审路径)。

接线(`main.py`):TICK_TIMEOUTS +`sys_sample:30/sys_eval:120`;`_bind_modules`
构造 eval 四件套并把 telemetry 注入 SM/RM orchestrator、event_sink 注入
rm_sweeper(对齐 ConfigStore(push_pool_config=...) 回调注入先例);`_build_jobs`
+2 选主 job(锁键 `agent_runtime:job:sys_*`);start() 起每副本 telemetry flush
任务(5s 周期,10s 超时防御),stop() cancel + 终结 drain。**jobs 5→7**。

可视化(`visualization_api.py`):`_scopes` 行补 phase/template_id/scope_concurrency/
max_waiters/session_count 等;`_scope` 补 `sm.capacity` 子对象(含
session_utilization/waiter_utilization/route_budget_sec)与顶层 phase;新端点
`/visualization/history`(单 scope 采样窗口)与 `/visualization/evaluation`
(latest+history,全局视角);`/visualization/stats` 补 `scopes` 段(Redis 全副本
聚合);`/visualization/overview` config 摘要补 eval 四项(llm 只报 enabled 布尔)。

埋点:`session_manager/orchestrator.py` route 在 resolve 后 try/finally 计数
(need_acquire 分支单独计数);`resource_manager/orchestrator.py` acquire finally
归一化 outcome 首词计数;`resource_manager/sweeper.py` 四处状态变迁经
`_emit`(埋点绝不反噬业务)。全部 keyword-only 注入、默认 None——旧测试零改动。

env(`config.py` + 部署模板):`AGENT_RUNTIME_EVAL_SAMPLE_INTERVAL/INTERVAL/
LLM_BASE_URL/LLM_API_KEY/LLM_MODEL/LLM_TIMEOUT/POD_BUDGET`,默认全空=纯规则
评估、无 LLM 外呼。deploy env 两文件 + template.yaml + server.env.example 同步
(空值变量也必须定义,防 render 残留 `<<` fail-fast)。

## 验证

- 单测:新增 `tests/evaluation/` 51 例(规则逐条触发/不触发边界、LLM 三态与
  解析降级与白名单、state 键形/TTL/报告容量、collector 缓冲/清单/采样、
  evaluator 全链路)+ `tests/integration/test_visualization_api.py` 异步用例
  (history/evaluation/stats.scopes 直接驱动 on_tick)。全量 **495 passed**
  (原 442 + 53,含快照只读纪律用例);唯一确定性改动 jobs==5→7。
- 真环境(2026-09-03,全部通过):
  - **verify_redis_cluster 真 3 主 cluster 19/19 PASS**(16 存量 + eval 三项:
    计数聚合/采样回读/报告落盘);
  - **真镜像发布门禁 126/126 PASS**(三件套契约参数 + --with-sidecar
    --with-mounts,AgentServer/sandbox 0.0.11s,agent-runtime:eval-20260904
    双副本经 NodePort LB;含阶段 13c 全五项:7 job 注册与 ok_ticks/scopes
    容量闸门字段/history 采样字段齐全/evaluation 报告 llm=disabled
    findings=6/无凭证泄漏)。
  - 门禁过程实锤并处置三件事:
    1. **采样只读纪律(设计缺陷,门禁抓出)**:首版 sys_sample 经
       `routing_snapshot_view()` 在快照缺失时从 DB 重建并回写 → 冒烟 FLUSHDB
       后快照"复活"+eager 预热拉 Pod,违反 H0「服务自身不拉起」(前置跑
       124/126,H0×2 挂)。修复:collector 直读原始快照
       (`routing_snapshot_raw`),缺失/损坏跳拍返回空清单,新增用例
       「快照缺失不得回写」;修复后全绿。
    2. **存量 PG 库迁移**:`agent_runtime` 库停在 pg-20260825 时代,按各
       feature 篇目文档义务执行 12 条 ALTER(策略四列 RENAME×4 + node_name/
       run_as_user/run_as_group + 容器拆分三列 + routing_scope.expires_at/
       enabled;模板表 0 行零数据风险)。
    3. **共享 ns 干扰**:共享集群上另一 runtime 实例(0.0.9s 旧配置)持续向
       agent-runtime-e2e 部署热备 Pod,污染 H0 的 ns 级 Pod 计数;e2e 换独立
       ns `agent-runtime-e2e-wmq`(AGENT_RUNTIME_AGENTSERVER_NAMESPACE)后
       全绿——多团队共享集群时 AgentServer ns 应按人隔离。
- 内存估算:30s×24h=2880 点/scope ≈370KB/scope/日;50 scope ≈18MB 单槽,可接受。

## 影响面

- 新 Redis 键域 `{agent_runtime:eval}`(已过 cluster 论证;e2e_lib FLUSHDB
  防误刷白名单 `"{agent_runtime:"` 前缀天然覆盖)。**无 DB 变更、无 Lua、
  无存量键侵入;存量部署不配 env 行为=纯规则评估+30s 采样,零 LLM 外呼。**
- 文档同步:spec 新建 evaluation.md;spec README(架构图 job 数/键前缀)/
  service-core(jobs 表/端点表/env 表,顺手修正 111/113 行 scope 重构前
  过时措辞)/session-manager(telemetry 注入)/resource-manager(event_sink)
  /HLD(§5 键表)/CLAUDE.md(用例计数)。
- 开放问题:① 评估建议的「一键应用」需 Manager 侧配合(单模板 PATCH 通道),
  首期人审经 config_sync;② 前端看板(纯 JSON,无 HTML)未做;③ LLM prompt
  的规则阈值联动(规则可解释但 LLM 只见聚合)可再打磨。
