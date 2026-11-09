# deploy_mode=subprocess:本机进程级拉起 AgentServer(轻量单机形态)

- 日期:2026-11-03
- 里程碑 / commit:M9(jiuwenswarm-ee-lite)/ 待提交
- 涉及模块:resource_manager / service-core / 部署 / 文档

## 背景与动机

jiuwenswarm 企业版需要一个**轻量单机部署**(jiuwenswarm-ee-lite):无 Docker、
无 K8s、无外部 Redis/MySQL,四服务(身份中心/管理面/runtime/gateway)全部本机
进程拉起。其中 AgentServer 的"Pod 供给"需要等价物——把 pod_spec 容器规范形
翻译为本机子进程。management 包的 `ProcessServiceHandler` 已验证进程级拉起
可行,但它面向 management 自身的会话服务,不实现 `K8sPodClient` 契约,不能
直接给 RM orchestrator 用。

不改的话:轻量版只能起 K8s(FakeK8sPodClient 不会真起进程,用户消息链路
route 拿不到 pod_sse_url)或引入完整容器栈,违背单机轻量目标。

## 方案

- 新增 `AGENT_RUNTIME_DEPLOY_MODE=subprocess`(第三种供给模式):
  RM 构造 `SubprocessPodClient` 代替 Real/Fake K8s 客户端,**对上层
  (orchestrator/sweeper/watch/reconcile)完整实现 K8sPodClient 契约**,
  语义逐字对齐 K8s——上层代码零分支、零改动。
- 端口策略:单机多进程不可共用模板声明口 → **逐端口动态分配空闲口**;
  `PodDeployInfo` 新增 `published_port`(实际 SSE 发布口),orchestrator 拼
  `pod_sse_url` 优先取它,K8s 模式恒 0 回退模板声明口(存量行为不变)。
- 每 Pod 独立工作区(`JIUWENSWARM_DATA_DIR/HOME/USERPROFILE` 重定向),
  防同机多实例 GatewayLock/配置文件互踩。
- 拉起工艺沿用 management `ProcessServiceHandler` 成熟做法:引导环境白名单
  (不继承网关业务环境)、Windows `CREATE_NEW_PROCESS_GROUP`+taskkill /F /T、
  POSIX `setsid`+killpg、TCP 就绪等待(不做 WS 握手——enforce 鉴权下无
  token 握手必拒,TCP 与 K8s readiness 探针同语义)。
- deploy 不就绪时异常携带 pod_id/namespace 上抛,孤儿清理交 orchestrator
  既有两级推导兜底(与 K8s「create 成功但未 Ready 留物理 Pod」语义一致)。
- `probe_pod_health` 直接返回 True(2026-11-05 补齐):K8s 的「Running 但
  SSE hang」探测场景对子进程不适用,进程死活由 sweeper dead-pod 扫描经
  `get_pod` phase 判定兜底,跳过 HTTP 探测(此前缺失该方法,sweeper
  watch 每拍 AttributeError)。
- 与 link mTLS enforce 不兼容(子进程 HTTP 明文口),启动 fail-fast raise。
- 被否方案:复用 management ProcessServiceHandler(不实现 K8sPodClient 契约,
  会迫使 orchestrator 加模式分支,违反「上层零改动」);嵌套 Docker
  (引入 Docker 依赖,不轻量)。

## 实现

- `resource_manager/subprocess_client.py`(新增):`SubprocessPodClient` 全量
  实现 start/close/deploy/delete/get_pod/list_pods。env 三层合成:引导白名单
  < 模板 env < 进程覆盖(端口映射/工作区/AGENT_SERVER_HOST/AGENT_HTTP_ENABLED
  兜底)。envFrom 不支持记 WARNING 不阻断;command/args(镜像入口语义)不翻译。
- 模板 env 值支持 `${VAR}` 引用(2026-11-05 补,语义对齐 K8s `$(VAR)`:仅 env
  值、不递归、不展开 command/args):展开源 = runtime 进程自身环境,deploy 期
  在 `build_env` 里展开。pod_spec 是管理面下发的静态快照,单机部署里"随部署
  位置变化"的值(如与 gateway 共享的 sqlite 路径)不应在配置里写死绝对路径——
  launcher 把绝对值注入 runtime 进程 env,模板里写 `${VAR}` 占位。解析不到
  保留原样并记 WARNING(不静默给字面量路径)。
- `config.py`:`_env_deploy_mode` 接受 full|routing|subprocess(非法 raise)。
- `resource_manager/models.py`:`PodDeployInfo.published_port: int = 0`。
- `resource_manager/orchestrator.py`:`_deploy_and_register` 的 sse_port 取
  `info.published_port or main_sse_port(main)`。
- `main.py`:`build_resources` local/server 分支按 deploy_mode 构造
  SubprocessPodClient;`start()` subprocess×mTLS enforce fail-fast。
- 新 env(均有缺省):`AGENT_RUNTIME_SUBPROCESS_PYTHON/MODULE/LAUNCHER_SCRIPT/
  PORT_ENV_MAP/DATA_ROOT/READY_TIMEOUT`。

## 验证

- 单测:agent_runtime 全量 `uv run pytest -q`:658 通过 / 2 失败 / 9 跳过;
  2 失败(link 物料同步、link profile 运行时)经 stash 对照确认为**存量环境
  失败**(OpenSSL 证书 AKI 校验),与本改动无关,零回归。
- 轻量栈实测(jiuwenswarm-ee-lite):identity(8770)/manager(8765)/
  runtime(8091) 进程级启动+就绪探针全通过;gateway 侧依赖见该仓 README。
- 用户消息链路 e2e(runtime route → SubprocessPodClient deploy → agentserver
  子进程 → SSE 回连)见 jiuwenswarm-ee-lite 仓 tests/e2e(进行中)。

## 影响面

- 同步文档:`docs/spec/service-core.md`(deploy_mode 行)、
  `docs/spec/resource-manager.md`(_deploy_and_register 的 sse_port 推导)。
- 配置与兼容:默认 full 行为逐字节不变;routing 不受影响;subprocess 仅
  显式选择时生效。`PodDeployInfo` 新字段带默认值,存量构造处无需改动。
- 遗留:subprocess 模式的专属单测(独立进程树断言)随 light 仓 e2e 一并
  落地;K8s 真镜像发布门禁不受影响。
