# AgentServer 默认 namespace 跟随 runtime 自身 ns(POD_NAMESPACE 兜底)

- 日期:2026-09-16
- 涉及模块:service-core(config)/ 部署模板 / session_manager(文档)/ resource_manager(文档)/ 测试

## 背景与动机

此前 AgentServer 目标 ns 与 runtime 自身 ns 是两个独立配置:部署模板把
`AGENT_RUNTIME_AGENTSERVER_NAMESPACE` 注入成 `AGENT_RUNTIME_DEFAULT_NAMESPACE`,仅当模板
namespace 为空才兜底到它——而 DB 列 `NOT NULL DEFAULT 'default'`、wire 省略也落
`'default'`,兜底几乎永不触发,实际 ns 完全由模板逐个显式指定。联调要双 ns 各配一份,
runtime 搬家(换 ns 重新部署)后模板不跟、AgentServer 仍落旧 ns。

需求(wmq 确认,边界明确):**模板下发 namespace 为空时用 runtime 自身 ns(downward
API 注入 env `POD_NAMESPACE`),否则用下发的值;不改 DB schema、不改对外契约、
不迁移存量库。**

## 方案

- **解析链(单点改动)**:`default_namespace = POD_NAMESPACE(downward API 自身 ns)> "default"`——`or` 链把"env 显式设空串"归一为继续下探。**`AGENT_RUNTIME_DEFAULT_NAMESPACE` 变量整体移除(2026-09-16 wmq 确认:不保留运维覆盖旋钮),设了也忽略**。
- **空串 = 继承语义**:wire 显式 `"namespace": ""` 一路存活到 k8s 层
  (`pod_spec.get("namespace") or self.default_namespace`,Real/Fake 同款,历史代码本来
  就是 falsy 兜底,**k8s.py 零改动**);**省略仍落 `'default'`**(列默认),表达继承必须
  显式空串——单义,不重载既有值。
- **部署模板**:删 `AGENT_RUNTIME_DEFAULT_NAMESPACE: <<AGENT_RUNTIME_AGENTSERVER_NAMESPACE>>`
  注入(不删则显式 env 永远压过 POD_NAMESPACE),换 downward API
  `fieldRef: metadata.namespace` 注入 `POD_NAMESPACE`;`AGENT_RUNTIME_AGENTSERVER_NAMESPACE`
  变量保留,仅继续渲染 AgentServer 侧 RBAC(存量显式指定独立 ns 的模板仍需)。
- **否决备选**:改 DB 列默认值为 `''`(需存量 ALTER + 省略语义变化,违反"不动库/契约"
  边界);cleanup/k8s 层各自独立解析 own-ns(语义分裂,default_namespace 单点解析最简)。

被否决的隐性耦合提醒(有意为之):控制面 ns 与业务 Pod ns 仍解耦——显式模板 ns 优先;
"跟随"只是**空值时的默认路径**(钉死 ns 的唯一途径 = 模板显式下发)。

## 实现

- `src/agent_runtime/config.py`:`from_env` 的 default_namespace 改 `POD_NAMESPACE or "default"`,删 `AGENT_RUNTIME_DEFAULT_NAMESPACE` 读取。
- `deploy/agent_runtime.template.yaml`:env 块 `AGENT_RUNTIME_DEFAULT_NAMESPACE` 注入 →
  `POD_NAMESPACE` downward API;`deploy/agent_runtime.env.example` 注释更新
  (AGENTSERVER_NAMESPACE 仅渲染 RBAC)。
- `src/agent_runtime/main.py`:`_collect_runtime_identity` 链插入 POD_NAMESPACE
  (NAMESPACE → POD_NAMESPACE → SA 文件 → …,/healthz 展示与 deploy 兜底对齐)。
- 不动:DB schema / wire 契约 / k8s.py / Lua 与 Redis 键(无需 verify_redis_cluster.py)/
  e2e 与压测脚本(均 seed 显式 ns)/ 存量库。

### 语义注记

- **deploy_ver**:空串以字面量进指纹;runtime 搬家(ns 变)不触发日落——与 kubeconfig
  同类的"只影响新 deploy"例外,老 ns 的 Pod 按 idle/pod_ttl 自然回收(Pod 记录存解析后
  ns,回收/删除跨 ns 无歧义)。
- **cleanup 默认目标**:集群内不带 ns 的 cleanup 兜底从 `"default"` 变为 runtime 自身 ns;
  CLAUDE.md"空目标必须无匹配 label_selector"红线继续适用。
- RBAC:AgentServer 落 runtime 同 ns 时第二份 Role/RoleBinding 仍由
  `AGENT_RUNTIME_AGENTSERVER_NAMESPACE` 渲染(默认可与其同值,重复 apply 无害);模板显式
  指向独立 ns 时必须保留该 ns 的 RBAC,否则日落回收 403 死循环(sweeper 每拍重试)。

## 验证

- 单测(+5):`tests/test_config.py` 新建(from_env 三例:POD_NAMESPACE 生效/空串下探/
  字面 default);`test_k8s_pod_body.py` 补 `namespace=""` 落 default_namespace +
  显式 ns 优先;`test_config_store.py` 补空串 sync 接受+往返+deploy_subset 携带。
- 全量 pytest:**534 passed, 9 skipped**(61s)全绿。
- 渲染冒烟:`render_and_apply.sh --render-only` 产物确认 downward API
  `POD_NAMESPACE` 注入(agent_runtime.yaml:167-170)、`AGENT_RUNTIME_DEFAULT_NAMESPACE`
  注入已移除、RBAC 第二份 Role/RoleBinding 仍渲染在 `agent-runtime-e2e-wmq`(:48/:61,
  存量显式 ns 模板继续可用)。

## 影响面

- 文档同步:spec/service-core.md(env 表 default_namespace 行)、spec/resource-manager.md
  (RealK8sPodClient 新增 namespace 解析小节)、spec/session-manager.md(模板表段补空串
  语义)、HLD template 字段表 namespace 行。
- 配置注意(升级破坏面):`AGENT_RUNTIME_DEFAULT_NAMESPACE` 变量已移除,既有部署若设了它,
  升级后**静默失效**(空 ns 模板一律落 POD_NAMESPACE/自身 ns);`AGENT_RUNTIME_AGENTSERVER_NAMESPACE`
  不再驱动运行时兜底,只渲染 RBAC。钉死 ns 的唯一途径 = 模板显式下发。
- 存量模板显式 ns(如 wmq 联调 `agent-runtime-e2e-wmq`)行为不变;后续想跟随 runtime
  的模板自行改下发空串即可(会触发一轮 A 类日落,预期行为)。

## 2026-09-17 补丁:SA 文件兜底(cyz2 403 事故复盘)

**现象**:cyz2 联调(镜像 0.0.28s,含本篇"删 AGENT_RUNTIME_DEFAULT_NAMESPACE"代码)
创建 AgentServer 全 403——`cannot create pods in the namespace "default"`。
启动日志 `config summary: namespace=default`。

**根因**:**镜像升级与部署模板重渲染两条时间线断层**。cyz/cyz2 的 Deployment 由
swarm 仓 `deploy/enterprise/templates/runtime.template.yaml` 渲染,旧模板仍注入
`AGENT_RUNTIME_DEFAULT_NAMESPACE=cyz2`(新代码忽略)且无 `POD_NAMESPACE` → 空 ns
模板落字面 `"default"` → SA 只在自身 ns 有 RBAC → 403。仅修模板不够:存量环境
(wx2/wx3/zxy/zyq)单独升镜像而不重渲染,会复刻同款故障。

**修复(代码层自愈,零部署配置)**:`config.own_namespace()` 解析链改为
`POD_NAMESPACE env(显式覆盖)> in-cluster SA namespace 文件(Pod 必挂)> "default"`
——任何镜像/模板升级顺序下自动正确。配套:swarm 模板同注入 POD_NAMESPACE 并清除
两个死变量(`AGENT_RUNTIME_DEFAULT_NAMESPACE`/`AGENT_RUNTIME_SCOPE_FULL_TIMEOUT`,
后者随场景 F 拆队列已删)。

**验证**:tests/test_config.py 扩至 5 例(env 生效/SA 兜底/env 压过 SA/空串下探/
双缺失字面 default);全量 pytest 全绿。
