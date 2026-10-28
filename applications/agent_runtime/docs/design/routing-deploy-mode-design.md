# agent-runtime 外部预建 Pod 模式(routing 供给模式)设计

- 日期:2026-10-26
- 状态:已实现(e662fe61 起)并真环境验收(e2e --routing-provision 15/15;2026-10-26)
- 适用模块:applications/agent_runtime
- 实现记录:`../feature/2026-10-routing-deploy-mode.md`;接口契约的对外版本:`../api/config-plane-api.md` §2b
- 后续配套演进(本文设计定稿之后,部署工具层):abe12a50 三模式(routing/off;full 暂未开放)、df91def9 openshift full 形态、34195c42 routing 模式 AgentServer 静态注册——runtime 服务侧开关语义仍为本文的 full|routing

## 1. 背景与目标

agent-runtime 目前对 agent-server Pod 全托管(full 模式):流量来了不够用就自己建 Pod,空闲超时或配置换代就自己删 Pod。这要求它的 ServiceAccount 持有 pods 的 create/delete/list/watch/get 权限。

有一类客户环境给不了任何 K8s 权限——create/delete/list/watch/get 全都没有。这类客户自己有权限、也有意愿用 StatefulSet 把 agent-server 提前拉好,希望 agent-runtime 只做"分配":把会话路由到这些现成的 Pod 上,不去动它们的生死。

本设计新增 routing 供给模式,开关打开后:

- agent-runtime 永远不创建、不删除 agent-server Pod;
- 客户把 StatefulSet 拉起的 Pod 地址(headless service 域名)通过新接口注册进来,进入现有分配池;
- 路由、亲和保持、保活、驱逐等既有逻辑全部复用,gateway 侧零改动;
- Pod 挂了由 StatefulSet 自动重建,agent-runtime 通过健康探测自动感知并重新纳管;
- **一次 K8s API 调用都不发生**,ServiceAccount 不需要任何 pods 权限。

存活检测完全建立在 HTTP 健康探测上:直连注册的域名,不走 K8s。网络前提由客户保证:agent-runtime 与 gateway 都能解析并直达注册的域名(即 gateway 需在集群内或可解析 svc DNS)。

## 2. 模式开关

```
AGENT_RUNTIME_DEPLOY_MODE=full|routing
```

- `full` 为默认值,行为与现状完全一致,存量部署零影响;
- `routing` 开启本特性;
- 严格解析:只认这两个值,配错启动即报错退出,不静默回退;
- 启动后不可热更,改模式须重启。

健康探测参数沿用既有配置:探测周期 `AGENT_RUNTIME_WATCH_INTERVAL`(默认 10s)、单次超时 `AGENT_RUNTIME_HEALTH_PROBE_TIMEOUT`(默认 3s);连续失败判死次数固定为 2,判死时延约 20 秒。

## 3. 新增接口

两个管理面接口,与 cleanup/config_sync 同走 `/api/session` 前缀的信封协议(POST,`metadata.request_id` 必填)。接口只含必填字段,没有可选参数。

### 3.1 注册:POST /api/session/register_pods

把一组预拉起的 Pod 声明给某个 scope,并立即尝试纳管。

请求:

```json
{
  "scope_id": "prod-default",
  "pods": [
    {"name": "agentserver-0", "host": "agentserver-0.as-svc.agentserver.svc"},
    {"name": "agentserver-1", "host": "agentserver-1.as-svc.agentserver.svc"}
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| scope_id | string | 目标路由 scope,必须已存在于配置中,否则 400 |
| pods | 对象数组 | 每项 `name` + `host`,两者都必填;数组非空、自动去重 |

- `name`:K8s Pod 名(StatefulSet 确定名,身份与排障用,注销时也用它);
- `host`:该 Pod 的稳定域名(headless service 形态,如 `agentserver-0.as-svc.agentserver.svc`)。注册后此域名同时用作健康探测目标和 SSE 地址,Pod 重建后域名解析到新 IP,自动自愈。

端口与路径不入载荷:健康探测用的 sse_port / health_path、拼 SSE 地址用的 sse_path,由 runtime 从该 scope 的模板配置推导。约束:**客户 StatefulSet 的端口/路径必须与平台配置的模板一致**(模板定义的就是 agent-server 的运行契约)。scope 没有模板时注册返回 400。

处理逻辑:

1. 名单先写入该 scope 的声明表(Redis 持久化),这一步与 Pod 当前状态无关——客户可以在 Pod 还没起来时就调注册;
2. 逐个对 `http://{host}:{sse_port}{health_path}` 探测一次(超时同全局配置):通过 → 注册入池(空闲态),记录域名、SSE 地址、探活参数;不通/超时 → 标记 pending,由后台循环每拍重试;
3. 幂等:已在池中且探测通过的 Pod 不重复处理(防止把正在服务的 Pod 误标回空闲);同名重注册 = 覆盖声明。

响应示例:

```json
{
  "ok": true,
  "rawdata": {
    "scope_id": "prod-default",
    "registered": [
      {"pod": "agentserver-0", "pod_sse_url": "http://agentserver-0.as-svc.agentserver.svc:8086/api/v1/events/stream"}
    ],
    "already_registered": [],
    "pending": [{"pod": "agentserver-1", "reason": "probe_failed"}]
  }
}
```

错误:400 VALIDATION(参数缺失/scope 不存在/pods 为空/当前是 full 模式);503 STATE_UNAVAILABLE(Redis 故障,可重试)。

### 3.2 注销:POST /api/session/deregister_pods

把 Pod 从声明表和分配池摘掉。只解除纳管,**绝不碰物理 Pod**(缩容由客户自己在 StatefulSet 上做)。

请求:

```json
{ "scope_id": "prod-default", "pods": ["agentserver-1"] }
```

行为:驱逐该批 Pod 上的全部活跃会话(gateway 自动重路由到其他 Pod),然后注销。幂等。

响应:

```json
{ "ok": true, "rawdata": { "deregistered": ["agentserver-1"], "evicted_sessions": 3 } }
```

### 3.3 既有接口影响

- cleanup:routing 模式下直接 400 拒绝(按 label 批量删 Pod 的运维口,该模式下无权限也不该用);
- route / touch / rebind / config_sync / config_refresh:契约不变,gateway 与管理面零改动。

## 4. 运行时行为

### 4.1 分配

注册成功的 Pod 进入空闲池,之后的分配完全走既有链路:会话 route 过来,现有 Pod 有空位直接放;没空位由资源管理层从空闲池取——外部 Pod 跳过版本比对直接可用。两级容量闸门照常生效:scope 级并发上限(scope_concurrency)、单 Pod 并发上限(pod_concurrency)。

原由并发参数推导的 max_pods 在 routing 模式下**不再限制注册数量**——注册多少个,可用容量就是多少,容量规划交还客户。

### 4.2 池空

所有注册的 Pod 都忙或都 pending 时,route 返回 503 NO_POD_AVAILABLE + retry_after=1,与现在容量打满的行为一致,gateway 重试逻辑不用改。语义差别:full 模式下这个错误意味着"稍等会有新 Pod 部署出来",routing 模式下意味着"容量到顶,需扩 StatefulSet 并注册新地址"。route 侧不做同步补收养,恢复完全靠后台循环。

### 4.3 死亡检测(唯一信号 = HTTP 探测)

每周期(默认 10s)对池内全部 Pod 探测一轮,连续 2 次失败判死:

- 移出分配池、清理记账;
- 驱逐该 Pod 上全部会话,gateway 感知后重新路由到其他 Pod。

K8s 相位判死、按 get 做的孤儿对账对外部 Pod 全部停用。

### 4.4 重建恢复

挂在既有的 30 秒对账任务里的收养逻辑:声明表里有、池里没有的名字,逐个探测其域名,通了就(重新)注册。StatefulSet 同名重建完成后域名解析到新 IP,探测通过,自动重新入池,全程无人工。最坏恢复时间 ≈ 判死(约 20s)+ 重建 + 收养(≤30s)+ 服务启动,量级一分钟出头。

### 4.5 扩缩容

- 扩容:StatefulSet replicas 调大后,对新序号条目再调一次 register_pods(只传新条目即可,重传旧条目幂等无害);
- 缩容:直接缩也可以(走 4.3 死亡路径,在跑会话会被驱逐);稳妥做法是先 deregister_pods 再缩。

### 4.6 配置变更(config_sync / config_refresh)

现有机制靠版本指纹(deploy_ver)和代次(generation)淘汰旧配置的 Pod:配置变了,旧 Pod 不再接新流量,排空后回收重建。外部 Pod 的镜像规格由客户的 StatefulSet 决定,runtime 无权重建它,这套淘汰机制对外部 Pod 整体关闭:

- config_sync 改模板、config_refresh 强刷:外部 Pod 不参与版本/代次比对,继续留在池里接流量;
- config_refresh 的前置闸门(旧代次没排空完会 409 拒绝刷新)不统计外部 Pod,不会被卡死;
- 镜像/规格升级由客户自己对 StatefulSet 做滚动更新,过程自然走"判死→重建→收养"。

### 4.7 scope 删除/禁用

scope 被删除或禁用时,该 scope 的声明表被清掉,其外部 Pod 走既有的日落排空:在 session_ttl 窗口内继续服务存量会话,窗口过后移出池子(只解绑,不删物理 Pod)。与 full 模式的删除行为对齐。

### 4.8 其他

- autoscale(min_idle 预热)在 routing 模式关闭——没有创建权,预热无从谈起;
- 观测:`/visualization/scope` 增加 deploy_mode 与声明清单(已入池 / pending),Pod 信息带 external 标记。

## 5. 权限与部署

routing 模式**不需要任何 K8s 权限**(Role 可不建)。部署物只需两处增量:agent-runtime 的 env 模板与外层 jiuwenswarm-ee 的变量样例各加 `AGENT_RUNTIME_DEPLOY_MODE` 一项。

## 6. 限制与注意事项

1. gateway 与 agent-runtime 到注册域名的解析与网络可达性由客户保证(headless service 形态,gateway 需在集群内或可解析 svc DNS);
2. link 层 mTLS enforce 模式与 routing 组合暂不支持(证书物料无法注入外部 Pod),该组合启动时报错;
3. 同一部署只使用一种模式,不考虑运行中互切。

## 7. 测试与验收要点

- 单元/集成测试覆盖:模式解析(默认 full/routing/非法启动报错)、注册分流(探测通过入池/失败 pending/参数校验 400)、分配与判死、收养(同名重建自动恢复)、注销驱逐语义、五个版本豁免点、无 Lua 兼容模式双跑;
- 端到端:routing 模式起服务(ServiceAccount 零 pods 权限)→ StatefulSet + headless service 预拉 → register_pods → route 正常分配 → 删一个 Pod → 会话驱逐 + 自动收养 + 恢复分配;config_refresh 不被外部 Pod 阻塞;
- 回归:默认 full 路径全部既有用例零退化,发布门禁不受影响。
