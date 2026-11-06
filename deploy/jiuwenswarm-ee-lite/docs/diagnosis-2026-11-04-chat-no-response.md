# 诊断报告：用户面发消息无响应（2026-11-04）

> 状态：**问题 1（主因）/4a（project.lifecycle）/4b（config.get）已于 2026-11-05 修复**（见文末"修复实施记录"）；问题 2/3 待观察与配置。
> 现场日志：`data/logs/gateway.log`、`data/agentserver_pods/pods/agentserver-evt4akkx/agent.log`
> 及其 `workspace/run/worker-logs/runtime-worker-*.stderr.log`（15:36 的"你好"请求）。

## 一、轻量版聊天链路还原（与最初假设的差异）

最初怀疑 supervisor 的 `dropped worker wire; no Gateway connection` 是主因——**不是**。
实际链路里 worker 对 HTTP 聊天是进程内直连处理的，supervisor 根本不在聊天回包路径上：

```
用户前端 → gateway(GATEWAY_AGENT_CLIENT_MODE=runtime)
  → POST runtime:8091 /api/session/route        （选 pod，200 OK）
  → POST pod:55977 /api/v1/chat/completions (SSE)   ← gateway 的 pod_http_client
     └─ pod 里 55977 是 worker 进程内的 FastAPI
        （subprocess_client 注入 AGENT_HTTP_ENABLED=true，
          worker/lifecycle.py 里 start_http_if_enabled 启动，共享 worker 进程内工具箱）
     └─ http_chat → handlers/_default.handle_stream → execution_runtime().stream()
        （全在 worker 进程内，不经 front/supervisor）
  → SSE 帧回流 gateway → 前端
```

`supervisor dropped worker wire` 丢的是**旁路 server-push**：worker 的
`send_push 已发送(E2A wire)`（chat.file 等）走 front WS 通道（`_wire_route`
回退到 `_gateway_ws`），而轻量版 gateway 只走 HTTP SSE，没人调
`attach_gateway_connection`，`_gateway_ws` 恒为 None → drop。
（15:28:01 / 15:28:27 两次是启动期推送，15:36:02 一次是该聊天触发的 push。）

## 二、"你好"无响应的根因（证据链）

时间线（15:36:02，request_id=req_mw1syrfa_376）：

| 时刻 | 证据 | 出处 |
|---|---|---|
| .164 | gateway → `POST http://127.0.0.1:55977/api/v1/chat/completions` **200 OK** | gateway.log:4757 |
| .224 | worker 开始处理：`[JiuWenSwarm] 处理流式请求 … sdk=harness` | worker stderr:4955 |
| .285 | **模型 `GLM-5.3-Flash` 不在配置 → 回退 default model 'NoneType'**；legacy gpt-4 缺 api_key 被跳过 | worker stderr:4962-4963 |
| .306 | runtime 仅产出 1 个事件 = `runtime.error`；`http_sink.deliver_runtime_event` 的 runtime.error 特判分支把它编码成 `AgentResponse(ok=False)`：`{request_id, channel_id, ok, payload, metadata}` | http_sink.py:177-194 |
| .306 | gateway `parse_agent_server_wire_chunk`（openjiuwen_protocol/e2a/wire_codec.py:245）只认 E2A envelope / deprecated chunk 形状 → **ValueError: unrecognized wire shape** | gateway.log:4764 |
| .313 | gateway 误报 `[MessageHandler] Stream 正常完成: total_chunks=0` → 前端一片空白 | gateway.log:4771 |

即：**模型配置错误本来该给用户报一句错，但错误帧在 SSE 协议层被吞了**。

## 三、问题清单与修复方案（均未实施）

### 1. 主因（协议）：SSE 流上的 runtime.error 帧形状不被 gateway 解析

`http_sink.deliver_runtime_event`（jiuwenswarm/server/transports/http_sink.py）：
streaming 分支对**其它**失败本来就会转 `chat.error` chunk（`is_complete` 语义齐全），
唯独 `runtime.error` 被前面的特判截住走了 `send_response(AgentResponse)`——
这个形状是给 WS 线协议用的，HTTP SSE 客户端不认。

- **方案 a（推荐，一处改动）**：streaming 模式下 runtime.error 也走 chunk 路由
  （`payload.event_type=chat.error` + `is_complete=True`），与非 runtime.error
  失败的编码统一；unary 模式保留 AgentResponse 不动。
- **方案 b（兼容加固，可选叠加）**：`pod_http_client.send_request_stream`
  （runtime_management_extension/pod_http_client.py:478）兼容 AgentResponse
  形状帧：ok=False → 转错误 chunk 上抛；ok=True → 视为终帧。双保险。

### 2. 旁路：server-push 经 front WS 转发被 drop

`_wire_route`（server/supervisor/runtime_supervisor.py:832）在轻量版必然走
`_gateway_ws=None` → drop。影响 chat.file 等推送类事件，不挡文本回复。

- 方向：轻量形态下容忍该 drop（降为 DEBUG）或在 pod_http_client 增加推送
  拉取/回流通道；改动涉及架构取舍，**建议先观察**文本回复修好后的实际影响面。

### 3. 配置：会话请求模型 GLM-5.3-Flash 不存在

agent 的模型列表里没有该模型（且遗留 gpt-4 条目缺 api_key）。
管理面给 agent 配置可用模型，或前端不传这个名字。零代码。

### 4. 顺带发现（独立小问题，不挡聊天主链路）

- gateway `config.get` 报 `module 'gateway.common.config_panel.config_set_handlers'
  has no attribute 'config_get_handler'`——前端配置面板拉取失败，疑似
  jiuwenswarm 克隆（develop"临时修改"版）与 gateway 代码版本不齐。
- `project.lifecycle 转发失败: REST 路径缺占位符 ['project_id']`——e2a_proxy
  拼 REST 路径时没做占位符替换，前端每 2 秒轮询刷一次 WARNING。

## 四、验证步骤（修复后）

1. 重启部署：`uv run jiuwenswarm-lite start`（后端即可，不用前端）。
2. 用户面发"你好"：
   - 若模型仍缺配置，页面应**显示模型错误提示**（而不是无响应）——验证问题 1。
   - 管理面配好模型后重发，应收到正常流式回复。
3. gateway.log 不再出现 `SSE 帧无法按 E2A chunk 解析`。

## 五、修复实施记录（2026-11-05，resources/jiuwenswarm 克隆内 4 个文件，未提交）

| # | 文件 | 改动 | 对应问题 |
|---|---|---|---|
| 1 | `jiuwenswarm/server/transports/http_sink.py` | `deliver_runtime_event`：流式下 `runtime.error` 不再走 `AgentResponse` 特判，并入 `chat.error` chunk（错误文案透传 + `_annotate_model_error`），且错误事件强制 `is_complete=True` 收流；unary 行为不变 | 1（方案 a） |
| 2 | `jiuwenswarm/extensions/runtime_management_extension/pod_http_client.py` | `send_request_stream` 帧循环：识别 AgentResponse 形状帧（有 `ok` 无 `is_complete`），转终态 chunk 外发，不再整帧丢弃（兜住 `invoke_stream` 异常回包等同类帧） | 1（方案 b） |
| 3 | `jiuwenswarm/extensions/runtime_management_extension/pod_rest_map.py` | ① `assemble_rest_request`：`project.lifecycle` 带 `events`/`inventory` 时改走 scope-wide `POST /lifecycle`（与 WS dispatch 分支条件一致）；② `declared_http_routes`：AgentServer 额外注册 `POST /api/v1/lifecycle`（同一 handler 按 params 分支）；按项目变体与缺 id 报错契约不变 | 4a |
| 4 | `gateway/common/config_panel/config_set_handlers.py` | 末尾追加缺失符号兜底导入：`config_get_handler`/`config_set_handler`/`config_save_all_handler` 从母仓模块（`jiuwenswarm/common/config_panel/config_set_handlers.py`）导入（本地已定义优先；独立打包无 jiuwenswarm 时 ImportError 静默维持原状）。副本落后母仓 36 个符号，config.set/save_all 本同样会挂，一并补齐 | 4b |

验证：冒烟脚本确认 events/inventory → `/lifecycle`、按项目变体路径不变、缺 id 仍报
`RestAssemblyError`、服务端两条 lifecycle 路由都注册、四个模块可导入、gateway 副本
三个 handler 可达。单测：`tests/unit_tests/extensions/` + `test_e2a_proxy.py`
417 通过 / 3 失败（adx 相关，stash 对照确认干净 develop 同样失败，存量环境问题）。

**重启部署后生效**（jiuwenswarm 以 editable 源码方式被各服务 import）。
修复后发"你好"：若模型仍未配置，页面会**显示模型错误**（而不是无响应）；
要收到真实回复，需在管理面给 agent 配置可用模型（GLM-5.3-Flash 或前端改选
已配置模型）——问题 3 属配置项。

## 六、追加（2026-11-05 下午）："No valid model entries found" —— 模型配置没到 agentserver

协议修复生效后前端能显示错误了，暴露出下一层问题：pod 里模型缓存为空
（worker 日志：`requested model 'GLM-5.3-Flash' not found in configured models`
+ `跳过无效模型条目(legacy) gpt-4`）。

### 根因

agentserver 走的**不是**企业版分支。模型配置的读取有两套：

| 分支 | 判定 | 模型来源 |
|---|---|---|
| YAML（个人版） | `JIUWENSWARM_EDITION != enterprise` | pod 自己的 `workspace/config/config.yaml`（`models.defaults` 全是 `${API_BASE}`/`${API_KEY}`/`${MODEL_NAME}` 占位符，pod env 里没有这些变量 → 全部校验失败） |
| enterprise | `JIUWENSWARM_EDITION=enterprise` | `resolve_config_source()` → `load_effective_enterprise_config(request)`：按 `request.bot_id`（=instance_agent_resource.resource_id）→ `agent_template` → `model_template` **读 gateway 的 DB**（GDB） |

gateway 侧一直是企业版（`spec.py` 给 gateway 进程设了 `JIUWENSWARM_EDITION=enterprise`
+ `GATEWAY_SQLITE_PATH`），所以管理面下发的配置在 gateway 生效、前端能看到模型名；
但 agent_runtime 拉起 pod 的容器模板（`lite-agent`）`env` 为空——pod 既没有
`JIUWENSWARM_EDITION` 也没有 DB 信息，只能走 YAML 分支读占位符 →
`No valid model entries found in config`。

agent_runtime 的 pod env 合成是「引导白名单 < 模板 env < 进程覆盖」：白名单只有
PATH/SYSTEMROOT 等系统变量，模板 env（pod_spec containers[].env）才是业务 env 的
正道——与 K8s 模式同构，管理面容器模板 API 下发的 env 就是走这里。

### 修复（配置下发，零代码；服务停止状态下直写两库，等效管理面推送终态）

给容器模板 `lite-agent` 补 5 个 env（管理面 UI 的容器模板编辑处同样可配）：

```
JIUWENSWARM_EDITION=enterprise          # is_enterprise 闸门 → 走 enterprise 分支
JIUWENSWARM_CONFIG_SOURCE=enterprise    # config source 显式钉死（缺省亦为 enterprise）
GATEWAY_DB_TYPE=sqlite                  # 与 gateway 同一套 DB 类型
GATEWAY_SQLITE_PATH=E:/code/cyz95/temp/agent-runtime/applications/jiuwenswarm-ee-lite/data/gateway.db
                                        # ← 指向 gateway.db 绝对路径（pod cwd 在独立
                                        #   workspace，相对路径会落到 pod 私有库）
GATEWAY_EPHEMERAL_BACKEND=memory        # 单机无 Redis；企业版 ephemeral 显式降级内存
```

- `manager.db` `service_config_container.lite-agent.env`：wire 形态
  `[{"name":..,"value":..},..]`（管理面源，下次 config_sync 重推一致）。
- `agent_runtime.db` `service_config_container.lite-agent.env`：canonical 形态
  `{"NAME":"value",..}`（RM 配置水合直接读；`_build_env` 以模板 env 注入 pod 进程）。

数据链已核验：gateway.db `instance_agent_resource`(732c3416…) → `agent_template`
(chat-agent-tpl-bcb8934b, default_model=[8227c3d5…]) → `model_template`
GLM-5.3-Flash（api_base/api_key 齐全，enabled=1）。

### 验证（重启后）

1. `uv run jiuwenswarm-lite start`
2. 用户面发"你好"：应收到真实回复（模型端点 `http://113.46.219.251:8080/` 可达时）。
3. worker stderr 日志应出现 `[JiuWenSwarmDeepAdapter] using enterprise model config`；
   不应再出现 `No valid model entries found` / `requested model … not found`。
   若出现 `[enterprise_config] bot_id missing` 或 `instance_agent_resource not found`，
   说明请求元数据没带 bot_id（gateway 准入绑定问题，另查）。
