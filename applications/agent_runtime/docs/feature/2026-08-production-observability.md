# 生产可观测性:日志体系完善 + /debug 诊断端点

- 日期:2026-08-25
- 涉及模块:service-core(logsetup/metrics/debug_api)/ session_manager / resource_manager / 框架层(service+foundation)/ 部署 / 测试 / 文档

## 背景与动机

上线生产前审阅日志现状,结论是**日志不足以定位问题**:

- 3MB 真实日志样本(~23 分钟)中 **0 条** route/touch/config_sync 业务日志、0 条 WARNING/ERROR;~13000 条为框架周期任务刷屏(rm_autoscale/rm_reclaim 1Hz,每拍「锁获取+选主+tick done」3 行)。
- `touch`/`cleanup` handler 两侧路径零日志;两个 `state.py`(Redis Lua 门面,~500 行)零日志,None/False 静默强转 `[]`(Lua 异常与正常业务分支不可区分)。
- `AGENT_RUNTIME_LOG_LEVEL` 是死配置(cli.py 的 basicConfig 被框架导入期 `setup_logging`→`dictConfig` 丢弃);日志无 request_id 关联;业务失败 WARNING 无堆栈;`_wait_ready` 最长 300s 零日志;健康探测 `except Exception: return False` 吞原因。
- K8s 容器内文件日志无卷挂载,pod 重启即丢;诊断仅有 /healthz,框架 `readiness()` 未暴露,JobRunner 无计数器。

**与需求方确认的决策**:① 保持文本格式 + 消息内补 key=value 上下文(不切 JSON——grep 友好、无平台配套);② /debug 默认开放靠网络边界,输出脱敏;③ 允许小改框架层(降噪/计数器/FrameworkError 日志);④ K8s 日志 stdout 为主(关闭容器内文件 handler)。**被否方案**:切换 JSON 结构化日志(影响所有用 openjiuwen_runtime 的服务、需日志平台配套);用框架 `ctx.logger`/audit 通道做请求关联(需改 ~10 个模块的 logger 风格,且 yaml 格式串不含 request_id 字段照样丢)。

## 方案

**日志**:
1. `logsetup.configure_logging()`(仅 cli.py 调用):重放框架 setup_logging → `AGENT_RUNTIME_LOG_LEVEL` 生效(handler 级只放宽不收紧)→ httpx 降噪 → 挂请求上下文。
2. 请求关联:contextvars + root 级 Filter/Formatter——请求期间日志行尾追加 `| request_id=… session_id=… endpoint=… instance=…`,后台任务行与原格式逐字节一致(后缀式,不用前缀污染全部行)。
3. 请求汇总中间件(`App.use()` 进 router 派发链):四端点每请求一行 INFO `request: endpoint= outcome= error_code= duration_ms=`。
4. 覆盖补齐:handler 失败统一 `_fail()`(WARNING+exc_info);`NO_POD_AVAILABLE` 粗化前留真因;acquire/follower/deploy 全路径时长与结果行;`_wait_ready` 30s 进度行;k8s 调用 DEBUG 耗时;state.eval 空表异常+慢 eval(>200ms)WARNING;config env 解析失败 WARNING;吞异常分支释放原因(健康探测/幂等缓存腐蚀/探测数据缺失按 pod 去重告警)。
5. 框架降噪:`tick lock acquired`/`single_leader claimed`→DEBUG;`tick done` 常态 DEBUG、异常/慢拍(>1s)/600 拍心跳保留 INFO;router `FrameworkError` 分支补 WARNING;JobRunner 加计数器 + `snapshot()`。
6. stdout 主形态:`OPENJIUWEN_RUNTIME_LOG_FILE=disabled`(新 sentinel)从 dictConfig 三处移除 file handler;deploy 模板固定注入。

**诊断**:`metrics.py`(MetricsRegistry:per-endpoint 计数/错误码分布/p50/p95/max + recent_errors 环形 200 条)+ `debug_api.py` 7 个只读端点(/debug/overview|session|scope|scopes|config|stats|recent_errors,裸 FastAPI 同 /healthz 模式),统一 `redact()` 脱敏(敏感 key→`***`、URL 剥 userinfo、嵌套 JSON 字符串深入——pod_spec_json 里的 kubeconfig 也要遮)。

## 实现

- 新文件:`logsetup.py` / `metrics.py` / `debug_api.py`;新只读访问器:`SessionState.session_hash/session_expiry_score/scope_session_count/scope_config_raw`、`ResourceState.health_fails`、`ConfigStore.list_templates`;`OrchestratorSystemContext.jobs_snapshot()`(leader 身份解析 `agent_runtime:job:{name}` token,tick 间隙锁缺失容错 null)。
- 框架(foundation+service):log/config.py sentinel+`_drop_file_handler`(必须三处同删否则 dictConfig ValueError);runner.py 计数器/条件 tick-done/心跳;lock.py+single_leader.py 降级;router.py FrameworkError 日志。
- 踩点:FastAPI `get_type_hints` 陷阱在 debug_api 复现风险——`Request` 顶层 import、query 参数不进签名、`_debug_endpoint` 包装器**不用 functools.wraps**(会把内层 `(request, sysctx)` 注解暴露给 FastAPI)。

## 验证

- pytest:**129 通过**(存量 114 零改动 + 新增 15:test_debug_api 7 / test_metrics 3 / test_logsetup 5)。
- 本地实跑(INF O 级):INFO 下 `tick lock acquired`/`single_leader claimed` **0 条**(原 ~6 行/秒);DEBUG 级旋钮生效(21 条锁日志、`lua eval: script=LUA_TOUCH duration_ms=0.6` 明细出现)。
- 请求汇总行实测:`request: endpoint=route outcome=ok error_code=- duration_ms=11.0 | request_id=req-r1 session_id=sess1 endpoint=route instance_id=ecs-38b3-0002:1a893294`;业务行(`route: session=…`/`acquire done: outcome=deployed`)同带尾巴。
- /debug 端点实测:overview(readiness 6 键 + 5 job 计数 ticks=38)、session(ttl 38.9s)、scope(rm pods=1/sm sessions=1)、config(**`/secret/kube/path` 出现 0 次**——脱敏生效,含 pod_spec_json 嵌套)、stats(5 请求 4 ok 1 error,VALIDATION 分桶)、recent_errors(route/VALIDATION/req-bad)。
- stdout-only:`OPENJIUWEN_RUNTIME_LOG_FILE=disabled` 下进程未写任何日志文件(文件 handler 已移除,console 全量保留)。

## 影响面

- spec 同步:service-core.md(可观测性整节 + cli.py/create_app 重写)、session-manager.md / resource-manager.md(eval 留痕/诊断访问器/sweeper 汇总/k8s 日志)。
- 配置:`AGENT_RUNTIME_LOG_LEVEL` 从死配置变为生效;新 env `OPENJIUWEN_RUNTIME_LOG_FILE=disabled`(deploy 模板固定注入,勿改回);无新增依赖、无新增 AGENT_RUNTIME_* 旋钮(慢 eval 阈值/心跳间隔为模块常量)。
- 开放问题:/debug 在 LB 后是 per-instance(响应带 instance_id 标识,看指定副本直连 Pod IP);uvicorn access log 保留(与汇总行经 request_id 关联)。
