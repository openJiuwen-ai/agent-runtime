# Manager 前后端集成审计（2026-11-03）

## 结论先行

本特性分支的 Manager 页面不是脱离后端的静态样机。相对 `5c5b09d4`（本地 develop 基线），前端保留并继续调用原有 Manager/Identity API；基于生成的 OpenAPI 和前端 TypeScript 调用静态审计，共发现 163 处 HTTP 调用，方法、路径参数、查询字段和请求体字段全部能匹配当前后端路由/Schema。逐条清单见 [API 契约清单](manager-api-contract-inventory-2026-11-03.csv)。核对范围是方法、路径及已声明字段名称；不等于所有请求值、可选字段省略语义、外部 Gateway/Runtime 或用户面都已验证。登录/刷新与导入导出的直接 fetch 分支另做人工路径核对，未计入此 163 条。

## 本次 30006 入口 404 的原因与恢复

用户确认出错入口为 `http://1.92.102.16:30006/manager`，对应 `zyq/jiuwenswarm-manager-web-nodeport`。本次根因是源码挂载覆盖了镜像前端，但宿主机没有构建产物：

- Deployment 将 `/root/zyq/agent-runtime` 挂载到 `/app/agent-runtime`，同时挂载仓库中的 nginx 配置模板。
- nginx 静态根目录为 `/app/agent-runtime/applications/manager/manager_web/dist`；服务器只有源码，没有 `dist` 和 `node_modules`。
- 2026-11-03 19:55 和 20:20（北京时间）请求日志明确出现 `open() "/app/agent-runtime/applications/manager/manager_web/dist/index.html" failed (2: No such file or directory)`。根路径、`/manager`、`/index.html` 都返回 nginx 404。
- 服务器源码 HEAD 与本地均为 `92f12810`。已在该目录执行 `npm ci --no-audit --no-fund && npm run build`；构建产物可被现有挂载立即读取，随后浏览器登录和管理总览加载成功。
- 本地部署工具已补充构建检查：源码挂载前检查 `index.html` 和其中 JS/CSS 引用；缺失则给出构建命令并中止。新增 `/index.html` readinessProbe，防止仅 nginx 进程存活就显示 Ready。此防护代码尚未同步到服务器。

初始排查还发现历史 `30014` 当前为 `wx2` 的 Manager REST API 端口，其 `/manager` 返回 Uvicorn 404；这是另一个入口的事实，不是本次 `30006` 的原因。以后仍应使用部署输出的 `MANAGER_WEB_NODE_PORT`。

## 分支差异范围

- 当前特性分支：`codex/feature-manager-redesign`，已提交 HEAD `92f12810`。
- 本地 develop 与共同基线为 `5c5b09d4`。`git diff develop...HEAD -- applications/manager`：148 个文件，新增 9,555 行、删除 3,721 行（包含文档、截图、开发脚本、测试）。
- `identity_center` 相对共同基线未修改；Manager 后端主要增加受限制的本地离线模式、开发状态接口、服务器 `agentserver.json` 发现接口。主要重构集中于前端路由、页面组合、上下文返回、布局和文案。
- 已读取最新 `upstream/develop=2dcf7ec4`。与它直接比较为 163 个文件；其中包括尚未合入特性分支的上游能力，不能把直接差异全部解释成重设计删除。具体缺失能力见下方“最新 develop 漂移”。
- 本轮尚未提交的页头调整、字段保留修复和部署防护不包含在上述 HEAD 统计内。

## 路由与真实后端

| 前端页面/能力 | 前端调用 | 后端入口/服务 | 持久化或下游 |
|---|---|---|---|
| 登录、用户、组织 | `/idp/v1/auth/token`、`/idp/v1/auth/me`、`/idp/v1/users/`、`/idp/v1/orgs/` | identity_center auth/IAM routers | Identity DB |
| 角色、权限 | `/api/v1/authz/permissions`、`/roles`、`/roles/{id}/users` | `authz_routers.py` / authz service | Manager DB |
| 审批 | `/api/v1/approvals`、`/{order}/actions` | `approval_routers.py` | Manager DB/审批逻辑 |
| Agent 模板 | `/api/v1/agent-templates/`、`/{id}` | `template_routers.py` / `AgentTemplateService` | Manager DB；更新向引用 Gateway 推送 |
| 模型、向量模型、技能、MCP、工具安全、扩展配置、A2A | 各自 `/api/v1/*-templates`、A2A discovery/outbound/policy | template/A2A routers | Manager DB；可用模板更新按类型推送 Gateway |
| 容器模板、运行规格 | `/api/v1/container-templates`、`/service-config-templates` | template routers | Manager DB；运行规格绑定会同步 Runtime |
| 集群列表/新建/删除/探活 | `/api/v1/instances/`、`/{id}`、`/health-probe` | `instance_routers.py` / `InstanceService` | Manager DB；创建和探活访问 Gateway/Runtime/User Web |
| 集群准入 | `/instances/{id}/users`、`/orgs`、`user-gateways`、`org-gateways` | `instance_access_routers.py` / `InstanceGrantService` | Manager DB；用户面选路和上下文查询实时读取准入规则 |
| 运行资源 | `/instances/{id}/service-resources` | `instance_resource_routers.py` / `InstanceServiceResourceService` | Manager DB；`runtime_config_sync.py` 调 Runtime `/api/session/config_sync` |
| Agent 绑定 | `/instances/{id}/agent-resources` | 同上 / `InstanceAgentResourceService` | Manager DB；先向 Gateway 下发再写库；可按 match expr 自动补准入 |
| 日志脱敏 | `/instances/{id}/log-masking-rules` | `application_config_routers.py` / masking service | Manager DB，并向 Gateway 下发 |
| 日志级别、定时任务配额、高级审计日志 | `/logging`、`/cron-policy`、`/audit-log` | `application_config_routers.py` | Manager DB，并向 Gateway 下发；未保存时 GET 404 是“无配置”而非页面不存在 |
| 工作空间/团队配额 | `/workspace-quota/*`、`/team-quota/*` | quota routers/services | Manager DB；Gateway 读写/用量同步；Workspace 受开关和权限控制 |
| 专家与专家包 | 集群真实能力：`/package-allowlists`、`/catalog` | package allowlist service | Manager DB + Gateway；目录由目标集群 Hub 返回 |
| 用户面验证 | GET `user-console/agent-contexts`，POST `active-cluster` | user console service | 重新按当前身份、集群准入、Agent 绑定计算可用上下文，然后进入 `/chat/` |
| 导入导出 | `/api/v1/import-export/{resource_type}/*` | import/export routers | 真实预检、写库/下载文件 |

## 页面真实性边界

### 已接真实接口

配置工作台聚合当前实例详情、用户/组织准入、有效运行资源、有效 Agent 绑定和组件状态。集群页的准入、运行资源、Agent 绑定、日志、定时任务、配额和连接检查均保留原 CRUD 或配置下发调用。集群创建向导实际调用 `POST /api/v1/instances/`，自动地址只是根据命名空间拼出的 Kubernetes Service URL；跨网络场景需要切换手动地址。

Agent 模板页面统一为 `/manager/agent-templates`，旧 `/agents` URL 仅做兼容重定向。模板引用的模型、技能、MCP、安全策略、扩展和 A2A 会通过各自目录 API 读取；集群绑定仍是 `/instances/{id}/agent-resources` 的真实资源授权。

### 明确不是后端功能的部分

1. `AgentExpertBindingsPreview` 和 `AgentTemplateModal` 的专家选择目前只写入浏览器 `localStorage`，没有进入 `template_ref`、模板 `data` 或任何下发请求。这是等待 Agent 模板专家绑定后端能力的明确预览，不能宣称已经部署专家资源。现有集群级专家包允许名单仍是真实功能。
2. Token 配额、成本统计、业务审计事件查询在页面明确标为“待开放”，没有伪造读写结果。
3. 授权管理页是关系说明和导航汇总，实际用户/组织/角色及集群准入操作在对应管理页完成，没有独立的虚构授权关系保存接口。
4. 本地离线模式是显式开发开关（仅 loopback + SQLite + `MANAGER_LOCAL_DEV`），它模拟 Gateway/Runtime 请求回执以验证 Manager 配置流程；不代表真实部署、容器运行或对话成功。

## 需要修复或关注的契约问题

### 1. 资源更新请求是“整份覆盖”

后端 `UpdateInstanceAgentResourceBody` 和 `UpdateInstanceServiceResourceBody` 的 `enabled`、`expires_at`、`data` 有默认值，路由会把整份请求写回数据库。前端编辑和启停之前省略了其中字段，因此可能出现：编辑禁用项被重新启用、编辑清空 `data`、启停清除过期时间（临时授权变成永久）。本轮已增加统一更新请求构造函数，编辑保留启用状态和 data，启停保留到期时间、原始范围和 data，同时允许显式清空到期时间。新增 6 项回归测试实际经过前端 API 序列化，全部通过；后端全覆盖语义保持不变。

### 2. Agent 模板删除的下游同步需继续验证

`AgentTemplateService.delete` 会调用 `InstanceAgentResourceService.delete_by_template` 后删除 Manager DB 行；该批量方法直接删除授权行，与单条 `remove_resource` 的 Gateway 卸载路径不同。删除模板前应确认 Gateway 是否有全量同步/删除机制，避免 Manager 已无绑定而 Gateway 仍保留旧授权。

### 3. agentserver.json 自动发现缺少标准部署接入

前端调用 `/api/v1/service-config-templates/agentserver-discovery` 是真实 API，但后端只读取 Manager 容器本地的 `AGENTSERVER_JSON_PATH`，或 `MANAGER_DEPLOY_TOOL_DIR/conf/agentserver.json`。部署工具生成的文件位于部署机配置目录，默认既没有挂载到 Manager Pod，也没有注入这两个环境变量，所以标准部署通常得到 `found:false`。应在部署渲染时通过受控 ConfigMap/只读挂载（或已有持久卷）向 Manager 提供生成文件，并注入明确路径；不得让浏览器直接读取服务器文件系统。

### 4. 绑定说明须与自动准入逻辑一致

`AgentConnectPage` 曾说明保存绑定不会自动授予集群准入，但服务端 `auto_bind_from_match_expr` 会为明确 user_id/group_id 等式或 in 元组中的身份补充缺失的允许规则，已有规则不会覆盖。页面说明已同步为实际行为；后端规则不变。

### 5. 配置进度不是运行成功证明

`deriveClusterSetupProgress` 只统计启用且未过期的准入/运行资源/Agent 绑定是否存在；它不判断授权范围是否有交集，也不证明 Gateway/Runtime 下发成功或用户面真的能对话。工作台的“检查连接”和“前往用户面验证”仍是必要的第二层验证。

### 6. 集群卡片指标

原卡片的 Agent/准入/运行池三个值固定为 `—`，没有后端统计。本轮已替换为列表 API 自带的 Gateway、Runtime、User Web 实际状态，不增加额外请求。

### 7. 反向引用查询的扩展性

`loadAgentBindings` 会遍历全部集群并逐个拉取完整绑定列表，`loadAgentTemplateCatalog` 的资源引用筛选也在分页汇总后由客户端过滤。当前实现读取真实数据且不会只查第一页，但集群/模板数量增加后请求数和延迟会增长。后续宜添加按模板或资源引用反查的后端聚合接口。

## 最新 develop 漂移

当前本地 `develop=5c5b09d4`，特性分支 HEAD=`92f12810`。当前 `upstream/develop=2dcf7ec4` 还新增了 jiuwenbox 挂载支持，以及企业版 Skill 上传权限申请/审批（32 个文件）。这部分不在本特性分支，不能把它误判为本次 Manager 重设计删除；如要交付企业最新功能，需要单独合并并重新审计路由、权限码、环境变量和审批页面。

## 现场联通观察

在已登录的 `:30006` 页面，集群 `3dc7817c-c788-4d73-8802-767296591468` 的工作台展示了真实接口结果：准入 0 个用户/0 个组织、有效运行规格 1 个、有效 Agent 绑定 1 个；Gateway 和 Runtime 为 ONLINE，User Web 为 OFFLINE，当前账号无可用 Agent 上下文。因而 Manager 页面读取已通，但当前配置并不满足用户面对话验证条件。本次审计没有修改服务器准入、模板或集群业务数据。

## 验证范围

已完成：前端 TypeScript 调用与当前后端 OpenAPI 的 163 条方法/路径/字段匹配；本轮 `npm run build`（tsc + Vite）通过，完整前端测试 102/102 通过（含新增字段保留测试 6 项），部署构建校验测试 9/9 通过，`git diff --check` 通过；服务器上对 30006、30014、30201 的 HTTP 入口及 zyq Web Pod 的 dist/nginx/访问日志进行核对。

尚未由本审计证明：真实 Gateway/Runtime 全链路配置下发、实际 Agent 容器创建、用户面最终对话；这些需要使用可达的实际服务地址，在 Manager Web 端口登录后完成一次端到端操作。

## 本轮集群页头布局验收

六个集群页面共用 `ClusterWorkspaceNavigation`。五个子页的“返回配置工作台”和“← 集群管理”位于集群名称同一行右侧，统一普通按钮样式；配置工作台保留刷新与返回集群列表。底部重复返回栏已移除。窄屏下按钮组自然换至名称及 ID 下方，并保持右对齐。

本地浏览器逐一检查六个页面及两个返回方向；在 1429px 视口中，两返回按钮与名称垂直中心差小于 1px，无横向溢出。该页头调整目前只在本地源码中，未同步服务器。
