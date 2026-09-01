# agent-runtime 端到端测试用例说明（e2e-test-cases）

- 日期:2026-08-18(M6 冒烟固化于 2026-08-15;M7 多副本补全于 2026-08-18;
  2026-08-27 起各阶段补「前置/内部状态变迁/留白」用于人工审遗漏;
  2026-08-28 补 §5.2 审计实锤回归网 16 用例、键类型 ZSET 迁移同步;
  2026-08-28 补阶段 0m/2c 全量真实规格(--with-mounts,主容器三挂载+PVC 预置+
  sidecar 完整规格逐字段断言)、阶段 12 清空断言语义修正)
- 读者:执行/评审端到端验收的工程师
- 配套:语义权威 = `../design/Agent-Runtime-HLD.md`(§6 场景、§5 键表);脚本本体在
  `applications/agent_runtime/scripts/`;本文回答"**每个 e2e 用例:场景、输入、预期输出**"。

---

## 1. 总览:端到端测试体系

| 层 | 入口 | 规模 | 依赖环境 | 退出码 |
|---|---|---|---|---|
| 进程内双实例 | `uv run pytest tests/integration/test_multi_replica.py` | 14 用例 | 无(离线,fakeredis) | pytest 标准 |
| **审计实锤回归网** | `uv run pytest tests/integration/test_audit_repro.py` | 16 用例 | 无(离线,fakeredis) | pytest 标准 |
| 集成冒烟(M6) | `./scripts/integration_smoke.sh`(sidecar 阶段加 `--with-sidecar`,全量规格加 `--with-mounts`) | 121 项断言(全规格形态;2026-09-01 真镜像 0.0.9s 门禁 121/121) | 单实例 server 模式 + 真 Redis/MySQL/K8s | 0/1/2 |
| 多副本 e2e(M7) | `uv run --no-sync python scripts/e2e_multi_replica.py` | 35 项断言 | K8s 多副本 + Service LB + 真 Redis | 0/1/2 |
| 压测/浸泡 | `uv run --no-sync python scripts/load_test.py` | 3 场景 | 任意入口(建议 LB) | 0/1 |

退出码约定(两个 e2e 脚本一致):**0**=全过(含 SKIP/DEGRADED);**1**=有 FAIL;
**2**=前置自检未过。可直接接 CI。

---

## 2. 通用约定

### 2.1 请求信封(全部用例的输入格式)

五个对外端点均为 `POST /api/session/{route|touch|config_sync|config_refresh|cleanup}`,请求体:

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
  "rawdata": {}                        // config_sync/cleanup 的载荷在此(config_refresh 必须为空)
}
```

成功响应 `rawdata` 携带业务字段(`pod_id`/`pod_sse_url`/`touched`/`ok`/`scopes_refreshed`/`cleaned`);
失败响应顶层 `error_code`(+可重试错误带 `retry_after`)。

### 2.2 错误码契约(断言依据)

| error_code | HTTP | retry_after | 语义 |
|---|---|---|---|
| `SCOPE_QUEUE_FULL` | 503 | ✅ | 等待队列满,快失败 |
| `SCOPE_FULL_TIMEOUT` | 504 | ✅ | 队列内等待超时(2026-09 起另有推导式总预算 `scope_full_timeout+ready_timeout+10s`,need_acquire 无上界循环同款 504;冷部署不受队列预算误伤) |
| `NO_POD_AVAILABLE` | 503 | ✅ | acquire 失败(MaxPodsReached/DeployFailed 映射) |
| `STATE_UNAVAILABLE` | 503 | ✅ | 状态后端(Redis/DB)连接级故障,handler 层翻译(2026-09;真环境注入手段有限,单测 `test_infra_faults.py` 覆盖) |
| `CONFIG_NOT_FOUND` | 503 | ❌ | 无匹配规则/模板禁用 |
| `VALIDATION` | 400 | ❌ | 参数错(含 config_refresh 带载荷) |
| `CONFIG_SYNC_BUSY` | 409 | — | 上次热更新未完成/日落待回收;`lock:config_sync` 被 config_sync 或 config_refresh 占用 |

### 2.3 Redis 真相源(e2e 直接断言的键)

- `{session_manager}:scope:{sid}:sessions`(SET,SCARD=scope 闸门)/`:pods`(ZSET,first-fit 候选)
  /`:waiters`(**ZSET**,score=deadline;2026-08-28 起 SET→ZSET,断言用 ZCARD);
  `{session_manager}:routing:snapshot`(STRING,路由快照)
- `{session_manager}:session:{sid}`(HASH)/`session_expiry`(ZSET)/`pods:registered`(SET)
- `{resource_manager}:resource:scope:{sid}:pods|idle|config|deploying`(deploying 为 **ZSET**,
  score=deadline,断言用 ZCARD)
- `{resource_manager}:resource:pods:all`、`resource:pod:{pod}:idle_since`
- `{agent_runtime:job:*}:winner:{epoch}` / `:candidates:{epoch}`(选主元数据,TTL≈3s)

### 2.4 前置自检与防误刷(两个 e2e 脚本共享 `e2e_lib`)

- 服务/LB 在线(`/healthz` 或 `/docs` 200)、Redis `PING` + `aof_enabled=1`、kubectl 可用、
  专用 namespace 存在(缺则自动创建)。
- **FLUSHDB 防误刷**:目标 Redis DB 存在 `{session_manager}:`/`{resource_manager}:`/`agent_runtime:`
  (选主执行锁)/`{agent_runtime:`(选主抽签键,hash tag 同槽)之外前缀的 key 即视为指错库,
  **中止**(除非显式 `--force-flush`)。务必独立 DB 编号(cluster 部署无库号概念,只能整集群
  FLUSH,更须专用)。

---

## 3. 集成冒烟(M6):`scripts/e2e_hld_acceptance.py`(75 项;`--with-sidecar` 时 +5 项 → 80;`--with-mounts` 时 +24 项 → 99,与 sidecar 叠加 + 带 `--agent-env` 共 105——2026-08-28 双真镜像门禁实测 104/105,唯一 FAIL 为真镜像 uid=1000 写 root 属主 PVC 被拒的真实缺陷,见阶段 2c 留白)

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
| `tpl-box` | cc=2 pc=1 pod_ttl=3600 + `sidecars=[box-standin]`(仅 `--with-sidecar` 下发) | **sidecar 多容器**(阶段 2b);pod_ttl 加大让 box Pod 全程长存,否则中途被自然回收会使阶段 4/5 的注册表计数(`--with-sidecar` 时 +1)随时序漂移 |
| `tpl-mnt` | cc=3 pc=2 session_ttl=60 pod_ttl=3600 **min_idle=1** ready_timeout=240 + 显式 `container_port=8086`/`readiness 5s/5s`/`pod_name=agentserver-mnt` + 主容器 **cm/hp/pvc 三挂载** + `sidecars=[jiuwenbox 完整规格]`(仅 `--with-mounts` 下发) | **全量真实规格**(阶段 0m/2c):对齐真实 config_sync 请求形状;min_idle=1 → stage1 下发后 autoscale 即预热(暖 Pod 不进 SM 注册表,2c route 前不可见);pod_ttl=3600 长存,阶段 4/5 计数再 +1 |

scope:`e2e-main|e2e-f|e2e-warm|e2e-bad|e2e-nat` 各按 `routing_rules` 表达式串绑一模板
(e2e-main 故意带 or 支 `group_id in ('e2e-main') or user_id in ('e2e-vip')` 验收混合表达式;
其余单条件 `group_id in (...)`;**不播通配兜底**——使「未知属性组合 → CONFIG_NOT_FOUND」可验收;
`--with-mounts` 时追加 `e2e-mnt`(表达式 `group_id in ('e2e-mnt')`,**不照搬真实请求的空串
通配**——通配兜底会打掉 CONFIG_NOT_FOUND 验收));
可选 DB 落库校验(mysql/psql 客户端在场时两表行数=模板/scope 数,否则 SKIP)。

### 3.1 阶段与用例(场景 → 输入 → 预期)

**阶段 0 前置自检**(5 项):服务在线 200 / Redis AOF=1 / kubectl 可用 / ns 就绪 / 防误刷守卫通过。

**阶段 0m:全量规格资源预置**(6 项,仅 `--with-mounts` 时执行;位于前置自检之后、清场之前——
预置失败返回 False,在 FLUSHDB/删 Pod 等破坏性动作**之前**中止,退出码 2):

| # | 场景 | 输入 | 预期输出/断言 |
|---|---|---|---|
| 0m-1~2 | ConfigMap 资源就绪 | `kubectl create configmap agent-config-cm / box-policy-cm`(幂等,subPath key 逐字等于 `config.yaml`/`policy.yaml`;已存在则复用,内容不断言播种值——阶段 2c 按 CM 当前值比对) | created/AlreadyExists |
| 0m-3 | 获取可调度节点 | `kubectl get nodes -o json` 取首个 Ready 且无 NoSchedule/NoExecute 污点者 | 非空(PV nodeAffinity 钉可调度节点;误指污点 master → Pod 永久 Pending「volume node affinity conflict」且 PV 亲和不可变,2026-08-28 手工补验教训) |
| 0m-4 | 双 PVC 预置 | **「已 Bound 即复用,缺失才静态供给」**:PVC 已存在且 Bound → 直接复用(顺手清自己的孤儿 e2e-* PV);缺失 → `kubectl apply -f -`(hostPath PV + 空 storageClassName PVC + `volumeName` 预绑);存在但 Pending → 删后重建 | 预置成功(2026-08-28 真环境实测:ns 里已有真实实验留下的 agent-data-pvc→pv-agent-data 手工对,`volumeName` 不可变,盲目 apply 必 Invalid——复用既绑也更贴近生产:PVC 由谁供给不归模板管) |
| 0m-5 | hostPath 数据目录放权 | **按各 PVC 实绑 PV 反查 hostPath**(复用既绑手工对时路径不由我们决定——2026-09-01 实测 agent-data-pvc→pv-agent-data→/mnt/pv-agent-data;自建 PV 的清单目录一并放,幂等),在节点上 `mkdir -p && chmod 0777`(本机直跑/远端 ssh passwordless) | 无错误(kubelet `DirectoryOrCreate` 建的是 root:root 0755,真镜像主容器多为非 root 用户,不放权则 2c 的 PVC 写入实证被目录权限挡——2026-09-01 真镜像门禁实测;替身 influxdb 以 root 跑故历史无感。失败仅记录不中止,2c 带证据说话) |
| 0m-6 | 双 PVC Bound | 轮询 ≤30s;卡 Released(PVC 曾删而 PV claimRef 钉死)→ `patch pv claimRef=null` 兜底重等 | 两 PVC phase=Bound |

> CM/PVC 跨轮复用不清理(create/复用幂等);宿主 `/mnt/host-test` 由 hostPath `DirectoryOrCreate` 自建,无需预置。

**阶段 1–2:播种 + 首次部署/亲和/打包/保活/幂等**(前置:清场后的空集群、空快照)

| # | 场景 | 前置状态 | 输入 | 预期输出/内部状态变迁 | 
|---|---|---|---|---|
| 1a | H0 零 Pod 基线 | FLUSHDB+TRUNCATE+删 ns Pod 后,配置未下发 | — | ns 内零 agentserver Pod;`routing:snapshot` 不存在(服务启动不拉 Pod——启动期不依赖配置) |
| 1 | —(种子) | 1a 之后 | 1×config_sync 全量 `{templates:N, scopes:N}`(基础 N=5;`--with-sidecar` +1;`--with-mounts` 再 +1,可叠加) | 200,`templates_synced=scopes_synced=N`;写 DB→原子 SET 快照→逐 scope 推 RM config(带 pod_spec);DB 行数 [N,N](可选) |
| 1b | H0 无请求预热 | 种子后,零 route | —(等待 ≤90s;`--with-mounts` 时同窗并发预热两个 min_idle scope) | autoscale(1s tick)为 e2e-warm 部署 1 个热备:`resource:scope:e2e-warm:idle`=1 且 K8s 真实存在(**配置驱动预热**;暖 Pod 不进 SM 候选集,不经任何请求;`--with-mounts` 时 e2e-mnt 暖 Pod 同理,断言在阶段 2c) |
| 2 | C 首次部署 | e2e-main 空(无候选 Pod) | `route(s1, e2e-main)` | 200,`pod_id` 以 `agentserver-` 开头,耗时≈一次 deploy(含 K8s create+等 Ready;内部:SM 候选空→need_acquire→RM deploy→REGISTER 入候选→重跑仲裁 placed) |
| 3 | C 物理真象 | 上一步返回的 pod | — | `kubectl get pod` 存在且 Ready(控制面声称的 Pod 真实存在) |
| 4 | C SSE 直连地址 | 同上 | — | `pod_sse_url` 以 `http://` 开头、host=Pod IP、端口/路径来自模板(替身为 :8086/sse) |
| 5 | C RM 池登记 | 同上 | — | `resource:scope:{MAIN}:pods` ZCARD=1(RM 侧池与 SM 候选一致) |
| 6 | A 亲和续期 | s1 已绑 pod1 且未过期 | `route(s1)` 再次 | 200 且同 `pod_id`(走 refresh 分支:仅续期 expiry,不重抢 scope 额度、不换 Pod) |
| 7 | A 会话不增 | 同上 | — | `scope:{MAIN}:sessions` SCARD=1(续期不新增会话四处记录) |
| 8 | B first-fit | pod1 尚有空位(pc=2,s1 占 1) | `route(s2)` | 200 且 `pod_id`=pod1(接入序 first-fit 打包,不新部署) |
| 9 | B per-Pod 闸门 | 同上 | — | `pod:{MAIN}:{pod1}:sessions` SCARD=2(打满) |
| 10 | C 扩 Pod | pod1 满(2/2)、未达 max_pods=2 | `route(s3)` | 200 且新 `pod_id`≠pod1(need_acquire→RM deploy pod2) |
| 11 | C 候选集 | 同上 | — | `scope:{MAIN}:pods` ZCARD=2(接入序即 first-fit 序) |
| 12 | E 保活 | s1 存在且未过期 | `touch(s1)`(间隔≥1.2s) | 200 `touched=true`;`session_expiry` 分数增大(=now+session_ttl,阻止老化) |
| 13 | E 未命中 | 会话不存在 | `touch(nope)` | 200 `touched=false`(gateway 回退重新 route 的信号) |
| 14 | 幂等 | s3 已成功返回 | 同 `request_id` 两次 `route(s3)` | 两次 `pod_id` 一致;SCARD 仍=3(重试不重抢额度、不重建 Pod) |

> 留白(阶段 1–2 不覆盖):route 与配置下发的并发交错(见审计网 C12)、亲和期间 Pod 死亡(阶段 9 只测外部 kubectl 删,审计网 C6/C8 测内部竞态)、四参缺省的 400 细粒度(阶段 13)。

**阶段 2b:sidecar 多容器**(5 项,仅 `--with-sidecar` 时执行/计数):tpl-box 的 sidecar 为
**influxdb:1.8 替身改绑 8096**(`INFLUXDB_HTTP_BIND_ADDRESS=:8096` + `INFLUXDB_BIND_ADDRESS=:8098`
——主容器已占 8086,同 Pod 共享网络命名空间必须错开端口;RPC 8088 不错开则双实例抢绑 →
sidecar CrashLoop,2026-08-27 真环境实测)+ TCP readiness + ConfigMap subPath 挂载
(`e2e-box-cm` 的 `policy.yaml` 单 key 挂到 `/etc/box/policy.yaml`);非特权——验证多容器渲染/
readiness 门控/挂载链路;`--sidecar-image <真 jiuwenbox 镜像>` 时 sidecar 自动切完整 jiuwenbox 规格
(特权四件套 + cgroup hostPath + 8321 + `JIUWENBOX_LISTEN=http://0.0.0.0:8321`——该 env 的 scheme
必须 `http://`/`unix://`,`tcp://` 被新版镜像拒绝,2026-08-28 sandbox 0.0.6s 实测;需 namespace
允许特权容器):

| # | 场景 | 输入 | 预期输出/断言 |
|---|---|---|---|
| 2b-1 | ConfigMap 资源就绪 | `kubectl create configmap e2e-box-cm`(幂等) | created/AlreadyExists(重跑可重入) |
| 2b-2 | C 双容器部署 | `route(s-box, e2e-box)` | 200,`pod_id` 以 `agentserver-` 开头(deploy 等待**全部容器** Ready 才返回) |
| 2b-3 | C 多容器真象 | —(上一步的 pod) | `kubectl get pod -o jsonpath={.spec.containers[*].name}` 含 `agent` 与 `box-standin` |
| 2b-4 | C sidecar readiness 门控 | — | 返回的 `pod_sse_url` 已可用——TCP 探针(8096)通过是 Pod Ready 的前置 |
| 2b-5 | C ConfigMap subPath 真挂载 | `kubectl exec -c box-standin -- cat /etc/box/policy.yaml` | 内容为 CM 播种的 `e2e-box-policy-standin`(挂载真实生效,非仅 spec 渲染) |

**阶段 2c:全量真实规格**(19 项 +`--agent-env` 时 1 项,仅 `--with-mounts` 时执行):tpl-mnt 按
**真实 config_sync 请求形状**逐字段复刻(主容器 cm/hp/pvc 三挂载 + 显式 `container_port=8086`
/`readiness 5s/5s`/`pod_name=agentserver-mnt` + sidecar jiuwenbox 完整规格:特权四件套 +
8321 + cm/hp/pvc 三挂载 + TCP 探针),2026-08-28 双真镜像手工全量验证(feature 记录)的 e2e 化。
sidecar 镜像随 `--sidecar-image` 分流:真 jiuwenbox 用 8321 + `JIUWENBOX_LISTEN`,替身 influxdb
错开 8096/8098——**特权四件套与三挂载两种模式都下发**(渲染路径不依赖镜像,默认替身跑即可断言);
注意其 ConfigMap 引用 `box-policy-cm`(阶段 0m 预置),**不是** 2b 的 `e2e-box-cm`(后者被
`--with-sidecar` 门控,单独开 `--with-mounts` 时不存在):

| # | 场景 | 输入 | 预期输出/断言 |
|---|---|---|---|
| 2c-1 | H0 全量规格预热 | —(等 ≤120s) | `scope:e2e-mnt:idle`≥1 且 Pod Ready——PVC 未 Bound/apparmor 被拒/CM 缺失都卡在此(而非 route 超时后一片红) |
| 2c-2 | C 零冷启动复用 | `route(s-mnt, e2e-mnt)` | 200 且 `pod_id` ∈ 阶段 2c-1 暖池集合(全量规格暖 Pod 被复用,不冷启) |
| 2c-3 | 双容器真象 | `kubectl get pod -o json` | 容器名集合 = `{agent, jiuwenbox}`(≠ 2b 的 box-standin) |
| 2c-4 | 显式 container_port | — | 主容器 ports 含 `containerPort=8086`(=sse_port → 单端口声明 `sse`) |
| 2c-5~7 | 主容器三挂载渲染 | — | volumeMounts:`/etc/agent/config.yaml`(subPath=config.yaml)/`/mnt/host-test`(readOnly=true)/`/var/lib/agent` 各就位 |
| 2c-8 | 主容器 readiness | — | httpGet path=模板 health_path、port=8086、initialDelay/period=5/5 |
| 2c-8b | 主容器 envFrom 渲染 | — | secretRef(e2e-agent-secret,**prefix=E2E_**)+ configMapRef(e2e-agent-env-cm,**无 prefix**)逐条透传——载荷故意一有一无,验证 prefix 可选(2026-09-01 修正:旧断言「全部有 prefix」与载荷自相矛盾,恒 FAIL);sidecar configMapRef(e2e-box-env-cm) |
| 2c-9 | agent_env 注入 | —(仅 `--agent-env` 时断言) | 主容器 env 逐项 == 模板 agent_env(真镜像三件套 AGENT_HTTP_*) |
| 2c-10 | sidecar 特权三件套 | — | securityContext:privileged=true、capabilities.add={SYS_ADMIN,NET_ADMIN}、seccompProfile=Unconfined |
| 2c-11 | sidecar apparmor | — | Pod annotation `container.apparmor.security.beta.kubernetes.io/jiuwenbox=unconfined` |
| 2c-12 | 卷全景 | — | Pod volumes 按内容断言:2 ConfigMap + 2 hostPath(/mnt/host-test、/sys/fs/cgroup)+ 2 PVC 各就位 |
| 2c-13 | sidecar TCP 探针 | — | tcpSocket port=8321(真)/8096(替身)、5s/5s |
| 2c-14~17 | 主容器 exec 实证 | `cat`/`ls -d`/`touch`/`sh -c 'echo>…&&cat'` | CM 内容可见;hostPath 目录存在(DirectoryOrCreate);`/mnt/host-test` 写入被拒(Read-only file system);`/var/lib/agent` PVC 可写回读 |
| 2c-18~20 | sidecar exec 实证 | 同上四连(-c jiuwenbox) | `policy.yaml` 内容可见;`/sys/fs/cgroup` 宿主 cgroup 可见;`/var/lib/jiuwenbox` PVC 可写回读 |

> 前置:阶段 0m 资源预置通过;tpl-mnt 已随阶段 1 下发(min_idle=1,暖 Pod 应已存在)。
> 内部状态变迁:route 命中暖 Pod → SM 注册(此后 mnt Pod 进 `pods:registered`,阶段 4/5 计数 +1)
> → Pod 全生命周期经历阶段 4 老化/11b 巡检/12 cleanup(全量卷+特权 Pod 不被误杀是白赚的回归面)。
> 留白:exec 类断言依赖镜像内有 shell/cat/ls(真 jiuwenbox 无 shell 时如实 FAIL——本身即真实场景
> 信息);探测契约 A 类变更矩阵(health_path/sse_port 变更后老 Pod)仍归真镜像门禁;readiness http
> 探针类型、sidecar 资源限额/run_as_user、nfs_* 挂载未覆盖(见 §8.3)。
> **已知真实缺陷(2026-08-28 双真镜像门禁实测,PVC 写回读 FAIL)**:agentserver 真镜像以
> uid=1000(app) 运行,PVC 后端目录 root:root 0755 → `/var/lib/agent` 写入 Permission denied
> (替身 influxdb 以 root 跑、sidecar 特权,均掩盖此问题)。Template schema 无 pod 级
> fsGroup(修法需产品决策,修前门禁该项保持红);主容器 securityContext 已具备
> `run_as_user/group` 字段(026450be,默认 None 不渲染——但**改 uid 不改卷属主**,单靠它
> 治不了本缺陷)。**考证与决策(2026-08-28)**:老体系靠部署脚本对存储后端 `chown 1000:1000/
> chmod 777` 预属主(NFS/hostPath 的 volume plugin 均不做 fsGroup 属主管理——Pod spec 层
> 解决不了);`fs_group` 字段方案曾同日实现并真环境验证(渲染生效,但 hostPath backend 卷
> 穿透不到目录属主),按需求方决定**整体回退暂缓**——本环境现阶段解法为存储侧预属主
> (运维手段,同老体系),详见 feature/2026-08-e2e-full-mounts-stage.md。
> run_as_user/group 与 node_name 系 deploy tool 联调引入的合法 A 类能力(2026-08-29 决策
> 确认),真环境生效实证仍列 §8.3 缺口。

**阶段 3:M(B 类)pod_ttl 热更新**(前置:阶段 2 结束,s1–s3 活跃、2 Pod 在役;3 项)

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 15 | config_sync 全量(tpl-e2e `pod_ttl:120`,纯 B 类——deploy_ver 不变) | 200 `ok=true`(**不**触发日落:老 Pod 原地继续服务) |
| 16 | —(1s 后) | RM `scope:config` 的 `pod_ttl`="120"(update_pool_config 主动推送,立即生效) |
| 17 | — | `routing:snapshot` 已原子覆盖(下次 route 即见新值) |

> 留白:B 类仅验 pod_ttl 一个字段;session_ttl/scope_concurrency/pod_concurrency 的 B 类生效路径未逐字段验(部分由审计网 C1a 与 §5 S6 覆盖)。B 类调小低于现活跃数的回收语义未覆盖(存量超限不驱逐,只老化回落)。

**阶段 4:D 老化回收**(前置:s1–s3 活跃、2 Pod 在役、pod_ttl 已热更为 120;**自然到期,零回拨/零直改键**(2026-09-01 改):tpl-e2e `session_ttl=30`,真等到期;5 项)

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 18 | —(自然到期,阶段 2 落位起 ≤45s) | `scope:sessions` 清空(sweeper 1s tick 扫 `session_expiry` 到期集→逐个 EVICT+PUBLISH free) |
| 19 | — | 会话四处全清(session HASH/expiry/pod 集/scope 集) |
| 20 | — | 空 Pod pass → idle_consider → RM `idle` 暖池 2 个(空闲 Pod 回暖池等待回收/复用) |
| 21 | — | 不变量 5:`pods:registered` 仍 2 个(`--with-sidecar` 时 3,box Pod 长存;`--with-mounts` 时再 +1,mnt Pod 2c 已 route;待 RM 回收后清) |
| 22 | — | 两个 Pod `phase`="idle" |

> 2026-09-01 实测教训(原回拨手法的缺陷):回拨用 zadd+hset 直改,`hset` 落在已被自然驱逐(DEL)的会话上会重建出**仅剩 `expiry` 的残骸哈希**——LUA_EVICT 对其崩溃循环(单坏键 = sweeper 到期 pass 永久瘫痪,D/5b/K 全链连锁失败);服务侧已加残骸自卫(LUA_EVICT 自清 + WARNING),e2e 侧改自然到期。touch 续期与到期竞态(恰好临界续期)未覆盖。

**阶段 5:K reclaim**(前置:阶段 4 结束,2 Pod 在 idle 池、pod_ttl=120;**加速手法:回拨 `idle_since` 到 pod_ttl 之前**;4 项)

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 23 | `idle_since=now-121` | 20s 内 idle 池清空(reclaim 1s tick:excess 且 aged≥pod_ttl → K8s delete+PURGE+notify) |
| 24–25 | 每个 Pod | K8s 真删(`kubectl` NotFound)+ RM `pods:all` PURGE(物理与编排态双清) |
| 26 | — | notify_pod_dead 已清 `pods:registered`(归零;`--with-sidecar` 时剩 1,`--with-mounts` 时再 +1,长存 Pod 阶段 12 cleanup 统一清) |

> 留白:min_idle 底数保护下的「不回收」正路径未在此验(阶段 8 只验补位);reclaim 与 acquire 复用的 TOCTOU(回收判定后 Pod 被复用)未覆盖(审计网 C 系外,属已知 P1 遗留,见 feature 记录遗留清单)。

**阶段 5b:自然老化全链路(零回拨,5 项)**——前置:e2e-nat 空闲、tpl-nat(session_ttl=15/pod_ttl=20/min_idle=0);**不回拨任何时间,真等 TTL 走完 D→K 全链路**;2026-08-26 缺陷①(idle_since 周期刷新致永不回收)的回归网——若在场,「计时自然累积满 pod_ttl→reclaim」永不成立,当场 FAIL:

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 5b-1 | `route(nat1, e2e-nat)` | 200 首会话 deploy |
| 5b-2 | 真等 session_ttl=15(不回拨) | sweeper 自然到期回收,`scope:sessions` 清空(expiry 由 route 真实写入,数值/单位错误在此可见) |
| 5b-3 | — | 空 Pod pass → 转 idle 暖池,`idle_since` 计时起点存在 |
| 5b-4 | 真等 pod_ttl=20(不回拨) | 计时自然累积满 → reclaim 真删(K8s NotFound + PURGE) |

> 留白:仅单会话单 Pod 形态;多会话错峰到期、touch 长期保活阻止回收的正路径未覆盖。

**阶段 6:I deploy 失败**(前置:e2e-bad 空闲、镜像不可拉(ErrImagePull);2 项)

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 27 | `route(b1, e2e-bad)`(镜像不可拉) | 503 `NO_POD_AVAILABLE`(约 ready_timeout=25s 后;DeployFailed 映射) |
| 28 | — | **红线**:`scope:{BAD}:deploying` ZCARD=0(错误路径清占位,不虚占 max_pods) |

> 留白:create 成功但不 Ready 的失败形态(审计网 C4a)与取消中途形态(C4b)在替身镜像下不可构造(不可拉镜像 create 后立即 Pending,走同一条超时路径但物理清理断言需 FakeK8s);物理孤儿 Pod 的兜底删除未在此断言。

**阶段 7:F 容量满/队列**(前置:e2e-f(cc=2/pc=1)空闲;max_waiters=2×cc=4;5 项)

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 29–30 | `route(f1/f2, e2e-f)` 串行 | 各 200,2 Pod 占满(scope 额度 2/2) |
| 31 | 5 并发 `route(f-over-0..4)` | ≥1 个 503 `SCOPE_QUEUE_FULL`(队列满快失败:LUA_WAITER_GATE 先清过期再 ZADD 先行+超限自退) |
| 32 | 同上 | ≥2 个 504 `SCOPE_FULL_TIMEOUT`(队列内等待至 deadline;信号唤醒或 0.5s 轮询重仲裁均以 route_place 为唯一仲裁) |
| 33 | — | `scope:{FSCOPE}:waiters` ZCARD=0(等待者 finally 出队) |

> 留白:丢唤醒窗口(publish 早于 subscribe)与崩溃遗留 waiter 的 deadline 自清在审计网 C7/C9 覆盖(需确定性时序注入);evict 唤醒等待者后「被唤醒者抢到/抢不到」两种落点未分别断言。

**阶段 8:H min_idle 热备**(前置:e2e-warm(min_idle=1)、阶段 1b 已预热 1 个、w1 未路由;3 项)

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 34 | `route(w1, e2e-warm)` | 200——**复用**阶段 1b 的热备 Pod(acquire 从 idle 弹出,零部署等待) |
| 35 | —(≤30s) | autoscale(1s tick)补位:idle=1(消耗的热备被补齐;版本感知——只数当前版本) |
| 36 | — | 热备 Pod 在 K8s 真实存在 |

> 留白:热备 Pod 的版本换代(A 类变更后旧暖 Pod 回收+新版本补位)在审计网 C2 覆盖(冒烟阶段 10 只验软摘除,不验池换代完成)。

**阶段 9:G/J 死 Pod**(前置:w1 绑在 pod 上、会话活跃;**外部强杀形态**(kubectl delete),区别于内部判死(健康探测);4 项)

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 37 | `kubectl delete pod <w1_pod>`(模拟宕机) | 删除指令成功 |
| 38 | —(≤40s) | watch(10s tick)发现 NotFound → `pods:all` PURGE(get_pod None → 判死 → delete+PURGE+notify) |
| 39 | `touch(w1)` | `touched=false`(notify_pod_dead 已清洗会话) |
| 40 | — | `pods:registered` 无该 (scope,pod) 前缀 |

> 留白:清理窗口内新 route 落上该 Pod 的竞态(refresh 自旋,审计网 C8)、同 request_id 重试拿到死 Pod(审计网 C6)、CrashLoopBackOff/OOMKilled 等中间判死形态(替身不可构造;见 §8.2 场景 N)。

**阶段 10:M(A 类)deploy 字段日落**(前置:阶段 9 结束、e2e-warm 有 1 个老版本 Pod(阶段 35 补位);3 项)

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 41 | config_sync 全量(tpl-warm `readiness_period:7`,A 类) | 200 `ok=true`(deploy_ver 变化→日落路径) |
| 42 | — | RM `scope:config` 的 `deploy_ver` 改变(新 Pod 用新 deploy 字段) |
| 43 | — | SM 候选集 ZREM 软摘除(老 Pod 不接新流量,自然回收) |

> 留白:老 Pod 带**活跃会话**的日落存活(存量会话亲和不受影响——审计网 C3 从探测参数侧覆盖)、软摘除中途失败后同载荷重试的补跑(审计网 C12)、health_path/sse_port 这类影响探测契约的 A 类变更(冒烟沿用同 path/port,变更后探测参数随 Pod 的行为需真镜像门禁)。

**阶段 10b:M-R 强制刷新**(前置:阶段 10 结束;先在 e2e-main route 一个会话保住候选集与亲和载体;8 项)

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 10b-1 | `route(s-refresh, e2e-main)` | 200,pod_id 非空(亲和断言载体) |
| 10b-2 | `POST config_refresh`(无载荷) | 200 `{ok, scopes_refreshed=全 scope 数, generations 覆盖全部 scope 且 ≥1}` |
| 10b-3 | — | 每个 scope 的 RM `scope:config` 出现非空 `generation` 字段 |
| 10b-4 | — | 全部 scope 的 SM `scope:{sid}:pods` ZCARD=0(全量软摘除) |
| 10b-5 | `route(s-refresh)`(同 session) | 200 且 pod_id 不变(存量会话亲和保持,不查候选集) |
| 10b-6 | —(≤120s) | e2e-warm 出现新代暖 Pod:idle 池中其 `pod:info.generation == scope:config.generation`(autoscale 用缓存 pod_spec 重建) |
| 10b-7 | — | `pods:all` 出现刷新前不存在的 pod_id(真实新部署;用集合差而非基数——老代 Pod 被 reclaim 并发回收,计数比较天然竞态) |
| 10b-8 | — | e2e-warm idle 池存在代次落后成员(老代排空态;**不等待 pod_ttl 回收**——全链自然回收由进程内 R1 小 TTL 用例覆盖) |

> 阶段 10(刚做完 A 类变更)与 10b 串行无冲突:A 类老 Pod 同时 stale 于版本与代次,reclaim 双重命中。

**阶段 11:N 半死探测**(1 项 SKIP):AgentServer 镜像对 `GET /health` 返回 426,
暂缓端到端(单测已覆盖:`tests/resource_manager/test_rm_business.py`)。

**阶段 11b:内部不变量巡检(4 项)**——2026-08-26 缺陷②④⑤的回归网(cleanup 清场前执行):

| # | 断言 | 预期(缺陷网) |
|---|---|---|
| IV-1 | `idle ⊆ pods:all` 且 idle 成员必有 `idle_since` | 无幽灵成员(缺陷②:TOCTOU 复活) |
| IV-2 | 各 scope `deploying` ZCARD→0(**有界收敛等待** ≤40s:min_idle≥1 的 scope 随时有 10–12s 在途预热占位,单次快照必误报,2026-08-28 实测;真泄漏永不收敛 → 超时 FAIL) | 无泄漏占位(缺陷⑤:停机取消/崩溃遗留) |
| IV-3 | 快照模板 `deploy_ver()` == RM cfg `deploy_ver` 且 cfg 内 pod_spec 自洽 | SM/RM 两端同指纹(缺陷④:暖复用前提) |

> 留白:巡检只覆盖三类不变量;K8s→Redis 反向(未登记的物理孤儿 Pod)无对账也无断言(已知遗留,见 feature 记录)。

**阶段 12:L 对账 + cleanup**(前置:前面各阶段的终态;4 项)

| # | 输入 | 预期(内部状态变迁) |
|---|---|---|
| 44 | 一致性巡检 | `pods:all` 每个 Pod 在 K8s 均存在(无漂移) |
| 45 | `cleanup(namespace=验收ns)` | 200 `cleaned≥0`;被删的存量 Pod 从 ns 消失(批删含 deploy 失败遗留的孤儿;**不断言 ns 恒零**——min_idle scope 的 autoscale 重建热备与即时采样竞速,「No resources found」与 H0 配置驱动预热自相矛盾;`--with-mounts` 时双 min_idle scope 同 watch tick 一起重建,旧恒零断言必闪红,2026-08-28 改) |
| 46 | —(12s 后) | watch/reconcile 兜底清空 Redis RM 编排态:**cleanup 前的存量 Pod 全部经 NotFound 路径收敛**(重建者=新 pod_id 的 min_idle 暖 Pod,详情列出 rebuilt_by_autoscale;旧「恒零」断言语义同 #45 已改) |
| 47 | — | `{session_manager}:pod:*` 注册态全清 |

**阶段 12b:表达式 or 支(清场后确定性验证,1 项)**:原位置在阶段 2 尾,但彼时 e2e-main 已被
s1–s3 占满(cc=3),or 支 route 只能排队 504——原断言仅在「部署慢、会话先过期」时序下碰巧
200(2026-08-27 快跑实测暴露,镜像预分发后快跑必现);移到阶段 12 清场后(Pod/会话全空、配置
仍在)e2e-main 空闲,确定性 200:

| # | 场景 | 输入 | 预期输出/断言 |
|---|---|---|---|
| 12b-1 | C 表达式 or 支 | `route(s-vip, e2e-no-such-group, user=e2e-vip)` | 200 且 `pod_id` 以 `agentserver-` 开头——group 不命中但 user 白名单 or 支命中 e2e-main |

**阶段 13:错误契约**(前置:配置在、会话/Pod 已清场;6 项)

| # | 输入 | 预期 |
|---|---|---|
| 48 | `route(无匹配 scope 的 group)` | 503 `CONFIG_NOT_FOUND`,**无** `retry_after`(语义不可重试) |
| 49 | `route(session_id=null)` / `route(user_id=null)` | 400 `VALIDATION`(四参非空) |
| 50 | `touch(session_id=null)` | 400 `VALIDATION` |
| 51 | `config_sync(kind="nope")`(旧 kind/op 协议) | 400 `VALIDATION` |
| 51b | `config_refresh(rawdata={"templates":[]})`(无载荷契约) | 400 `VALIDATION` |
| 52 | `cleanup(验收ns, label_selector=无匹配)` | 200 `cleaned=0`(空目标须用无匹配 selector,见 §8.1) |

> 留白:config_sync 载荷深层的畸形矩阵(int 字段非数值/0 值策略字段/scope 引用缺失模板)在审计网 C10a/C10b 覆盖(冒烟只验协议层 kind/op 遗迹);CONFIG_SYNC_BUSY 409 的日落中间态语义在审计网 C1 覆盖。

> 断言逐条 `check()` 记名,全规格形态(`--with-sidecar --with-mounts`,真 agentserver/sandbox 镜像)实测 **121 项,2026-09-01 门禁 121/121**(个别为条件性/可选 SKIP,计入通过)。
> **注意**:M6 冒烟回归请对**单实例**执行——多副本后端冷突发语义不同(见 §6)。

---

## 4. 多副本 e2e(M7):`scripts/e2e_multi_replica.py`(35 项)

### 4.0 形态与前置

- **真 LB 单入口**:K8s Deployment 多副本 + ClusterIP/NodePort Service
  (默认 `http://127.0.0.1:30091/api/session`);脚本不打多地址。
- 实例身份从 Redis 选主键反查:`{agent_runtime:job:{job}}:candidates:{epoch}`(SET,成员=
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
| 6 | —(≤45s 轮询) | waiters 清空(ZCARD=0;残留时打印成员便于定位) |
| 7 | — | `deploying` ZCARD=0(占位清空) |

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

## 5. 进程内集成测试(pytest 离线层)

### 5.1 双实例:`tests/integration/test_multi_replica.py`(14 用例)

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

### 5.2 审计实锤回归网:`tests/integration/test_audit_repro.py`(16 用例)

2026-08-27 全量审计(五维:状态层原子性/停机并发/外部契约/测试缺口/业务语义)产出
~40 条假设,先以本套用例**实锤**再修(16/16 全部成立;修复后全绿转回归网)。
缺陷编号 C1–C13 对应 `docs/feature/2026-08-27-audit-e2e-repro-fixes.md`。

**方法论(与全局硬标准一致)**:全部走真实业务流(config_sync/route/touch/真实
sweep/autoscale/reclaim/watch tick),小 TTL 自然到期,**零指针回拨、零直改 Redis 键**;
竞态窗口仅用调度/故障注入控制时序(包装注入点、按 facade 真实顺序驱动状态原语、
K8s 替身可编程状态),被测业务逻辑零改动。K8s 替身 `_VersionedProbeK8s` 补齐了
FakeK8s 忽略探测参数的保真度缺口(按 (ip,port,path) 判定——2026-08-26 缺陷③的
测试盲区根源)。

| # | 用例(缺陷) | 前置状态 | 输入/时序 | 预期(正确行为) |
|---|---|---|---|---|
| C1a | 静息池二次下发(C1) | Pod 曾服务会话→自然到期→真实 tick 转 idle(min_idle=0,pod_ttl=60 长窗;处于 registered∖candidates 合法中间态) | 纯 B 类变更(session_ttl 60→2) | config_sync 200——正常空闲 Pod 不是日落残留,不得 409 |
| C1b | 底数保护下 409 不永久(C1) | min_idle=1/pod_ttl=2;暖 Pod 服务过一会话后回 idle;自然老化超 pod_ttl + 真实 reclaim tick(底数保护使其留存) | 纯 B 类变更 | config_sync 200——版本相同的空闲 Pod 永远不该锁死配置面 |
| C2 | A 类变更后暖池换代(C2) | min_idle=1/max_pods=1;v1 暖 Pod 在池 | A 类变更(agent_image 2.0)+ 4×(autoscale+reclaim) tick + 自然老化>pod_ttl + route 新会话 | 旧版暖 Pod 被回收、新版本部署(deployed_specs≥2)、route 成功——不可复用的旧版不得钉死暖池/蹲占 max_pods |
| C3 | 探测参数随 Pod(C3) | 会话活跃于老 Pod(烘焙 /health);替身按 (ip,port,path) 判定 | A 类变更 health_path→/api/v1/health + 2×watch tick(连续 2 败阈值) | 老 Pod 存活 + 会话 touch=true——探测用 Pod 自己烘焙的契约,不得拿新参数杀存量 |
| C4a | deploy 失败不留物理孤儿(C4) | FakeK8s `fail_after_create=1`(create 成功永不 Ready,DeployFailed 携带 pod_id) | route 单会话 | NO_POD_AVAILABLE 且集群零残留 Pod(服务层兜底删除) |
| C4b | deploy 取消不留物理孤儿(C4) | 替身在 create 后卡在等 Ready 窗口 | 0.3s 后 cancel(优雅停机语义) | Redis 占位清(⑤)+ 物理 Pod 清(deploy 层清理契约) |
| C5 | REGISTER 步失败不占容量(C5) | max_pods=2/pod_concurrency=1;注入 REGISTER 步抛错一次 | 三连 route(第 1 个触发注错) | 第 3 个会话仍能扩到 2 个真实 Pod——注册步失败不得永久虚占 max_pods |
| C6 | 死 Pod 不回放(C6) | 会话 Pod 经 watch 判死 PURGE(注册全清) | 同 `request_id` 重试 route(网关幂等重试语义) | 拿到**新** Pod——idem 命中须校验存活,不得复活死 Pod 喂死地址 |
| C7 | 丢唤醒后轮询重仲裁(C7) | scope 满(cc=1),sess_2 入队等待 | 注入:释放信号恰在 subscribe 完成前发布(天然并发窗口) | ≤0.5s 轮询重仲裁拿到空闲额度,而非空等满 2s 后 504 |
| C8 | 亲和 Pod 被清后换绑(C8) | 按 notify_pod_dead 真实顺序驱动窗口:evict 已枚举会话→窗口内新会话落上该 Pod→cleanup 收口(info 已清) | 再 route 该会话(线程+硬 join 有界 5s) | 2s 内换绑新 Pod——refresh 须有存活守卫,不得无限自旋(每圈续期 expiry 的绝症) |
| C9 | 幽灵 waiter 自清(C9) | scope 满;经真实闸门塞 2 个 **deadline 已过期**的幽灵 waiter(模拟崩溃副本遗留) | 新请求 route | 能入队、等满 0.5s 得 504——而非被永久 503 SCOPE_QUEUE_FULL |
| C10a | 畸形 int 400(C10) | 配置在 | config_sync `session_ttl="abc"` | 400 VALIDATION——int 畸形不得裸抛 ValueError 成 500 |
| C10b | 0 值策略字段拒绝(C10) | 配置在 | config_sync `pod_concurrency=0` | 400 VALIDATION——0 值是拒绝服务配置(满 max_pods 个必用不上的 Pod 后永久 scope_full) |
| C11 | 幻影 scope 收敛(C11) | 双 scope 下发;注入 drain 推送(min_idle=0 无 spec)失败一次(模拟滚动重启中断扩散③) | 删 scope 后 2×(sync+autoscale) | 幻影 scope 零 Pod——drain 目标集以 RM 已知 scope 为真源,重试必补推 |
| C12 | 重试补跑软摘除(C12) | 会话在老版本 Pod 上;注入软摘除步失败一次(写 DB 后) | 同载荷重试 config_sync + route 新会话 | 新会话不落旧版本 Pod——候选集版本收敛是声明式每拍重算,不由 diff 驱动 |
| C13 | 路径归一(C13) | — | config_sync `sse_path="api/v1/stream"`(缺前导 /) | URL `port=8080` 可解析且 path=/api/v1/stream——缺 / 会拼出 `http://ip:8080api/...` 非法端口 → 健康 Pod 被探死循环 |

> **本层留白(需真环境/真镜像)**:C3/C4/C13 的替身保真度依赖可编程 K8s 双打——
> 真 K8s 的删除异步性、CrashLoopBackOff 判死、真 AgentServer 的 health 契约仍只在
> 门禁层可见;C9 的硬崩形态(kill -9 遗留 waiter)在本层用过期 deadline 模拟,
> 真进程硬崩的端到端未覆盖;reclaim 与 acquire 的 TOCTOU(在用 Pod 被回收)两轮
> 审计均确认存在但确定性复现需真时序,列为已知 P1 遗留(feature 记录遗留清单)。

### 5.3 强制刷新自然老化网:`tests/integration/test_force_refresh.py`(4 用例)

方法论同审计网(真实业务流 + 小 TTL 自然到期,禁回拨/直改键),覆盖 config_refresh(场景 M-R)的全链日落闭环:

| # | 场景 | 前置状态 | 输入/时序 | 预期 |
|---|---|---|---|---|
| R1 | 全量自然周期 | min_idle=1/session_ttl=1/pod_ttl=2,route 建会话 | config_refresh → 亲和再验 → 自然到期 → 真等过 pod_ttl → reclaim/autoscale | 候选集清空+代次=1;同 session 回同 Pod;老 Pod 被 reclaim(出 pods:all、K8s 删、SM 清注册);新暖 Pod `generation == cfg.generation` |
| R2 | 重复刷新收敛 | min_idle=1/pod_ttl=1 | 刷新→补位→回收→再刷新→再补位(交错,max_pods=2 内) | 代次 1→2 递增;终态仅最新代 warm Pod 存活 |
| R3 | 刷新后下发守卫 | 会话在老代 Pod 上,刷新后自然转 idle | B 类下发 → A 类下发(409)→ 回收后 A 类下发 | B 类放行;A 类按日落中间态 409(守卫按版本、不看代次);老代回收后 A 类 200 |
| R4 | 重建用存量 spec | min_idle=1,autoscale 暖 Pod | config_refresh → autoscale | 重建部署的 pod_spec 与 RM 缓存逐字段一致(配置零变化,仅换代) |

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
./scripts/integration_smoke.sh                              # 75 项(基础);
./scripts/integration_smoke.sh --with-sidecar                # +5 项 sidecar 阶段

# ③ 宿主机双进程(观察选主互斥)
./scripts/deploy_replicas.sh 2 .env.production.local 8091
redis-cli -p 30001 -n 1 --scan --pattern '{agent_runtime:job:*}:winner:*'

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

### 8.3 已知覆盖缺口汇总(人工审阅入口;各阶段「留白」的聚合)

按「补上的代价」分三档,审阅时从这找遗漏:

**A. 需真环境/真镜像才能构造(替身世界不可见)**
- K8s 判死中间形态:CrashLoopBackOff / OOMKilled / Terminating 的 watch 端到端
  (替身只能 Running/Failed 两态;阶段 9 只覆盖外部强杀 NotFound 形态)。
- 探测契约×Pod 版本的真矩阵:health_path/sse_port A 类变更后老 Pod 存活
  (§5.2 C3 在替身层已覆盖逻辑,真 AgentServer 契约须真镜像门禁)。
- deploy 失败/取消的物理孤儿在真 K8s 的清理(§5.2 C4 为替身双打;真 K8s 的
  删除异步性、节点驱逐形态未覆盖)。
- 进程硬崩(kill -9)遗留的端到端自愈:waiter/占位 deadline 在本层以过期
  deadline 模拟,真进程硬崩 + 重启收敛未覆盖。
- 依赖故障注入:Redis 运行中闪断、DB 不可达时的 route/config_sync/启动降级、
  K8s API 5xx/超时、create 409 名字冲突——全部零覆盖。
- 真实配置形状的**镜像侧行为**(2026-08-28 阶段 0m/2c 已把配置渲染与挂载链路
  自动化,替身即可断言;仍留):真 jiuwenbox 镜像内无 shell 时 exec 类断言的
  可达性、readiness `http` 探针类型真环境、多 sidecar(>1)形态。
- sidecar 细粒度规格:资源限额(cpu/memory request/limit)、`run_as_user/group`、
  `capabilities_drop`——schema/渲染已支持,e2e 零覆盖。
- 主容器 pod 落位字段(026450be 引入):`run_as_user/group`(容器内 `id -u` 生效实证)、
  `node_name`(Pod 实际落指定节点)——渲染层已有单测(`test_k8s_pod_body.py`),真环境
  生效零覆盖;`PVC 同 claim 跨容器去重`(主/sidecar 共享一卷)同此。
- `nfs_server/nfs_path/nfs_mount_path` 挂载:e2e 零覆盖(需 NFS 环境决策)。

**B. 已确认存在、复现需确定性时序或产品决策(P1/P2 遗留)**
- reclaim 与 acquire 复用的 TOCTOU(在用 Pod 被物理删):两轮审计确认,
  LUA_RELEASE/PURGE 无「仍在 idle」守卫;确定性复现需真时序。
- route 全程不重 resolve:config_sync 与长 route 并发时按旧 scope/模板仲裁、
  对已删 scope 白等满超时。
- B 类调小(session/cc)低于现活跃数的回收语义:存量超限不驱逐只老化回落,
  行为未固化无断言。
- 停机预算:uvicorn 无 `timeout_graceful_shutdown` + 30s 宽限 vs 300s 在飞请求
  ——优雅停机可能整体跳过(占位/等待已 deadline 自愈,锁内中断仍依赖 finally)。

**C. 加速手法残留(与「零回拨」硬标准的差距)**
- 冒烟阶段 4(D 老化)与阶段 5(K reclaim 主体)仍回拨 expiry/idle_since——
  阶段 5b 已补自然对照(仅 tpl-nat 单会话形态);多会话错峰自然到期未覆盖。
- pytest 层(双实例/审计网之外的老用例)仍有十余处回拨直改——方法论约束
  目前 scoped 到 e2e 层,pytest 层待逐步改造。
