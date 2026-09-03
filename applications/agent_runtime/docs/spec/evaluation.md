# evaluation —— 系统自评估(观测补齐 + 规则引擎 + LLM 分析)

- 读者:AI / 维护工程师。本文件回答 evaluation 子包「代码在哪、怎么协作、改哪里」。
- 改动史:`../feature/2026-09-system-self-evaluation.md`(定案依据与被否备选)。

## 一句话

评估数据落 Redis(`{agent_runtime:eval}` 单槽 tag)→ 确定性规则引擎产结构化
findings → 可选 LLM 叠加分析 → 报告只读产出(`/visualization/evaluation`),
人审后经 Claw Manager 应用。**不动写通道,不自动改配置。**

## 文件一览

| 文件 | 职责 |
|---|---|
| `src/agent_runtime/evaluation/state.py` | Redis 门面(全单键命令):计数 HINCRBY/采样 ZADD/报告 SET+ZADD;键表见下 |
| `src/agent_runtime/evaluation/collector.py` | `ScopeTelemetryBuffer`(热路径内存缓冲,drain-清零)+ `EvaluationCollector`(scope 清单/sampling) |
| `src/agent_runtime/evaluation/rules.py` | 纯函数规则引擎(静态 7 + 动态 5;快失败适配);`Finding`/`ScopeConfigView`/`RuleThresholds` 数据类 |
| `src/agent_runtime/evaluation/llm.py` | OpenAI 兼容 client(短连接、transport 可注入测试)+ prompt 白名单构造 + `parse_llm_analysis` 防御解析 |
| `src/agent_runtime/evaluation/evaluator.py` | `evaluate_once` 编排:清单→规则→LLM(可选)→报告落盘 |
| `tests/evaluation/` | 49 用例(规则边界/LLM 三态/键形 TTL/全链路) |

## Redis 键表(前缀 `{agent_runtime:eval}`,hash tag 单槽、零 Lua)

| 键 | 类型 | TTL | 写者(频率) | 读者 |
|---|---|---|---|---|
| `sample:scope:{sid}` | ZSET(member=紧凑 JSON,score=t 秒;短键 t/p/i/d/s/rt/ef/en/ad/ar/rc/dd) | 25h(每采样刷新) | sys_sample leader(30s/scope) | /visualization/history;sys_eval |
| `ct:scope:{sid}` | HASH(route_*/acq_*/ev_* 计数) | 25h(每次写刷新) | 每副本 flusher(5s 批)/RM 事件 sink | sys_eval;/visualization/stats.scopes |
| `report:latest` | STRING(报告 JSON) | 无 TTL | sys_eval leader | /visualization/evaluation |
| `report:history` | ZSET(member=瘦身条目:去 findings 只留 summary;保最近 200) | 30d | sys_eval leader | /visualization/evaluation |

计数器单调递增,采样含计数快照 → 相邻采样差分即速率,动态规则全从采样序列
推导(无独立历史计数键)。scope 消失后键自然过期,免残留清理。
内存估算:30s×24h=2880 点/scope ≈370KB/scope/日;50 scope ≈18MB。

## 两个选主 job(main.py `_build_jobs`)

| job | on_tick | interval(env,下限钳) | tick 超时 | 锁键 |
|---|---|---|---|---|
| sys_sample | `eval_collector.sample_once` | `AGENT_RUNTIME_EVAL_SAMPLE_INTERVAL`(30s,钳 5) | 30s | `agent_runtime:job:sys_sample` |
| sys_eval | `evaluator.evaluate_once` | `AGENT_RUNTIME_EVAL_INTERVAL`(300s,钳 30) | 120s(盖住 LLM timeout 60s+余量) | `agent_runtime:job:sys_eval` |

另有一个**非选主**任务:telemetry flusher(`OrchestratorSystemContext.start()`
起,每副本一个,5s 周期/10s 超时;drain 缓冲 → 同槽 HINCRBY;stop 时终结
drain)。必须每副本独立——选主 job 会漏非 leader 副本的缓冲。

## 埋点注入契约(keyword-only、默认 None,旧测试零改动)

- `SessionOrchestrator(telemetry=None)`:route 在 resolve 后 try/finally 记
  `observe_route(scope_id, ok, error_code)`;need_acquire 分支记
  `observe_acquire(scope_id, "need_acquire")`。
- `ResourceOrchestrator(telemetry=None)`:acquire finally 归一化 outcome 首词
  (`reuse/deployed/follower_reuse/max_reached`;error 路径 `error`)记
  `observe_acquire`。
- `ResourceSweeper(event_sink=None)`:状态变迁 `await _emit(scope_id, field)` —
  `ev_autoscale_deployed/ev_autoscale_deploy_error/ev_reclaimed`(autoscale 两
  返回点 + _reclaim_pod)/`ev_pod_dead`(watch 判死、半死探测、reconcile 孤儿
  三处调用点;**不在 _purge_and_notify 内发**,防回收重复计数)。skip_* 不写
  (顶格信号由采样序列承担)。埋点绝不反噬业务(try/except 包裹)。
- touch 不分桶:HGET 反查 scope 热路径加一跳,且 touch 无容量信号(取舍定案)。

## 规则清单(`rules.py`;阈值全在 `RuleThresholds`,测试可覆写)

静态(输入 ScopeConfigView 集 + ServiceView):
`S-CONTRADICTION-MIN-IDLE`(min_idle>max_pods,**critical**,config 层只查下界
不拦)/`S-POD-BUDGET`(Σmin_idle>env 预算;0=关闭)/`S-CEIL-WASTE`(sc%pc≠0 尾 Pod
浪费)/`S-DISABLED-TEMPLATE-REF`/`S-SCOPE-EXPIRY`(过期 info/临期 warn)/
`S-RM-ORPHAN-CONFIG`(min_idle>0 幻影预热 warn;=0 残留 info)/
`S-RM-MISSING-CONFIG`。(原 S-TIMEOUT-TTL-RATIO 随场景 F 快失败拆除的等待
队列废弃——无 scope_full_timeout 概念)

动态(输入采样序列,24h 窗口):
`D-CONCURRENCY-SATURATION`(1h 顶格占比≥80% → 升 sc)/
`D-CAPACITY-ERRORS`(SCOPE_FULL 快失败>6/h → 升 sc;pods 长期顶格时标注
max_pods 联动)/`D-COLD-START-RATE`(deployed/(deployed+reuse)>50% → 升
min_idle)/`D-WARM-POOL-SLACK`(2h 满水位+低负载+零容量错误 → 降 min_idle)/
`D-POD-TTL-CHURN`(回收后 10 分钟内重建对数≥3 → 升 pod_ttl)。
(原 D-WAITER-PRESSURE 随等待队列废弃;计数错误码 2026-09 起为单一 SCOPE_FULL,
HASH 字段 route_err_scope_full)

Finding:`{id, severity(info/warn/critical), source(rule/llm), target,
field, current, suggested, rationale, evidence[], change_class, rebuild_cost}`。
**建议只落 B 类策略字段**(scope_concurrency/pod_concurrency/session_ttl/
pod_ttl/min_idle_pods,即时生效);报告 caveat 明示 A 类(deploy 子集)变更
触发 Pod 日落重建 + 409 风险,本报告不涉及。

## LLM 层(env)

| env | 默认 | 说明 |
|---|---|---|
| `AGENT_RUNTIME_EVAL_LLM_BASE_URL` | 空 | OpenAI 兼容端点(如 `http://api.openai.rnd.huawei.com/v1`);**与 model 均非空才启用** |
| `AGENT_RUNTIME_EVAL_LLM_API_KEY` | 空 | 可空(内网免鉴权);绝不进日志/报告/端点输出 |
| `AGENT_RUNTIME_EVAL_LLM_MODEL` | 空 | 模型名 |
| `AGENT_RUNTIME_EVAL_LLM_TIMEOUT` | 60.0 | 须 < TICK_TIMEOUTS.sys_eval=120 |

降级矩阵:未配置→`llm.status="disabled"`(纯规则报告照常);HTTP/超时→
`"error"`+error 留痕(纯规则报告);输出不可解析→`"error"`+"parse failed";
解析成功→合并 `additional_findings`(**逐项策略字段白名单,越界整条丢弃**,
`source="llm"`)。prompt 构造白名单投影(绝不含 agent_env/kubeconfig/
pod_spec/api_key/base_url)+48KB 体积护栏(超限截 trend 段)。

## 可视化端点(service-core.md 端点表同步)

- `/visualization/scopes`:行补 `phase/template_id/scope_enabled/expires_at/
  scope_concurrency/pod_concurrency/session_ttl/session_count`
  (phase 分类:active/disabled/orphan_rm/missing_rm_cfg;清单 = RM 键 ∪ 快照)。
- `/visualization/scope`:`sm.capacity` 子对象(策略字段+派生 max_pods/
  session_utilization/route_budget_sec=ready_timeout+10)+顶层 phase
  (快失败后无 waiters/max_waiters 概念)。
- `/visualization/stats`:`scopes` 段 = Redis 全副本聚合计数(与 endpoints 段
  的命中实例视角并列,docstring 标注差异)。
- `/visualization/history?scope_id=&window_sec=&limit=`:单 scope 采样窗口
  (新在前;limit 钳 [1,1440];缺参 400)。
- `/visualization/evaluation?limit=`:`{latest, history}`(全局视角,读 Redis;
  无报告 latest=null 属正常态,不 404)。

## 环境变量全表

`AGENT_RUNTIME_EVAL_SAMPLE_INTERVAL`(30)/`AGENT_RUNTIME_EVAL_INTERVAL`(300)/
`AGENT_RUNTIME_EVAL_LLM_BASE_URL`/`_API_KEY`/`_MODEL`/`_TIMEOUT`(60)/
`AGENT_RUNTIME_EVAL_POD_BUDGET`(0=预算规则关闭)。默认全空 = 纯规则评估 +
30s 采样,零 LLM 外呼。部署模板(deploy/ 两 env + template.yaml +
server.env.example)已同步——**空值变量也必须定义**,否则 render 残留 `<<`
fail-fast。

## 落地闭环(红线)

报告只读产出。建议应用路径:**人审 → Claw Manager 改模板/scope → 全量
config_sync 下发**。runtime 侧无任何配置写端点;`max_pods` 是派生值不可配
(调容量 = 改 scope_concurrency/pod_concurrency)。
