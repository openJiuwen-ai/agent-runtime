# agent-runtime 配置面接口文档

> 覆盖 runtime 服务配置平面三个能力:**配置下发**(`config_sync`)、**配置刷新**(`config_refresh`)、**可视化**(`/visualization/*` 只读观测端点)。
> 语义权威为 `docs/design/Agent-Runtime-HLD.md` §3.1(冲突以它为准);本文面向调用方(Claw Manager / 运维 / 可视化前端),给出功能说明、入参出参字段表、curl 与真实返回示例。
> 示例来源:curl 载荷取自 HLD §3.1 完整示例(develop1 分支);返回示例为 local 模式真实拉起服务抓取(镜像名/命名空间等环境值以实际部署为准)。

## 0. 通用约定

### 0.1 基地址与端口

| 项 | 值 |
|---|---|
| 服务前缀 | `/api/session`(业务端点)/ `/visualization`(可视化端点,无前缀) |
| 默认端口 | 8091(`service_port`,部署形态经 Service/NodePort 暴露时以入口地址为准) |
| 认证 | 无内置鉴权,靠网络边界(Service ClusterIP 仅集群内可达);可视化输出对 secrets 脱敏 |
| Content-Type | `application/json`(业务端点 POST) |

### 0.2 业务端点请求信封(Envelope)

`config_sync` / `config_refresh` 走框架统一信封,HTTP body 即下述 JSON:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | str | 是 | 端点名,须与 URL 路径段一致(如 `config_sync`) |
| `metadata` | object | 是 | 请求元数据 |
| `metadata.request_id` | str | 是 | 幂等键 + 链路追踪键(60s 窗口内同 id 重放回缓存结果) |
| `metadata.user_id` | str? | 否 | 操作者标识(下发方通常填 `ops`) |
| `metadata.bot_id` | str? | 否 | 机器人标识 |
| `metadata.session_id` | str? | 否 | 会话标识(配置面端点不使用,通常 `null`) |
| `metadata.extra.group_id` | str? | 否 | 分组标识 |
| `metadata.timestamp` / `trace_id` / `instance_id` / `channel` / `chat_id` | — | 否 | 透传字段,配置面不消费 |
| `rawdata` | object | 视端点 | 业务载荷:`config_sync` 为三段式配置;`config_refresh` **必须为空对象** |
| `version` | str | 否 | 信封版本,固定 `"1"` |

### 0.3 业务端点响应信封与错误码

成功响应 HTTP 2xx,业务数据在 `rawdata` 内:

```json
{
  "type": "config_sync",
  "metadata": { "request_id": "...", "...": "请求元数据回显" },
  "rawdata": { "ok": true, "…": "业务字段" },
  "ok": true,
  "error_code": null,
  "error_message": null,
  "version": "1"
}
```

失败响应(所有非 2xx)统一错误信封:

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | bool | 固定 `false` |
| `error_code` | str | 见下表 |
| `error_message` | str | 人类可读描述(含失败原因明细) |
| `retry_after` | int?(秒) | 仅过载/暂态类错误携带(`STATE_UNAVAILABLE`、`SCOPE_QUEUE_FULL`、`SCOPE_FULL_TIMEOUT`、`NO_POD_AVAILABLE`),其余省略 |

配置面相关错误码:

| error_code | HTTP | 可重试 | 配置面含义 |
|---|---|---|---|
| `VALIDATION` | 400 | 否(改载荷) | 载荷校验失败(锁外零副作用,改后重发即可) |
| `CONFIG_SYNC_BUSY` | 409 | 稍后重试 | 上一次 config_sync/config_refresh 仍在执行(串行化锁被占),或受影响 scope 存在日落中间态待回收 |
| `STATE_UNAVAILABLE` | 503 | 是(`retry_after=1`) | Redis/DB 连接级故障(LB/客户端应重试而非放弃) |

### 0.4 可视化端点通用约定

- 全部 **GET、只读**,不写任何 Redis/DB/K8s 状态;成功响应统一包裹 `{ok: true, instance_id, generated_at, …端点载荷}`。
- 错误面:`400 {ok:false, detail}`(缺参/参数非法)、`404 {ok:false, detail}`(对象不存在)、`503 {ok:false, detail}`(sysctx 未就绪或内部异常,绝不裸 500)。
- **多副本视角差异**:经 LB 命中哪个副本就是哪个副本的数据(响应带 `instance_id` 标识应答者;要看指定副本请直连 Pod IP)。`stats.endpoints`、`recent_errors` 为**命中实例视角**;`stats.scopes`、`history`、`evaluation` 读 Redis,**全局视角**(全副本聚合)。
- 输出统一 `redact()` 脱敏:敏感 key(`password`/`secret`/`token`/`kubeconfig`/`credential`/`api_key`)→ `"***"`,URL 剥 userinfo,嵌套 JSON 字符串(如 `pod_spec_json`)深入内部脱敏。

---

## 1. 配置下发 `POST /api/session/config_sync`

### 1.1 功能说明

Claw Manager **全量配置下发**(场景 M):一次请求同时携带 `containers` / `templates` / `scopes` 三个列表,**快照式全量替换**——upsert 本批全部条目 + 删除 DB 中已消失的条目(容器以本批为集 GC)。旧 `kind/op` 增量协议与无 `containers` 键的 legacy 内联载荷均 400 拒绝(三段式契约独占)。

处理编排(全程持 `lock:config_sync` 串行化,忙 → 409):锁外校验(确定性 400、零副作用)→ 日落中间态检查 → **单事务写 DB**(全有或全无)→ 重建路由快照(原子 SET,`route` 匹配立即用新配置)→ 逐 scope 推 RM 池参数 + pod_spec(**eager 预热**:autoscale 下一拍即预热 `min_idle_pods`,从未被请求过的 scope 也生效)→ A 类软摘除老版本 Pod(不再接新会话,存量会话不受影响)→ 被删/禁用 scope 推 `min_idle=0` 自然排空。

变更分类(服务端逐字段 diff 自动判定,无需调用方声明):

| 类别 | 触发 | 生效方式 |
|---|---|---|
| **A 类**(deploy 字段变更) | 镜像/端口/env/挂载/sidecar 等容器规格变化(`deploy_ver` 指纹不等) | 老 Pod 软摘除退出候选集,按新规格重建;存量会话自然跑完 |
| **B 类**(策略字段变更) | `scope_concurrency`/`pod_concurrency`/`session_ttl`/`pod_ttl`/`min_idle_pods`/`message_timeout` | 快照覆盖 + RM 池参数重推,**立即生效**,不动存量 Pod |
| **删除** | 本批载荷未携带的模板/scope/容器 | DB 删行;被删 scope 停预热自然排空,存量会话到期止 |

幂等性:同载荷重放收敛(`affected_scopes` 为空数组)。

### 1.2 入参

Envelope 外层见 §0.2;`rawdata` 为三段式配置快照:

#### `rawdata` 顶层

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `containers` | list[object] | 是 | 容器规格列表(主容器与 sidecar 同一 schema,角色由模板引用位置决定);**缺失此键 = legacy 载荷 → 400** |
| `templates` | list[object] | 是 | 模板列表(只持容器引用与 Pod 级字段) |
| `scopes` | list[object] | 是 | 路由 scope 列表 |

#### `containers[]` 字段(wire 对齐 K8s 原生 container 规范,camelCase)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `container_id` | str | 是 | 业务键(≤100 字符,同批唯一);**未被任何模板引用 → 400**;同 id 双角色(既是主容器又是 sidecar)→ 400 |
| `name` | str | 主容器可缺省 | 容器名(DNS-1123,≤63);主容器缺省 `agent`;sidecar 必填且不得撞主容器名/兄弟 sidecar |
| `image` | str | 是 | 镜像(非空,≤512) |
| `imagePullPolicy` | str | 否 | 默认 `IfNotPresent` |
| `ports` | list | 否 | 主容器**必须有且仅有一个 `name="sse"` 端口**(gateway 直连契约,缺省 `[{"name":"sse","containerPort":8080}]`),可另有一个 `name="http"`;sidecar 至多 1 个**无名**端口(条目形如 `{"containerPort": 8321}`,不写 `name`) |
| `ports[].name` | str? | — | `"sse"` / `"http"`(主容器)或 `null`(sidecar) |
| `ports[].containerPort` | int | 是 | 1–65535 |
| `env` | list[{name, value}] | 否 | K8s 列表形态;name 重复或 value 非 str → 400 |
| `envFrom` | list | 否 | envFrom 引用注入:每项 `{prefix?, secretRef:{name, optional?}}` 或 `{prefix?, configMapRef:{name, optional?}}`(恰一 ref)。**密钥以引用名下发,值不落模板/快照/pod_spec** |
| `resources` | object | 否 | `{requests?: {cpu?, memory?}, limits?: {cpu?, memory?}}`,量纲字符串(如 `"500m"`/`"1Gi"`) |
| `volumeMounts` | list | 否 | `[{name, mountPath, subPath?, readOnly?}]`,按名引用模板 `volumes`;悬挂引用(卷未定义)→ 400;`subPath` 仅 configMap 卷;`readOnly` 缺省按源类型(configMap→true、hostPath/PVC→false) |
| `securityContext` | object | 否 | **主容器只许 `runAsUser`/`runAsGroup`**(int ≥0,`null`=走镜像默认;注意不改变卷文件属主);sidecar 另有 `privileged`(bool)、`capabilities:{add:[], drop:[]}`、`seccompProfile:{type}`、`appArmorProfile:{type}`(type ∈ {`Unconfined`, `RuntimeDefault`}) |
| `readinessProbe` | object | 否 | **主容器恒 `httpGet{path, port}`** + `initialDelaySeconds`/`periodSeconds`(缺省 5/5);`tcpSocket`/`timeoutSeconds` → 400;probe port 若给必须等于 sse 端口。sidecar 为 `tcpSocket{port}`/`httpGet{path, port}` 二选一(可整体缺省,默认 5/10/3),`timeoutSeconds` 1..300 |

> 未知键、越角色键、内部表达不了的 K8s 字段(`command`/`args`/端口 `protocol` 等)→ **400,绝不静默丢弃**(防"看似配置了实际没生效")。

#### `templates[]` 字段(模板只持容器引用与 Pod 级/策略字段)

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `template_id` | str | 是 | — | 模板业务键 |
| `template_name` / `description` | str | 否 | `""` | 元信息 |
| `main_container_id` | str | 是 | — | 主容器引用,必须在本批 `containers` 内 |
| `sidecar_container_ids` | list[str] | 否 | 无 | sidecar 引用列表(≤8,本批内,不得与主容器同 id、不得重复) |
| `volumes` | list[object] | 否 | 无 | **Pod 级卷定义**(K8s `spec.volumes` 同构):`[{name(DNS-1123,模板内唯一), 恰一源}]`;源 = `hostPath{path, type?}` / `configMap{name, items?}` / `persistentVolumeClaim{claimName}` / `nfs{server, path?}`(NFS 仅主容器、至多一个挂载)。**未被任何容器挂载的卷 → 400** |
| `namespace` | str | 否 | `"default"` | K8s 命名空间(Pod 级) |
| `nodeName` | str? | 否 | `null` | 节点绑定(A 类,渲染 `V1PodSpec.nodeName` 绕过调度器点名上机);空串同 `null`;按 hostname 形态 ≤253 校验 |
| `pod_name` | str | 否 | `"agentserver"` | Pod 名前缀(pod_id = 前缀-随机后缀) |
| `sse_path` | str | 否 | `"/sse"` | SSE 路径(网关契约;真 AgentServer 为 `/api/v1/events/stream`) |
| `kubeconfig` | str? | 否 | `null` | K8s 认证(deploy 凭证,**不进 deploy_ver 指纹**,改它不触发日落) |
| `ready_timeout` | int(秒) | 否 | 300 | deploy 等 Ready 超时(下界 1) |
| `ready_poll_interval` | int(秒) | 否 | 2 | deploy 轮询间隔 |
| `scope_concurrency` | int | 否 | 3 | 该 scope 最大活跃会话数(scope 闸门,下界 1) |
| `pod_concurrency` | int | 否 | 2 | 单 Pod 最大并发(下界 1);`max_pods = ⌈scope_concurrency/pod_concurrency⌉` 为派生值 |
| `session_ttl` | int(秒) | 否 | 60 | 会话保活超时(下界 1) |
| `pod_ttl` | int(秒) | 否 | 300 | idle Pod 至 reclaim 的等待(下界 1) |
| `min_idle_pods` | int | 否 | 0 | 该 scope 最少热备 Pod 数(≥0) |
| `message_timeout` | int(秒) | 否 | 600 | 数据面 SSE 读写超时语义(gateway 侧使用) |
| `enabled` | bool | 否 | true | 模板禁用则路由不解析、不预热 |
| `data` | object | 否 | `{}` | 透传扩展字段 |

> 模板级 K8s 派生字段用 K8s 拼写(`nodeName`),snake 双形态并存 → 400(防静默二义)。

#### `scopes[]` 字段

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `scope_id` | str | 是 | — | 资源域标识,字符集 `[0-9A-Za-z._-]` ≤128(禁 `:` `*` 空白与非 ASCII——内嵌 Redis 键名) |
| `index` | int | 是 | — | 匹配序号;请求按 `(index 升序, scope_id 升序)` **first-fit** 首个命中即止(bool 拒绝) |
| `template_id` | str | 是 | — | 引用模板,**必须在本批模板列表内**;多个 scope 可引用同一模板 |
| `routing_rules` | str | 否 | `""` | 布尔表达式字符串;**null/空串/纯空白 = 通配兜底 scope**(命中一切);语法见下 |
| `enabled` | bool | 否 | true | 禁用则不参与路由匹配与 eager 预热(仍落库/进快照) |
| `expires_at` | str? | 否 | `null` | 可选过期时间(ISO-8601);到点后视为不生效;`null` = 永不过期 |

`routing_rules` 表达式语法:

```
group_id not in ('g1', 'g2') and (user_id in ('admin', 'user1') or bot_id in ('b1'))

条件     := field ('not'? 'in') '(' [值 {',' 值} [',']] ')'
表达式   := 条件经 and/or 与括号组合;优先级 条件 > and > or(同 SQL/Python)
field    := user_id | group_id | bot_id(固定小写枚举)
值        := 单引号串('' 加倍或 \' 转义引号,\\ 转义反斜杠;空列表 ():in 恒假、not_in 恒真)
关键字   := and / or / in / not(大小写不敏感);不支持一元 not
上限     := 长度 ≤ 8000,括号嵌套 ≤ 32
```

**下发方应保证含一个空表达式的通配 scope 兜底**;服务端缺失时仅 WARNING 放行,运行时无匹配 → 503 `CONFIG_NOT_FOUND`。

### 1.3 返回值(`rawdata`)

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | bool | 固定 `true` |
| `templates_synced` | int | 本批 upsert 的模板数 |
| `templates_deleted` | int | DB 中被删除的模板数(本批未携带的) |
| `containers_synced` | int | 本批 upsert 的容器数 |
| `containers_deleted` | int | 被容器 GC 删除的容器数(不在本批引用集内的) |
| `scopes_synced` | int | 本批 upsert 的 scope 数 |
| `scopes_deleted` | int | 被删除的 scope 数 |
| `affected_scopes` | list[str] | 受影响 scope(A 类模板变更 ∪ 引用切换);幂等重放收敛为 `[]` |
| `wildcard_present` | bool | 载荷是否含生效中的通配兜底 scope |

### 1.4 curl 示例

> 完整示例(主容器 httpGet 探针 + envFrom 引用注入 + hostPath/configMap/PVC 卷;sidecar jiuwenbox 特权三件套 + tcpSocket 探针;单模板双容器;空 `routing_rules` 通配兜底 scope)。K8s Pod 内探测的脚本版本见 `scripts/config_sync_seed.sh`;同一份三段式 JSON(root 即 `rawdata` 内容)也可直接经 Manager Web「服务配置模板 → 导入」落库。

```bash
curl -s -X POST "http://127.0.0.1:8091/api/session/config_sync" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{
  "type": "config_sync",
  "metadata": {
    "request_id": "cfg-20260903-001",
    "user_id": "ops",
    "bot_id": "ops",
    "extra": {
      "group_id": "ops"
    }
  },
  "version": "1",
  "rawdata": {
    "containers": [
      {
        "container_id": "c-agentserver",
        "name": "jiuwenclaw-agentserver",
        "image": "swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-amd64:0.0.10s",
        "imagePullPolicy": "IfNotPresent",
        "ports": [
          {
            "name": "sse",
            "containerPort": 8766
          }
        ],
        "envFrom": [
          {
            "secretRef": {
              "name": "jiuwenclaw-secret-configmap"
            }
          },
          {
            "configMapRef": {
              "name": "jiuwenclaw-agentserver-env"
            }
          }
        ],
        "securityContext": {
          "runAsUser": 0,
          "runAsGroup": 0
        },
        "readinessProbe": {
          "httpGet": {
            "path": "/api/v1/health",
            "port": 8766
          },
          "initialDelaySeconds": 5,
          "periodSeconds": 10
        },
        "volumeMounts": [
          {
            "name": "hp-code",
            "mountPath": "/app/jiuwenswarm"
          },
          {
            "name": "hp-rt-foundation",
            "mountPath": "/usr/local/lib/python3.11/site-packages/openjiuwen_runtime/foundation"
          },
          {
            "name": "hp-rt-management",
            "mountPath": "/usr/local/lib/python3.11/site-packages/openjiuwen_runtime/management"
          },
          {
            "name": "hp-openjiuwen",
            "mountPath": "/usr/local/lib/python3.11/site-packages/openjiuwen"
          },
          {
            "name": "gw-config",
            "mountPath": "/root/.jiuwenswarm/config/config.yaml",
            "subPath": "config.yaml"
          },
          {
            "name": "gw-envfile",
            "mountPath": "/root/.jiuwenswarm/config/.env",
            "subPath": ".env"
          },
          {
            "name": "data",
            "mountPath": "/root/.jiuwenswarm"
          }
        ]
      },
      {
        "container_id": "c-jiuwenbox",
        "name": "jiuwenbox",
        "image": "swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-sandbox-amd64:0.0.10s",
        "imagePullPolicy": "IfNotPresent",
        "ports": [
          {
            "containerPort": 8321
          }
        ],
        "env": [
          {
            "name": "JIUWENBOX_LISTEN",
            "value": "http://0.0.0.0:8321"
          },
          {
            "name": "JIUWENBOX_POLICY_PATH",
            "value": "/usr/local/lib/python3.11/site-packages/jiuwenbox/configs/enterprise-policy.yaml"
          },
          {
            "name": "TZ",
            "value": "Asia/Shanghai"
          }
        ],
        "securityContext": {
          "privileged": true,
          "capabilities": {
            "add": [
              "SYS_ADMIN",
              "NET_ADMIN"
            ]
          },
          "seccompProfile": {
            "type": "Unconfined"
          },
          "appArmorProfile": {
            "type": "Unconfined"
          }
        },
        "readinessProbe": {
          "tcpSocket": {
            "port": 8321
          },
          "initialDelaySeconds": 10,
          "periodSeconds": 5
        },
        "volumeMounts": [
          {
            "name": "hp-cgroup",
            "mountPath": "/sys/fs/cgroup"
          },
          {
            "name": "hp-jiuwenbox",
            "mountPath": "/usr/local/lib/python3.11/site-packages/jiuwenbox"
          },
          {
            "name": "data",
            "mountPath": "/home/app/.jiuwenswarm"
          }
        ]
      }
    ],
    "templates": [
      {
        "template_id": "default",
        "template_name": "default",
        "main_container_id": "c-agentserver",
        "sidecar_container_ids": [
          "c-jiuwenbox"
        ],
        "pod_name": "jiuwenclaw-agentserver",
        "namespace": "wmq",
        "nodeName": "ecs-38b3-0002",
        "sse_path": "/api/v1/events/stream",
        "scope_concurrency": 3,
        "pod_concurrency": 2,
        "session_ttl": 60,
        "pod_ttl": 3600,
        "min_idle_pods": 1,
        "ready_timeout": 240,
        "volumes": [
          {
            "name": "hp-code",
            "hostPath": {
              "path": "/root/wangxin/jiuwenswarm",
              "type": "Directory"
            }
          },
          {
            "name": "hp-rt-foundation",
            "hostPath": {
              "path": "/root/wangxin/agent-runtime/foundation/openjiuwen_runtime/foundation",
              "type": "Directory"
            }
          },
          {
            "name": "hp-rt-management",
            "hostPath": {
              "path": "/root/wangxin/agent-runtime/management/openjiuwen_runtime/management",
              "type": "Directory"
            }
          },
          {
            "name": "hp-openjiuwen",
            "hostPath": {
              "path": "/root/wangxin/agent-core/openjiuwen",
              "type": "Directory"
            }
          },
          {
            "name": "gw-config",
            "configMap": {
              "name": "jiuwenclaw-gateway-config"
            }
          },
          {
            "name": "gw-envfile",
            "configMap": {
              "name": "jiuwenclaw-gateway-envfile"
            }
          },
          {
            "name": "data",
            "persistentVolumeClaim": {
              "claimName": "jiuwenclaw-pvc"
            }
          },
          {
            "name": "hp-cgroup",
            "hostPath": {
              "path": "/sys/fs/cgroup",
              "type": "Directory"
            }
          },
          {
            "name": "hp-jiuwenbox",
            "hostPath": {
              "path": "/root/wangxin/jiuwenswarm/jiuwenbox/src/jiuwenbox",
              "type": "Directory"
            }
          }
        ]
      }
    ],
    "scopes": [
      {
        "scope_id": "default",
        "index": 100,
        "template_id": "default",
        "routing_rules": ""
      }
    ]
  }
}
JSON
```

### 1.5 返回值示例

成功(信封已折叠为 `rawdata` 内容,外层见 §0.3):

```json
{
  "ok": true,
  "templates_synced": 2,
  "templates_deleted": 0,
  "containers_synced": 3,
  "containers_deleted": 0,
  "scopes_synced": 2,
  "scopes_deleted": 0,
  "affected_scopes": [
    "scope-default",
    "scope-vip"
  ],
  "wildcard_present": true
}
```

失败(400,legacy 内联载荷被三段式契约拒绝;DB/Redis 零副作用):

```json
{
  "type": "config_sync",
  "metadata": { "request_id": "req-f1005268", "...": "..." },
  "rawdata": {},
  "ok": false,
  "error_code": "VALIDATION",
  "error_message": "config_sync requires the three-part contract {containers, templates, scopes}; legacy inline templates are no longer accepted",
  "version": "1"
}
```

---

## 2. 配置刷新 `POST /api/session/config_refresh`

### 2.1 功能说明

强制刷新(场景 M-R,**无载荷**端点):不改任何配置值、不写 DB、不动路由快照——把**全部存活 scope** 的现有 Pod 优雅日落并**按存量配置重建**。适用:配置漂移自愈、镜像同 tag 更新后强制重拉、运行时异常兜底重建。

每 scope 三步(顺序红线:bump 先于摘除,任何中途失败形态都收敛于"老 Pod 暂时继续接新流量",重试即收敛):

1. **代次 +1**(`generation` HINCRBY 唯一写点)——老代 Pod 即刻退出候选集,**不接新会话**;存量会话亲和不受影响,自然跑完;
2. 重推池参数 + pod_spec(值未变,确保 RM 缓存/预热就绪);
3. 候选集全量软摘除——reclaim 按 `pod_ttl` 回收老代空 Pod,autoscale 按缓存的 pod_spec(即存量配置)重建。

与 `config_sync` 共用串行化锁:上一次操作未完成 → 409 `CONFIG_SYNC_BUSY`。非幂等但收敛:每次调用 = 一轮全量日落重建,**成功后勿自动重试**。

### 2.2 入参

Envelope 外层见 §0.2;**`rawdata` 必须为空对象 `{}`**(非空 → 400 `VALIDATION`)。

### 2.3 返回值(`rawdata`)

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | bool | 固定 `true` |
| `scopes_refreshed` | int | 完成刷新的 scope 数(悬挂引用的 scope 跳过不计) |
| `pods_sunset` | int | 被软摘除的 Pod 总数(仅统计 SM 候选集内成员;从未被 route 过的 RM 暖 Pod 不计数,但同样被代次日落) |
| `generations` | object | `{scope_id: 新代次号}` 逐 scope 返回(bump 后的 `generation` 值,单调递增) |

### 2.4 curl 示例

```bash
curl -s -X POST "http://127.0.0.1:8091/api/session/config_refresh" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "config_refresh",
    "metadata": {
      "request_id": "cfg-refresh-20260904-001",
      "user_id": "ops",
      "bot_id": "ops",
      "extra": {"group_id": "ops"}
    },
    "rawdata": {}
  }'
```

### 2.5 返回值示例

成功(2 个 scope、1 个在候选集的 Pod 被日落,两 scope 代次均升到 1):

```json
{
  "ok": true,
  "scopes_refreshed": 2,
  "pods_sunset": 1,
  "generations": {
    "scope-vip": 1,
    "scope-default": 1
  }
}
```

失败(400,带了载荷):

```json
{
  "type": "config_refresh",
  "metadata": { "request_id": "req-6ceb55bc", "...": "..." },
  "rawdata": {},
  "ok": false,
  "error_code": "VALIDATION",
  "error_message": "config_refresh takes no payload; send config via config_sync",
  "version": "1"
}
```

忙(409,上一次 config_sync/config_refresh 仍在执行或受影响 scope 有日落 Pod 待回收):

```json
{
  "type": "config_refresh",
  "metadata": { "request_id": "...", "...": "..." },
  "rawdata": {},
  "ok": false,
  "error_code": "CONFIG_SYNC_BUSY",
  "error_message": "a previous config_refresh is still in progress",
  "version": "1"
}
```

---

## 3. 可视化端点 `GET /visualization/*`

全部只读、用于定位问题(实例/依赖/后台任务总览、会话与 scope 池状态、DB 配置、请求统计、趋势采样、系统评估报告)。通用约定见 §0.4。辅助端点 `GET /healthz`(进程就绪探针,503 = 未就绪)返回 `{ok, instance_id, service_type: "runtime", namespace, pod_name, hostname}`。

### 3.1 `/visualization/overview` —— 实例总览

**功能**:本实例版本/配置摘要(脱敏)+ 依赖 readiness + 7 个后台任务的间隔/超时/计数器/当前 leader。排查"哪个副本在跑后台任务/依赖是否健康"入口。

**入参**:无。

**返回字段**:

| 字段 | 类型 | 说明 |
|---|---|---|
| `mode` | str | 运行模式(`local`/`server`) |
| `uptime_sec` | float | 进程存活秒数 |
| `pid` / `python` / `platform` | int/str/str | 进程号 / Python 版本 / 平台 |
| `config` | object | 脱敏配置摘要:命名空间、5 个后台任务间隔、`scope_full_timeout`、`default_session_ttl`、自评估四项(sample_interval/interval/llm_enabled/pod_budget)、`kubeconfig`(脱敏 `"***"`)、服务 host/port、`redis_url`(剥凭据)、DB 类型/主机/库名 |
| `readiness` | object | 依赖就绪:`{db, redis, kubernetes, lock, cache, ready}` |
| `jobs` | list[object] | 7 个后台任务(`sm_sweep`/`rm_autoscale`/`rm_reclaim`/`rm_watch`/`rm_reconcile`/`sys_sample`/`sys_eval`)逐个:`name`、`instance_id`、`running`、`ticks`/`ok_ticks`/`error_ticks`/`aborted_ticks`/`timedout_ticks`/`lock_misses`(计数器)、`last_tick_at`(epoch 秒)、`last_duration_ms`、`last_error`、`interval_sec`、`tick_timeout_sec`、`leader`(`{instance_id, is_local, ttl_sec}` 或 `null`——tick 间隙锁瞬时缺失 → `null` 属正常) |

**curl 与返回示例**:

```bash
curl -s "http://127.0.0.1:8091/visualization/overview"
```

```json
{
  "mode": "local",
  "uptime_sec": 0.2,
  "pid": 738541,
  "python": "3.11.4",
  "platform": "Linux-5.15.0-161-generic-x86_64-with-glibc2.35",
  "config": {
    "mode": "local",
    "default_namespace": "default",
    "sweep_interval": 1,
    "autoscale_interval": 1,
    "reclaim_interval": 1,
    "watch_interval": 10,
    "reconcile_interval": 30,
    "scope_full_timeout": 30.0,
    "default_session_ttl": 60,
    "eval_sample_interval": 30,
    "eval_interval": 300,
    "eval_llm_enabled": false,
    "eval_pod_budget": 0,
    "kubeconfig": null,
    "service_host": "0.0.0.0",
    "service_port": 8090,
    "redis_url": "redis://localhost:6379/0",
    "db_type": "none",
    "db_host": null,
    "db_name": null
  },
  "readiness": {
    "db": true,
    "redis": true,
    "kubernetes": null,
    "lock": null,
    "cache": null,
    "ready": true
  },
  "jobs": [
    {
      "name": "sm_sweep",
      "instance_id": "ecs-38b3-0002:650ec2e1",
      "running": true,
      "ticks": 1,
      "ok_ticks": 1,
      "error_ticks": 0,
      "aborted_ticks": 0,
      "timedout_ticks": 0,
      "lock_misses": 0,
      "last_tick_at": 1788490029.025,
      "last_duration_ms": 3.18,
      "last_error": null,
      "interval_sec": 1,
      "tick_timeout_sec": 30,
      "leader": null
    }
  ],
  "ok": true,
  "instance_id": "ecs-38b3-0002:650ec2e1",
  "generated_at": 1788490029.076
}
```

> `jobs` 实际返回 7 项,此处示例截断为 1 项。

### 3.2 `/visualization/scopes` —— 全部 scope 清单

**功能**:全部 scope(RM 键 ∪ 路由快照)逐行容量摘要——scope 池拓扑总览页数据源。

**入参**(query):

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 100 | 返回行数上限,夹取 [1, 500](行读放大 6 读/scope,大规模部署调小) |

**返回字段**:

| 字段 | 类型 | 说明 |
|---|---|---|
| `scopes` | list[object] | 逐 scope 摘要,字段见下 |
| `total` | int | 全部 scope 总数 |
| `truncated` | bool | 是否因 `limit` 截断 |

`scopes[]` 行字段:

| 字段 | 类型 | 说明 |
|---|---|---|
| `scope_id` | str | scope 标识 |
| `phase` | str | 生效分类:`active`(正常)/ `disabled`(scope 禁用/过期或模板禁用/缺失)/ `missing_rm_cfg`(快照生效但 RM 无 config 键)/ `orphan_rm`(RM 有键但不在快照) |
| `template_id` | str? | 引用模板 |
| `scope_enabled` | bool? | scope 自身 enabled |
| `expires_at` | str? | 过期时间(ISO-8601) |
| `pods` / `idle` / `deploying` | int | Pod 总数 / 空闲数 / 部署中数 |
| `session_count` / `waiters` | int | 活跃会话数 / 等待队列长度 |
| `max_pods` / `min_idle_pods` | int | Pod 上限(派生 ⌈sc/pc⌉)/ 最小热备 |
| `scope_concurrency` / `pod_concurrency` / `session_ttl` | int? | 模板策略字段(模板缺失时为 `null`) |
| `max_waiters` | int? | 等待队列上限(2 × scope_concurrency) |

**curl 与返回示例**:

```bash
curl -s "http://127.0.0.1:8091/visualization/scopes?limit=100"
```

```json
{
  "scopes": [
    {
      "scope_id": "scope-default",
      "phase": "active",
      "template_id": "tpl-standard",
      "scope_enabled": true,
      "expires_at": null,
      "pods": 1,
      "idle": 0,
      "deploying": 0,
      "session_count": 1,
      "waiters": 0,
      "max_pods": 3,
      "min_idle_pods": 1,
      "scope_concurrency": 6,
      "pod_concurrency": 2,
      "session_ttl": 120,
      "max_waiters": 12
    },
    {
      "scope_id": "scope-vip",
      "phase": "active",
      "template_id": "tpl-vip",
      "scope_enabled": true,
      "expires_at": null,
      "pods": 0,
      "idle": 0,
      "deploying": 0,
      "session_count": 0,
      "waiters": 0,
      "max_pods": 2,
      "min_idle_pods": 0,
      "scope_concurrency": 3,
      "pod_concurrency": 2,
      "session_ttl": 300,
      "max_waiters": 6
    }
  ],
  "total": 2,
  "truncated": false,
  "ok": true,
  "instance_id": "ecs-38b3-0002:650ec2e1",
  "generated_at": 1788490029.076
}
```

### 3.3 `/visualization/scope` —— 单 scope 池详情

**功能**:单 scope 的 RM 池状态(逐 Pod 详情)/ SM 容量闸门与等待队列 / 路由快照内定义。

**入参**(query):

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `scope_id` | str | 是 | — | scope 标识 |
| `limit` | int | 否 | 50 | 逐 Pod 详情条数上限,夹取 [1, 500] |

**返回字段**:

| 字段 | 类型 | 说明 |
|---|---|---|
| `scope_id` | str | scope 标识 |
| `phase` | str | 生效分类(同 §3.2) |
| `rm` | object | RM 池状态,字段见下 |
| `sm` | object | SM 侧状态,字段见下 |

`rm` 字段:

| 字段 | 类型 | 说明 |
|---|---|---|
| `rm.pod_count` / `idle_count` / `deploying_count` / `deploy_followers` | int | Pod 总数 / 空闲数 / 部署中数 / deploy follower 等待数 |
| `rm.scope_config` | object | RM 缓存的池配置(脱敏):`min_idle_pods`/`max_pods`/`pod_ttl`/`pod_concurrency`/`deploy_ver`/`pod_spec_json`(deploy 子集 JSON 串)/`generation` |
| `rm.pods` | list[object] | 逐 Pod:`pod_id`、`idle`、`health_fails`、`idle_since`、`scope_id`、`pod_sse_url`、`pod_ip`、`namespace`、`deploy_ver`、`phase`、`created_ts`、`sse_port`、`health_path`、`generation` |
| `rm.total_pods` / `truncated` | int/bool | 总数与是否截断 |

`sm` 字段:

| 字段 | 类型 | 说明 |
|---|---|---|
| `sm.waiters` / `session_count` / `candidate_pods` | int/int/list | 等待队列 / 活跃会话 / 候选集 Pod id |
| `sm.capacity` | object? | 生效容量闸门(快照无此 scope 的孤儿为 `null`):`template_id`、`template_enabled`、`scope_enabled`、`expires_at`、`scope_concurrency`、`pod_concurrency`、`session_ttl`、`pod_ttl`、`min_idle_pods`、`max_pods`、`max_waiters`(2×sc)、`session_utilization`/`waiter_utilization`(0–1)、`route_budget_sec`(= `scope_full_timeout + ready_timeout + 10`) |
| `sm.routing` | object? | 快照内路由定义:`scope_id`、`index`、`template_id`、`routing_rules`、`enabled`、`expires_at`(不在快照为 `null`) |

**curl 与返回示例**:

```bash
curl -s "http://127.0.0.1:8091/visualization/scope?scope_id=scope-default&limit=50"
```

```json
{
  "scope_id": "scope-default",
  "phase": "active",
  "rm": {
    "pod_count": 1,
    "idle_count": 0,
    "deploying_count": 0,
    "deploy_followers": 0,
    "scope_config": {
      "min_idle_pods": "1",
      "max_pods": "3",
      "pod_ttl": "600",
      "deploy_ver": "7257231fcba16409",
      "pod_spec_json": "{\"agent_image\": \"…\", \"namespace\": \"agent-runtime-pool\", \"sse_port\": 8086, \"sse_path\": \"/sse\", \"health_path\": \"/api/v1/health\", \"agent_env\": {\"AGENT_HTTP_ENABLED\": \"true\", \"AGENT_HTTP_PORT\": \"8086\"}, \"agent_cpu_request\": \"500m\", \"…\": \"deploy 子集完整 JSON(已脱敏)\"}",
      "generation": "1"
    },
    "pods": [
      {
        "pod_id": "agentserver-yaay2tnb",
        "idle": false,
        "health_fails": 0,
        "idle_since": null,
        "scope_id": "scope-default",
        "pod_sse_url": "http://10.42.0.2:8086/sse",
        "pod_ip": "10.42.0.2",
        "namespace": "agent-runtime-pool",
        "deploy_ver": "7257231fcba16409",
        "phase": "created",
        "created_ts": "1788490029",
        "sse_port": "8086",
        "health_path": "/api/v1/health",
        "generation": ""
      }
    ],
    "total_pods": 1,
    "truncated": false
  },
  "sm": {
    "waiters": 0,
    "session_count": 1,
    "candidate_pods": [],
    "capacity": {
      "template_id": "tpl-standard",
      "template_enabled": true,
      "scope_enabled": true,
      "expires_at": null,
      "scope_concurrency": 6,
      "pod_concurrency": 2,
      "session_ttl": 120,
      "pod_ttl": 600,
      "min_idle_pods": 1,
      "max_pods": 3,
      "max_waiters": 12,
      "session_utilization": 0.167,
      "waiter_utilization": 0.0,
      "route_budget_sec": 340.0
    },
    "routing": {
      "scope_id": "scope-default",
      "index": 100,
      "template_id": "tpl-standard",
      "routing_rules": "",
      "enabled": true,
      "expires_at": null
    }
  },
  "ok": true,
  "instance_id": "ecs-38b3-0002:650ec2e1",
  "generated_at": 1788490029.079
}
```

> `pod_spec_json` 为完整 deploy 子集 JSON 串,此处中段截断示意。

404 示例(RM 无键、无会话、且不在快照):

```json
{ "ok": false, "detail": "scope not found: scope-nope" }
```

### 3.4 `/visualization/session` —— 单会话状态

**功能**:单会话 HASH / 到期 / 所属 scope 等待队列 / 绑定 Pod 的 SSE 地址与版本。

**入参**(query):

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | str | 是 | 会话标识 |

**返回字段**:

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | str | 会话标识 |
| `session` | object | 会话 HASH:`scope_id`、`pod_id`、`expiry`(到期 epoch 秒)、`session_ttl` |
| `expiry_score` | float? | 到期分数(epoch 秒) |
| `ttl_remaining_s` | float? | 剩余存活秒数 |
| `scope` | object? | 所属 scope(有 `scope_id` 才返回):`scope_id`、`waiters`、`session_count`、`candidate_pods` |
| `pod` | object? | 绑定 Pod(有绑定才返回):`pod_id`、`sse_url`、`deploy_ver`、`session_ids_on_pod`(同 Pod 全部会话) |

**curl 与返回示例**:

```bash
curl -s "http://127.0.0.1:8091/visualization/session?session_id=sess-7f3a2b"
```

```json
{
  "session_id": "sess-7f3a2b",
  "session": {
    "scope_id": "scope-default",
    "pod_id": "agentserver-yaay2tnb",
    "expiry": "1788490149",
    "session_ttl": "120"
  },
  "expiry_score": 1788490149.0,
  "ttl_remaining_s": 119.9,
  "scope": {
    "scope_id": "scope-default",
    "waiters": 0,
    "session_count": 1,
    "candidate_pods": []
  },
  "pod": {
    "pod_id": "agentserver-yaay2tnb",
    "sse_url": "http://10.42.0.2:8086/sse",
    "deploy_ver": "7257231fcba16409",
    "session_ids_on_pod": ["sess-7f3a2b"]
  },
  "ok": true,
  "instance_id": "ecs-38b3-0002:650ec2e1",
  "generated_at": 1788490029.083
}
```

### 3.5 `/visualization/config` —— DB 配置观测

**功能**:DB 现行配置(routing scopes + templates 摘要,脱敏)+ 路由快照观测 + RM Redis 缓存计数。核对"下发是否落库、快照是否一致"。

**入参**:无。

**返回字段**:

| 字段 | 类型 | 说明 |
|---|---|---|
| `routing_scopes` | list[object] | DB 全部 scope(wire 形态):`scope_id`、`index`、`template_id`、`routing_rules`、`enabled`、`expires_at` |
| `templates` | list[object] | 模板摘要(脱敏):`template_id`、`enabled`、`agent_image`、`namespace`、`scope_concurrency`、`session_ttl`、`pod_concurrency`、`max_pods`、`min_idle_pods`、`pod_ttl`、`kubeconfig`(脱敏) |
| `routing_snapshot` | object | 快照观测:`exists`、`ver`(重建时间戳)、`scope_count`、`template_count` |
| `redis` | object | `rm_scope_configs`(RM 侧 scope config 键数)、`rm_registered_pods`(注册 Pod 总数) |

**curl 与返回示例**:

```bash
curl -s "http://127.0.0.1:8091/visualization/config"
```

```json
{
  "routing_scopes": [
    {
      "scope_id": "scope-vip",
      "index": 0,
      "template_id": "tpl-vip",
      "routing_rules": "user_id in ('user-vip-1', 'user-vip-2')",
      "enabled": true,
      "expires_at": null
    },
    {
      "scope_id": "scope-default",
      "index": 100,
      "template_id": "tpl-standard",
      "routing_rules": "",
      "enabled": true,
      "expires_at": null
    }
  ],
  "templates": [
    {
      "template_id": "tpl-standard",
      "enabled": true,
      "agent_image": "swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-amd64:0.3.1",
      "namespace": "agent-runtime-pool",
      "scope_concurrency": 6,
      "session_ttl": 120,
      "pod_concurrency": 2,
      "max_pods": 3,
      "min_idle_pods": 1,
      "pod_ttl": 600,
      "kubeconfig": null
    },
    {
      "template_id": "tpl-vip",
      "enabled": true,
      "agent_image": "swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-amd64:0.3.1",
      "namespace": "agent-runtime-pool",
      "scope_concurrency": 3,
      "session_ttl": 300,
      "pod_concurrency": 2,
      "max_pods": 2,
      "min_idle_pods": 0,
      "pod_ttl": 900,
      "kubeconfig": null
    }
  ],
  "routing_snapshot": {
    "exists": true,
    "ver": 1788490029,
    "scope_count": 2,
    "template_count": 2
  },
  "redis": {
    "rm_scope_configs": 2,
    "rm_registered_pods": 1
  },
  "ok": true,
  "instance_id": "ecs-38b3-0002:650ec2e1",
  "generated_at": 1788490029.089
}
```

### 3.6 `/visualization/stats` —— 请求统计

**功能**:per-endpoint 请求统计(**命中实例视角**)+ per-scope 计数(**Redis 全副本聚合**,route/acquire 计数经每副本 5s 批量 flush,任意副本读到同一全局值)。

**入参**:无。

**返回字段**:

| 字段 | 类型 | 说明 |
|---|---|---|
| `started_at` / `uptime_sec` / `pid` | float/float/int | 实例启动时间(epoch)/ 存活秒数 / 进程号 |
| `latency_window` | int | 延迟分位数的滑动窗口(请求数) |
| `requests` | object | `{total, ok, error}` 汇总 |
| `endpoints` | object | 逐端点(命中实例视角):`total`、`ok`、`error`、`by_error_code`(错误码→次数)、`p50_ms`、`p95_ms`、`max_ms` |
| `scopes` | object | 逐 scope 全局计数(有值的 scope 才出现):`route_total`/`route_ok`/`route_err_scope_full_timeout`/`route_err_scope_queue_full`/`route_err_no_pod_available`/`acq_deployed`/`acq_reuse`/`acq_need_acquire`/`ev_reclaimed`/`ev_pod_dead`/`ev_autoscale_deployed` 等(单调递增累计值) |

**curl 与返回示例**:

```bash
curl -s "http://127.0.0.1:8091/visualization/stats"
```

```json
{
  "started_at": 1788490834.5,
  "uptime_sec": 1.4,
  "latency_window": 1024,
  "requests": {
    "total": 5,
    "ok": 3,
    "error": 2
  },
  "endpoints": {
    "config_refresh": {
      "total": 2, "ok": 1, "error": 1,
      "by_error_code": { "VALIDATION": 1 },
      "p50_ms": 7.6, "p95_ms": 7.6, "max_ms": 7.6
    },
    "config_sync": {
      "total": 2, "ok": 1, "error": 1,
      "by_error_code": { "VALIDATION": 1 },
      "p50_ms": 82.8, "p95_ms": 82.8, "max_ms": 82.8
    },
    "route": {
      "total": 1, "ok": 1, "error": 0,
      "by_error_code": {},
      "p50_ms": 5.5, "p95_ms": 5.5, "max_ms": 5.5
    }
  },
  "pid": 742117,
  "scopes": {
    "scope-default": {
      "acq_need_acquire": 1,
      "acq_deployed": 1,
      "route_total": 1,
      "route_ok": 1,
      "ev_autoscale_deployed": 1
    }
  },
  "ok": true,
  "instance_id": "ecs-38b3-0002:320b06dc",
  "generated_at": 1788490835.9
}
```

### 3.7 `/visualization/recent_errors` —— 最近错误

**功能**:本进程最近错误环形缓冲(新在前,单进程视角,容量 200)。

**入参**(query):

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 50 | 返回条数上限,夹取 [1, 200] |

**返回字段**:

| 字段 | 类型 | 说明 |
|---|---|---|
| `errors` | list[object] | 逐条(新在前):`ts`(epoch 秒)、`endpoint`、`error_code`、`duration_ms`、`detail` |

**curl 示例**:

```bash
curl -s "http://127.0.0.1:8091/visualization/recent_errors?limit=20"
```

```json
{
  "errors": [
    {
      "ts": 1788490029.0,
      "endpoint": "config_refresh",
      "error_code": "VALIDATION",
      "duration_ms": 1.2,
      "detail": "config_refresh takes no payload; send config via config_sync"
    }
  ],
  "ok": true,
  "instance_id": "ecs-38b3-0002:650ec2e1",
  "generated_at": 1788490029.1
}
```

### 3.8 `/visualization/history` —— 单 scope 历史趋势

**功能**:单 scope 历史趋势采样(`sys_sample` job 每 `eval_sample_interval`(默认 30s)一拍;数据在 Redis,25h TTL——重启不丢、**全局一致**;新在前)。

**入参**(query):

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `scope_id` | str | 是 | — | scope 标识 |
| `window_sec` | int | 否 | 3600 | 回看窗口(秒),夹取 [60, 86400] |
| `limit` | int | 否 | 240 | 点数上限,夹取 [1, 1440](超限保留最新) |

**返回字段**:

| 字段 | 类型 | 说明 |
|---|---|---|
| `scope_id` / `window_sec` | str/int | 回显 |
| `points` | list[object] | 采样点(新在前),紧凑短键见下表 |
| `counters_current` | object | 该 scope 当前累计计数(同 §3.6 `scopes.{scope_id}`) |

`points[]` 短键语义(计数项为单调累计值,相邻差分即速率):

| 键 | 含义 | 键 | 含义 |
|---|---|---|---|
| `t` | 采样时间(epoch 秒) | `rt` | route 总数累计 |
| `p` | Pod 总数 | `ef` | `SCOPE_FULL_TIMEOUT` 错误累计 |
| `i` | 空闲 Pod 数 | `eq` | `SCOPE_QUEUE_FULL` 错误累计 |
| `d` | 部署中 Pod 数 | `en` | `NO_POD_AVAILABLE` 错误累计 |
| `s` | 活跃会话数 | `ad` | acquire 新部署 Pod 累计 |
| `w` | 等待队列长度 | `ar` | acquire 复用 Pod 累计 |
| — | — | `rc` | 会话到期回收累计 |
| — | — | `dd` | Pod 死亡回收累计 |

**curl 与返回示例**:

```bash
curl -s "http://127.0.0.1:8091/visualization/history?scope_id=scope-default&window_sec=3600"
```

```json
{
  "scope_id": "scope-default",
  "window_sec": 3600,
  "points": [
    {
      "t": 1788490835, "p": 2, "i": 1, "d": 0, "s": 1, "w": 0,
      "rt": 1, "ef": 0, "eq": 0, "en": 0, "ad": 1, "ar": 0, "rc": 0, "dd": 0
    },
    {
      "t": 1788490834, "p": 1, "i": 0, "d": 0, "s": 1, "w": 0,
      "rt": 1, "ef": 0, "eq": 0, "en": 0, "ad": 1, "ar": 0, "rc": 0, "dd": 0
    }
  ],
  "counters_current": {
    "acq_need_acquire": 1,
    "acq_deployed": 1,
    "route_total": 1,
    "route_ok": 1,
    "ev_autoscale_deployed": 1
  },
  "ok": true,
  "instance_id": "ecs-38b3-0002:320b06dc",
  "generated_at": 1788490835.904
}
```

### 3.9 `/visualization/evaluation` —— 系统评估报告

**功能**:系统自评估报告(`sys_eval` job 每 `eval_interval`(默认 300s)产出;读 Redis,**全局视角**)。`latest` 为完整报告,`history` 为瘦身条目;无报告(评估间隔未到/从未跑过)`latest=null` 属正常态,不 404。LLM 段只含 status/model/latency,无凭证。

**入参**(query):

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 10 | 历史条数上限,夹取 [1, 50] |

**返回字段**:

| 字段 | 类型 | 说明 |
|---|---|---|
| `latest` | object? | 完整报告(可能为 `null`),字段见下 |
| `history` | list[object] | 瘦身历史条目(新在前,去 findings 只留 summary):`generated_at`、`instance_id`、`llm`、`summary` |

`latest` 字段:

| 字段 | 类型 | 说明 |
|---|---|---|
| `generated_at` | float | 产出时间(epoch 秒) |
| `instance_id` | str | 产出报告的实例 |
| `window_sec` | int | 评估回看窗口 |
| `llm` | object | `{status: ok|error|disabled, model, latency_ms, error}`(未配置 env → `disabled`;调用失败/输出不可解析 → `error` + 原因,纯规则报告照常) |
| `llm_analysis` | object? | **仅 llm.status=ok 时存在**:`{summary(一段话总评), risk_notes: [str](逐条风险与规则评估), additional_findings: [Finding](LLM 补充建议,已逐项过策略字段白名单,`source="llm"`), confidence: low|medium|high}` |
| `summary` | object | `{scopes_total, active, findings_total, by_severity: {info, warn, critical}}` |
| `findings` | list[object] | 建议/发现清单(规则引擎 `source="rule"` + LLM 补充 `source="llm"`):`{id, severity, source, target{scope_id?, template_id?}, field, current, suggested, rationale, evidence[], change_class, rebuild_cost}` |
| `trend` | object | 逐 scope 趋势:`{pods, idle, session_count, waiters, cold_start_ratio, err_full_timeout, err_queue_full, saturation_peak, points[]}`(points 每 10 采样抽 1,含 t/s/i/p/w) |
| `service` | object | 评估时的服务参数:`scope_full_timeout`/`eval_interval`/`eval_sample_interval`/`pod_budget` |
| `caveats` | list[str] | 报告使用注意(如:建议均为 B 类策略字段、需人工审阅后经 config_sync 应用,本服务不自动改配置) |

> 启用 LLM 的部署注意(推理模型):GLM 系等推理模型的 reasoning 计入输出 token 预算,须设 `AGENT_RUNTIME_EVAL_LLM_MAX_TOKENS≈16384` 并相应抬 `AGENT_RUNTIME_EVAL_LLM_TIMEOUT`(实测默认 1024 时 content 为空、解析必败;详见 `docs/spec/evaluation.md` LLM 层一节)。

**curl 与返回示例**:

```bash
curl -s "http://127.0.0.1:8091/visualization/evaluation?limit=10"
```

> 场景:单 scope 高负载——1h 窗口 31 个采样并发恒顶格 6/6、Pod 恒 3/3 顶格、等待队列 0–10 震荡并触发 `SCOPE_QUEUE_FULL`。规则引擎产出 4 条(`source="rule"`),LLM(GLM-5.3)总评 + 3 条补充建议(`source="llm"`)。

```json
{
  "latest": {
    "generated_at": 1788492852.5,
    "instance_id": "ecs-38b3-0002:471b7c54",
    "window_sec": 86400,
    "llm": {
      "status": "ok",
      "model": "GLM-5.3",
      "latency_ms": 286555.7,
      "error": ""
    },
    "llm_analysis": {
      "summary": "scope-default 处于持续且严重的容量不足状态:3600s 观察窗口内全部 31 个采样 session_count 均为 6/6(并发饱和率 100%),pods 恒为 3/3 顶格且 idle 恒为 0,等待队列在 0–10(上限 12)间剧烈震荡并触发 3 次 SCOPE_QUEUE_FULL,容量错误速率 13.0/h 达阈值(6.0/h)的约 2.2 倍,主要风险是面向用户的会话排队超时与拒绝,其次是空闲 Pod 回收后 600s 内重建 6 次带来的部署 churn;规则产出的四条 findings 中三条 warn 收敛于同一动作(scope_concurrency 6→12),证据链相互印证、方向与幅度均合理——100% 饱和、错误率超标 2.2 倍与队列满员共同支持直接翻倍而非渐进式调整——应最优先合并执行,D-POD-TTL-CHURN 的 600→1200s …",
      "risk_notes": [
        "规则评估·优先级最高(合理):D-CONCURRENCY-SATURATION、D-CAPACITY-ERRORS、D-WAITER-PRESSURE 三条 warn 收敛于同一变更(scope_concurrency 6→12),证据互证",
        "规则评估·幅度判断(合理):在饱和率 100%、队列已出现满员的情况下直接翻倍(6→12)优于渐进(如 6→9),且 max_waiters=2×sc 联动扩至 24 可为 0→10 的突发排队留出余量;但数据无法推断真实需求上限,变更后应 …",
        "副作用与前置确认:sc 6→12 将使 max_pods 由 3 联动升至 6,pod 数翻倍;pod_budget=0 表示未设全局 pod 预算上限,但实际集群资源能否承受 6 个 pod 无法从数据确认(低置信度),建议变更前核实资源",
        "规则未覆盖的结构性问题:满载时 pod 数恰为 ⌈sc/pc⌉=max_pods,系统无余量创建空闲 pod,min_idle_pods=1 的保底目标在 31/31 采样中从未达成(idle=0)…",
        "需求形态为突发型:waiters 在相邻采样(120s 间隔)间于 0/4/10 间跳变,存在短时突发;sc=12 后瞬时排队仍可能出现属正常 …"
      ],
      "additional_findings": [
        {
          "id": "A-MIN-IDLE-UNMET", "severity": "warn", "source": "llm",
          "target": { "scope_id": "scope-default", "template_id": "tpl-standard" },
          "field": "min_idle_pods", "current": "1",
          "suggested": "2(需在 scope_concurrency 提升至 12、max_pods 联动抬升后再执行)",
          "rationale": "31/31 采样 idle=0,min_idle_pods=1 的保底从未达成:满载时 3 个 pod 恰好承载 6 会话且 max_pods=⌈sc/pc⌉ 无余量,该配置当前形同虚设;叠加 waiter 0→10 的突发形态,尖峰需求无热备 pod 只能排队或超时。当前状态下单独调高该值无效,应在 sc 扩容解除 max_pods 封顶后调至 2,保留热备缓冲以降低突发冷启动延迟。",
          "evidence": [], "change_class": "B", "rebuild_cost": "即时生效,无重建"
        },
        {
          "id": "A-POD-CONCURRENCY-FOOTPRINT", "severity": "info", "source": "llm",
          "target": { "scope_id": "scope-default", "template_id": "tpl-standard" },
          "field": "pod_concurrency", "current": "2",
          "suggested": "3(备选方案,仅在确认单 pod 资源有余量时采用)",
          "rationale": "…(备选扩容路径:pc 2→3 使 max_pods=⌈6/3⌉=2,以更少 Pod 承载同容量)…",
          "evidence": [], "change_class": "B", "rebuild_cost": "即时生效,无重建"
        },
        {
          "id": "A-SESSION-TTL-AUDIT", "severity": "info", "source": "llm",
          "target": { "scope_id": "scope-default", "template_id": "tpl-standard" },
          "field": "session_ttl", "current": "120",
          "suggested": "90(仅在审计确认会话普遍持有至 TTL 到期而非自然完成后执行)",
          "rationale": "…(缩短槽位占用以提升周转)…",
          "evidence": [], "change_class": "B", "rebuild_cost": "即时生效,无重建"
        }
      ],
      "confidence": "high"
    },
    "summary": {
      "scopes_total": 1,
      "active": 1,
      "findings_total": 7,
      "by_severity": { "info": 3, "warn": 4, "critical": 0 }
    },
    "findings": [
      {
        "id": "D-CONCURRENCY-SATURATION", "severity": "warn", "source": "rule",
        "target": { "scope_id": "scope-default", "template_id": "tpl-standard" },
        "field": "scope_concurrency", "current": "6", "suggested": "12",
        "rationale": "窗口 3600s 内并发使用率 ≥95% 的采样占 100%,容量长期顶格",
        "evidence": ["saturated_share=1.00 (threshold=0.8)", "samples=31",
                     "scope_concurrency=6"],
        "change_class": "B", "rebuild_cost": "即时生效,无重建"
      },
      {
        "id": "D-CAPACITY-ERRORS", "severity": "warn", "source": "rule",
        "target": { "scope_id": "scope-default", "template_id": "tpl-standard" },
        "field": "scope_concurrency", "current": "6", "suggested": "12",
        "rationale": "容量错误(SCOPE_FULL_TIMEOUT+SCOPE_QUEUE_FULL)速率 13.0/h 超阈值 6.0/h;pods 长期=3(顶格占比 100%),升 sc 会联动抬升 max_pods=⌈sc/pc⌉",
        "evidence": ["err_rate_per_hour=13.0", "threshold=6.0",
                     "pods_at_max_share=1.00"],
        "change_class": "B", "rebuild_cost": "即时生效,无重建"
      },
      {
        "id": "D-POD-TTL-CHURN", "severity": "info", "source": "rule",
        "target": { "scope_id": "scope-default", "template_id": "tpl-standard" },
        "field": "pod_ttl", "current": "600s", "suggested": "1200s",
        "rationale": "24h 内回收后 600s 内重建 6 次——空闲 Pod 被回收后很快又要用,pod_ttl 偏短造成部署churn",
        "evidence": ["churn_pairs=6", "threshold=3"],
        "change_class": "B", "rebuild_cost": "即时生效,无重建"
      },
      {
        "id": "D-WAITER-PRESSURE", "severity": "warn", "source": "rule",
        "target": { "scope_id": "scope-default", "template_id": "tpl-standard" },
        "field": "scope_concurrency", "current": "6", "suggested": "12",
        "rationale": "等待队列峰值 10/12 且发生 SCOPE_QUEUE_FULL 3 次——max_waiters=2×sc 联动,升 sc 同时扩队列",
        "evidence": ["peak_waiters=10", "max_waiters=12", "queue_full=3"],
        "change_class": "B", "rebuild_cost": "即时生效,无重建"
      },
      "…": "…(3 条 source=\"llm\" 的补充建议,同 llm_analysis.additional_findings)"
    ],
    "trend": {
      "scope-default": {
        "pods": 3,
        "idle": 0,
        "session_count": 6,
        "waiters": 10,
        "cold_start_ratio": 0.023,
        "err_full_timeout": 10,
        "err_queue_full": 3,
        "saturation_peak": 1.0,
        "points": [
          { "t": 1788489232, "s": 6, "i": 0, "p": 3, "w": 10 },
          { "t": 1788489352, "s": 6, "i": 0, "p": 3, "w": 4 },
          { "t": 1788489472, "s": 6, "i": 0, "p": 3, "w": 0 },
          "…": "…(共 31 点,120s 间隔)"
        ]
      }
    },
    "service": {
      "scope_full_timeout": 30.0,
      "eval_interval": 300,
      "eval_sample_interval": 30,
      "pod_budget": 0
    },
    "caveats": [
      "建议均为 B 类策略字段(即时生效,无 Pod 重建);A 类(deploy 子集)变更将触发存量 Pod 日落重建且有 409 CONFIG_SYNC_BUSY 中间态风险,本报告不涉及",
      "建议经人工审阅后由 Claw Manager 经 config_sync 下发应用;本服务不自动改配置"
    ]
  },
  "history": [
    {
      "generated_at": 1788492852.5,
      "instance_id": "ecs-38b3-0002:471b7c54",
      "llm": { "status": "ok", "model": "GLM-5.3", "latency_ms": 286555.7,
               "error": "" },
      "summary": { "scopes_total": 1, "active": 1, "findings_total": 7,
                   "by_severity": { "info": 3, "warn": 4, "critical": 0 } }
    }
  ],
  "ok": true,
  "instance_id": "ecs-38b3-0002:471b7c54",
  "generated_at": 1788492855.1
}
```

> 上例为 LLM 启用(`AGENT_RUNTIME_EVAL_LLM_*` env,模型 GLM-5.3)的真实报告,长文案处有截断省略;未配置 LLM 时 `llm.status="disabled"`、无 `llm_analysis`,`findings` 只含 `source="rule"` 条目。

---

## 附:本文档示例的生成方式

返回值示例均为真实响应(local 模式:fakeredis + SQLite + FakeK8s 拉起 `create_app`,经 ASGI 打真实 HTTP 请求捕获;`instance_id`/`pid`/epoch 时间戳等环境值以实际部署为准)。curl 载荷取自 HLD §3.1(develop1 分支 `8a4fbee9`)。§3.9 的 LLM 报告为真实调用 GLM-5.3 端点产出(推理模型须抬 `AGENT_RUNTIME_EVAL_LLM_MAX_TOKENS`,见该节部署注意)。接口行为变更时同步更新本文(与代码改动同一提交完成)。
