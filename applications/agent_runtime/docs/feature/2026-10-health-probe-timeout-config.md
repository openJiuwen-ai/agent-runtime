# 健康探测超时可配(AGENT_RUNTIME_HEALTH_PROBE_TIMEOUT)

- 日期:2026-10-10
- 涉及模块:resource_manager / service-core / 测试 / 部署 / 文档

## 背景与动机

sweeper 半死检测(场景 N)对 AgentServer `GET /health` 的单次 HTTP 超时为
k8s.py 两处硬编码 `httpx.AsyncClient(timeout=3.0)`(基类 `probe_health` 与
Real `probe_pod_health`),间隔侧 `watch_interval` 已有 env
(`AGENT_RUNTIME_WATCH_INTERVAL`)而超时侧无配置入口。AgentServer 慢启动/
高负载下 /health 响应慢于 3s 会被连续判失败(`HEALTH_FAIL_THRESHOLD=2` 硬
编码)误杀半死并触发排空重建,现场只能改代码重发版。

## 方案

- env `AGENT_RUNTIME_HEALTH_PROBE_TIMEOUT`(float,默认 3.0,非法值
  `_env_float` 留痕回退)→ `AgentRuntimeConfig.health_probe_timeout` →
  `build_resources` 构造注入 `RealK8sPodClient(health_probe_timeout=...)` →
  `probe_pod_health` 的 httpx 超时。与 `watch_interval` 同链路(启动时一次
  解析,代码不散读 env)。
- 默认值单源:模块常量 `k8s.HEALTH_PROBE_TIMEOUT = 3.0`(与 config 字段
  默认两处各持字面量,注释互指同步);基类 `probe_health` 签名加
  keyword-only `timeout` 参数(基类无构造器,方法注入是最小侵入),基类
  `probe_pod_health` 透传。
- FakeK8sPodClient 不发 HTTP,不受影响。
- 顺带贯通:`WATCH_INTERVAL` 此前部署模板未渲染(env.example 无变量、模板
  无占位符,改了也不生效)——本次与超时同批补齐模板渲染项。

被否方案:probe 方法由 sweeper 调用时传 timeout——调用方需额外持有配置且
签名扩散,不如构造注入符合 `default_namespace` 既有模式。

## 实现

- `config.py`:字段 + `from_env` 解析;`_env_float` docstring 更新(现用户
  两个,原注释只提评估 LLM)。
- `resource_manager/k8s.py`:常量 `HEALTH_PROBE_TIMEOUT`;基类
  `probe_health`/`probe_pod_health` 参数化;`RealK8sPodClient.__init__` 加
  `health_probe_timeout`,mTLS 路径 `timeout=self.health_probe_timeout`。
- `main.py` `build_resources`:构造传入。
- `visualization_api.py`:`/visualization/overview` config 摘要加
  `health_probe_timeout`。
- 部署:`agent_runtime.template.yaml` 加 `AGENT_RUNTIME_WATCH_INTERVAL`/
  `AGENT_RUNTIME_HEALTH_PROBE_TIMEOUT` 两渲染项;env.example 加默认值行。

## 验证

- 单测:`tests/test_config.py` +3(默认/env 覆盖/非法回退);
  `tests/resource_manager/test_k8s_io_timeouts.py` +1(捕获 httpx
  AsyncClient kwargs,断言注入 7.5 生效、未注入回退常量、URL 形态)。
  全量 578 passed / 9 skipped(FakeK8sPodClient.probe_health 签名同步
  对齐基类 timeout 参数——首轮全量跑出 2 处 TypeError 回归后修复)。
- 判死时延公式不变:≈`HEALTH_FAIL_THRESHOLD(2) × watch_interval`(默认
  20s);调大超时不改判死节奏,只减少慢响应误判。

## 影响面

- spec 同步:resource-manager.md(probe_health 契约)、service-core.md
  (env 表)、api/config-plane-api.md(overview 示例与字段说明)。
- **存量部署升级注意**:部署模板新增两个 `<<VAR>>` 占位符,实际 env 文件
  (deploy/agent_runtime.env,不入库)缺变量时 render_and_apply.sh 渲染后
  残留 `<<` 会 fail-fast——升级前须在 env 文件补
  `AGENT_RUNTIME_WATCH_INTERVAL` 与 `AGENT_RUNTIME_HEALTH_PROBE_TIMEOUT`
  两行(默认 10 / 3.0)。
- 遗留:`HEALTH_FAIL_THRESHOLD`(判死阈值)与 `WATCH_LOCK_TTL`(选主锁,
  watch_interval 调大到 ≥15s 时应同步调大)仍为常量硬编码,有需求再开。
