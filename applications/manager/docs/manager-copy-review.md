# Manager 中文文案修改清单

本清单按当前本地源码整理。直接填写「修改后」列即可；留空表示暂不修改。本文整理配置引导、页面主标题/副标题和首页引导；文末附完整文案索引，覆盖弹窗、字段提示、空状态等补充内容。用户自定义的 Agent 名称、实例名称、描述和服务端错误消息无法预列。当前只整理，不修改界面。

## 1. 配置引导

### 通用文案

| 编号 | 当前文案 | 修改后 |
| --- | --- | --- |
| G01 | 配置引导 |  |
| G02 | Agent 配置引导 · {当前步骤}/4 · {阶段名称} |  |
| G03 | 准备资源 |  |
| G04 | 定义 Agent |  |
| G05 | 运行环境 |  |
| G06 | 访问授权 |  |
| G07 | 当前页面 |  |
| G08 | 展开更多资源配置 |  |
| G09 | 收起更多资源配置 |  |
| G10 | 按本次 Agent 的用途选择资源；此引导不会自动判定配置完成。 |  |
| G11 | 继续：定义 Agent |  |
| G12 | 继续：运行环境 |  |
| G13 | 继续：访问授权 |  |

### 第 1 步：准备资源

前四项默认展示，后三项在「展开更多资源配置」内。页面副标题与以下提示共用文案，修改时一起生效。

| 编号 | 引导卡片标题 | 当前提示 | 页面路径 | 新标题 | 新提示 |
| --- | --- | --- | --- | --- | --- |
| R1 | 模型 · 必需 | 新建或检查要用于本次 Agent 的模型。 | /manager/model-templates |  | 新建或检查要用于 Agent 的模型配置。 |
| R2 | 技能 · 可选 | 按业务用途添加预置技能。 | /manager/skill-prebuilt-templates |  | 按业务用途添加预置技能源。 |
| R3 | MCP · 可选 | 需要外部系统能力时配置 MCP。 | /manager/mcp-templates |  | 按业务用途配置外部 MCP 服务。 |
| R4 | 工具安全 · 可选 | 根据工具风险配置限制，随后在 Agent 中引用。 | /manager/safety-guardrails |  | 根据工具执行风险配置对应安全护栏限制。 |
| R5 | 向量模型 · 可选 | 需要知识库或向量检索时配置 Embedding 模型。 | /manager/embedding-templates |  |  |
| R6 | A2A · 可选 | 需要 Agent 间协作时配置 A2A 能力。 | /manager/a2a-management |  |  |
| R7 | 扩展配置 · 可选 | 按需添加高级运行或集成扩展。 | /manager/extension-config-templates |  |  |

### 第 2—4 步的卡片

| 编号 | 阶段 | 当前标题 | 当前提示 | 跳转页面 | 新标题 | 新提示 |
| --- | --- | --- | --- | --- | --- | --- |
| G-C1 | 定义 Agent | 创建或编辑 Agent | 明确用途，选择本次使用的默认模型、技能与安全策略。 | /manager/agents | 创建或编辑 Agent 定义 | 选择本次创建 Agent 可使用的模型、技能与安全策略等。 |
| G-C2 | 运行环境 | 接入服务实例 | 检查 Gateway、Runtime 和 User Web 的连接状态。 | /manager/instances |  |  |
| G-C3 | 运行环境 | 准备容器模板 | 按部署方式检查容器配置。 | /manager/container-templates |  |  |
| G-C4 | 运行环境 | 配置运行规格 | 配置运行资源，并在目标实例中检查运行规格绑定。 | /manager/service-config-templates |  |  |
| G-C5 | 访问授权 | 新建成员 | 创建可登录用户，之后才能登录用户面进行对话。 | /manager/users |  |  |
| G-C6 | 访问授权 | 新建组织并绑定成员 | 组织用于集群准入和授权范围。 | /manager/orgs |  |  |
| G-C7 | 访问授权 | 绑定 Agent | 选择 Agent → 配置使用范围，选择目标实例和授权条件。 | /manager/agents |  |  |
| G-C8 | 访问授权 | 检查准入与运行条件 | 进入目标实例核对用户准入、运行规格和组件状态；保存绑定不代表执行成功。 | /manager/instances |  |  |

## 2. 左侧导航与页面主标题/副标题

左侧导航分组：工作台、资源中心、运行环境、安全与访问、运营（按权限及功能开关显示）。

| 编号 | 分组 | 左侧名称 | 路径（/manager 后） | 当前页面标题 | 当前副标题 | 新导航名 | 新标题 | 新副标题 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P01 | 工作台 | 总览 | /overview | 管理总览 | 从服务连接到 Agent 使用，掌握配置与运行环境。 |  |  |  |
| P02 | 工作台 | Agent 管理 | /agents | Agent 管理 | 管理 Agent 定义、能力配置与实例使用范围。 | Agent 定义管理 | Agent 定义管理 |  |
| P03 | 资源中心 | 模型 | /model-templates | 模型 | 新建或检查要用于本次 Agent 的模型。 |  |  | 管理创建 Agent 时可使用的模型配置 |
| P04 | 资源中心 | 预置技能源 | /skill-prebuilt-templates | 预置技能源 | 按业务用途添加预置技能。 |  |  | 管理创建 Agent 时可添加的预置技能源 |
| P05 | 资源中心 | MCP服务 | /mcp-templates | MCP服务 | 需要外部系统能力时配置 MCP。 |  |  | 管理创建 Agent 时可配置的 MCP 服务 |
| P06 | 资源中心 | 工具安全策略 | /safety-guardrails | 工具安全策略 | 根据工具风险配置限制，随后在 Agent 中引用。 |  |  | 管理创建 Agent 时可配置的工具安全护栏 |
| P07 | 资源中心 | 向量模型 | /embedding-templates | 向量模型 | 需要知识库或向量检索时配置 Embedding 模型。 |  |  | 管理可用于知识库或向量检索的 Embedding 模型配置 |
| P08 | 资源中心 | A2A管理 | /a2a-management | A2A管理 | 需要 Agent 间协作时配置 A2A 能力 |  |  | 管理用于 Agent 间协作的 A2A 能力配置 |
| P09 | 资源中心 | 扩展配置 | /extension-config-templates | 扩展配置 | 按需添加高级运行或集成扩展。 |  |  | 管理添加高级运行或集成扩展配置 |
| P10 | 运行环境 | 服务实例 | /instances | 服务实例 | 管理 {{productName}} 集群 |  |  | 管理接入已部署好的 JiuwenSwarm 运行实例（包括 Gateway、Runtime、User Web等） |
| P11 | 运行环境 | 运行规格 | /service-config-templates | 运行规格 | 维护实例池参数和容器运行模板 |  |  | 由容器模板组装成的可调度、可扩缩的 Agent 运行池方案（Runtime Profile） |
| P12 | 运行环境 | 容器模板 | /container-templates | 容器模板 | 维护 AgentServer 与 Sandbox 容器的可复用运行规格，供运行时模板绑定 |  |  | 管理 Agent 运行时所使用的“软件镜像和容器包装方式” |
| P13 | 访问授权 | 用户 | /users | 用户管理 | 管理用户与角色 | 访问授权 |  | 管理用户 |
| P14 | 访问授权 | 组织 | /orgs | 组织管理 | 管理组织与成员归属 | 访问授权 |  |  |
| P15 | 访问授权 | 角色与权限 | /roles | 角色管理 | 配置角色、权限和授权 | 访问授权 |  |  |
| P16 | 运营 | 审批中心 | /approvals | 审批中心 | 查看和处理平台各类审批申请 |  |  |  |

### 其他入口与动态标题

| 页面/位置 | 当前标题 | 提示/说明 | 新标题 | 新提示 |
| --- | --- | --- | --- | --- |
| Agent 模板目录 /agent-templates | Agent模板 | 维护可复用的Agent模板，供实例绑定使用 |  |  |
| Agent 详情 | {Agent 名称} | {Agent 描述}；无描述时：尚未填写描述 |  |  |
| Agent 使用范围 | 配置 Agent 使用范围 | {Agent 名称} |  |  |
| 实例详情 | {实例名称} | 显示实例 ID；各子页提示见下表 |  |  |

### 实例内部页面标题与提示

以下取自实例模块文案定义，部分页面按权限/功能开关呈现。动态组合标题及其他提示见完整索引。

| 文案键 | 当前标题 | 当前提示 | 新标题 | 新提示 |
| --- | --- | --- | --- | --- |
| instanceDetail.title | 集群详情 | — |  |  |
| instanceDetail.access.title | 集群准入 | 配置谁可以进入本集群 |  |  |
| instanceDetail.tokenQuota.title | Token配额 | 查看与管理集群 Token 配额 |  |  |
| instanceDetail.cost.title | 成本统计 | 查看集群成本与用量统计 |  |  |
| instanceDetail.audit.title | 业务审计 | 查看集群业务审计日志 |  |  |
| instanceDetail.resourcePanel.agent.title | Agent | 基于 Agent模板 接入并管理 Agent，配置各 Agent 的授权范围 |  |  |
| instanceDetail.resourcePanel.serviceResource.title | 运行时 | 基于 运行时模板 创建并管理实例池，配置各池的调度策略，实例在会话触发时按需分配 |  |  |
| instanceDetail.workspaceQuota.title | 用户空间配额 | 按匹配范围配置本集群的用户空间配额 |  |  |
| instanceDetail.workspaceQuota.usage.title | 用户空间用量统计 | 展示本集群已缓存的用户空间用量。数据来自用户查看工作空间页面后的用量快照，可能略旧于最新用量。 |  |  |
| instanceDetail.teamQuota.title | 团队配额 | 按匹配范围配置本集群每用户最多团队数、每组织最多团队数、每团队最多人数（含 Leader） |  |  |

## 3. 首页四步入口与其他新增页面文案

首页四步入口与下拉引导目前是分别定义的，需同时修改。

| 当前标题 | 当前提示 | 新标题 | 新提示 |
| --- | --- | --- | --- |
| 准备模型与工具 | 连接模型，配置技能和外部工具。 |  |  |
| 定义 Agent | 组合可复用能力和工具安全策略。 |  |  |
| 接入运行环境 | 接入服务实例，配置容器与运行规格。 |  |  |
| 配置使用范围 | 先配置成员/组织准入，再绑定 Agent、运行时及授权条件。 |  |  |

### OverviewPage.tsx

包含该页面写在代码中的标题、提示、状态和相关操作文字，便于一起审阅。

| 编号 | 当前文案 | 修改后 |
| --- | --- | --- |
| Overview-01 | 准备模型与工具 |  |
| Overview-02 | 连接模型，配置技能和外部工具。 |  |
| Overview-03 | 定义 Agent |  |
| Overview-04 | 组合可复用能力和工具安全策略。 |  |
| Overview-05 | 接入运行环境 |  |
| Overview-06 | 接入服务实例，配置容器与运行规格。 |  |
| Overview-07 | 配置使用范围 |  |
| Overview-08 | 先配置成员/组织准入，再绑定 Agent、运行时及授权条件。 |  |
| Overview-09 | 管理总览 |  |
| Overview-10 | 从服务连接到 Agent 使用，掌握配置与运行环境。 |  |
| Overview-11 | 刷新状态 |  |
| Overview-12 | 管理 API |  |
| Overview-13 | 连接失败 |  |
| Overview-14 | 连接正常 |  |
| Overview-15 | 状态未知 |  |
| Overview-16 | 仅表示管理服务可访问 |  |
| Overview-17 | 服务实例 |  |
| Overview-18 | 已接入管理的服务环境 |  |
| Overview-19 | 组件连接正常 |  |
| Overview-20 | 待检查实例 |  |
| Overview-21 | 存在离线或未知组件 |  |
| Overview-22 | 无法读取实例： |  |
| Overview-23 | 服务环境 |  |
| Overview-24 | 探活状态不代表 Agent 请求成功；进入实例进一步检查配置。 |  |
| Overview-25 | 管理实例 |  |
| Overview-26 | 正在读取服务状态… |  |
| Overview-27 | 还没有接入服务实例 |  |
| Overview-28 | 先准备能力资源，再接入环境并配置 Agent 使用范围。 |  |
| Overview-29 | 接入服务实例 |  |
| Overview-30 | 名称 |  |
| Overview-31 | 操作 |  |
| Overview-32 | 检查 |  |
| Overview-33 | 让 Agent 可用的四个步骤 |  |
| Overview-34 | 配置路径 |  |

### AgentWorkspacePage.tsx

包含该页面写在代码中的标题、提示、状态和相关操作文字，便于一起审阅。

| 编号 | 当前文案 | 修改后 |
| --- | --- | --- |
| AgentWorkspace-01 | Agent 管理 |  |
| AgentWorkspace-02 | 管理 Agent 定义、能力配置与实例使用范围。 |  |
| AgentWorkspace-03 | 模板目录与导入 |  |
| AgentWorkspace-04 | 新建 Agent |  |
| AgentWorkspace-05 | Agent 列表 |  |
| AgentWorkspace-06 | 搜索 Agent |  |
| AgentWorkspace-07 | 搜索名称、描述或标签 |  |
| AgentWorkspace-08 | 定义状态 |  |
| AgentWorkspace-09 | 全部状态 |  |
| AgentWorkspace-10 | 已启用 |  |
| AgentWorkspace-11 | 已停用 |  |
| AgentWorkspace-12 | 刷新 |  |
| AgentWorkspace-13 | 正在读取 Agent… |  |
| AgentWorkspace-14 | 没有匹配的 Agent |  |
| AgentWorkspace-15 | 创建第一个 Agent |  |
| AgentWorkspace-16 | 尝试其他关键词或清除筛选条件。 |  |
| AgentWorkspace-17 | 先选择模型与工具，再配置服务实例中的使用范围。 |  |
| AgentWorkspace-18 | 清除筛选 |  |
| AgentWorkspace-19 | 创建 Agent |  |
| AgentWorkspace-20 | Agent 名称 |  |
| AgentWorkspace-21 | 能力配置 |  |
| AgentWorkspace-22 | 更新时间 |  |
| AgentWorkspace-23 | 操作 |  |
| AgentWorkspace-24 | 尚未填写描述 |  |
| AgentWorkspace-25 | 查看详情 |  |
| AgentWorkspace-26 | 配置使用范围 |  |
| AgentWorkspace-27 | Agent 摘要 |  |
| AgentWorkspace-28 | 关闭 |  |
| AgentWorkspace-29 | 标签 |  |
| AgentWorkspace-30 | 启用状态不代表运行可用。打开完整详情查看资源名称、资源状态和实例绑定。 |  |
| AgentWorkspace-31 | 打开完整详情 |  |
| AgentWorkspace-32 | 编辑定义 |  |

### AgentDetailPage.tsx

包含该页面写在代码中的标题、提示、状态和相关操作文字，便于一起审阅。

| 编号 | 当前文案 | 修改后 |
| --- | --- | --- |
| AgentDetail-01 | 正在读取 Agent… |  |
| AgentDetail-02 | 重试 |  |
| AgentDetail-03 | Agent 管理 |  |
| AgentDetail-04 | 尚未填写描述 |  |
| AgentDetail-05 | 刷新 |  |
| AgentDetail-06 | 编辑定义 |  |
| AgentDetail-07 | 绑定服务实例 |  |
| AgentDetail-08 | 定义状态 |  |
| AgentDetail-09 | 已启用 |  |
| AgentDetail-10 | 已停用 |  |
| AgentDetail-11 | 标签 |  |
| AgentDetail-12 | 更新时间 |  |
| AgentDetail-13 | 技术标识 |  |
| AgentDetail-14 | 能力与安全策略 |  |
| AgentDetail-15 | 定义引用，不代表运行时已加载 |  |
| AgentDetail-16 | 正在解析资源… |  |
| AgentDetail-17 | 没有配置资源引用 |  |
| AgentDetail-18 | 资源读取失败： |  |
| AgentDetail-19 | 待检查 |  |
| AgentDetail-20 | 打开资源目录 |  |
| AgentDetail-21 | 实例绑定与使用范围 |  |
| AgentDetail-22 | 以下为已保存的绑定。实例准入和运行规格需要分别检查。 |  |
| AgentDetail-23 | 正在检查实例绑定… |  |
| AgentDetail-24 | 尚未绑定服务实例 |  |
| AgentDetail-25 | 管理绑定 |  |
| AgentDetail-26 | 绑定已启用 |  |
| AgentDetail-27 | 绑定已停用 |  |
| AgentDetail-28 | 已过期： |  |
| AgentDetail-29 | 有效期格式异常： |  |
| AgentDetail-30 | 有效期至： |  |
| AgentDetail-31 | 长期有效 |  |
| AgentDetail-32 | 查看授权条件 |  |
| AgentDetail-33 | 实例内已准入的全体用户 |  |

### AgentConnectPage.tsx

包含该页面写在代码中的标题、提示、状态和相关操作文字，便于一起审阅。

| 编号 | 当前文案 | 修改后 |
| --- | --- | --- |
| AgentConnect-01 | Agent 管理 |  |
| AgentConnect-02 | 配置 Agent 使用范围 |  |
| AgentConnect-03 | 无法暂存草稿，离开前请保存配置。 |  |
| AgentConnect-04 | 输入已暂存于当前标签页，返回此 Agent 可继续。尚未提交到服务器。 |  |
| AgentConnect-05 | 清除草稿 |  |
| AgentConnect-06 | 清除尚未保存的绑定配置？此操作会清空名称、实例和授权条件。 |  |
| AgentConnect-07 | 继续编辑 |  |
| AgentConnect-08 | 确认清除 |  |
| AgentConnect-09 | 选择环境 |  |
| AgentConnect-10 | 授权范围 |  |
| AgentConnect-11 | 检查并保存 |  |
| AgentConnect-12 | 绑定配置已保存 |  |
| AgentConnect-13 | 这不代表运行时已经生效。请继续检查实例准入、运行规格及组件状态。 |  |
| AgentConnect-14 | 查看实例配置 |  |
| AgentConnect-15 | 选择服务实例 |  |
| AgentConnect-16 | 运行环境 |  |
| AgentConnect-17 | 请选择 |  |
| AgentConnect-18 | 正在读取服务实例… |  |
| AgentConnect-19 | 草稿中的实例已不可用，请重新选择。 |  |
| AgentConnect-20 | 暂无服务实例，请先在运行环境中接入实例。 |  |
| AgentConnect-21 | 实例中的 Agent 名称 |  |
| AgentConnect-22 | 谁可以使用 |  |
| AgentConnect-23 | 全员指此实例中已获准入的用户，不会自动开通实例准入。 |  |
| AgentConnect-24 | 确认绑定配置 |  |
| AgentConnect-25 | 服务实例 |  |
| AgentConnect-26 | 资源名称 |  |
| AgentConnect-27 | 授权表达式 |  |
| AgentConnect-28 | 实例内已准入的全体用户 |  |
| AgentConnect-29 | 本操作只保存 Agent 绑定。运行规格匹配和最终有效权限仍需在实例中核对。 |  |
| AgentConnect-30 | 正在检查实例配置… |  |
| AgentConnect-31 | 重试检查 |  |
| AgentConnect-32 | 同一 Agent 已有绑定： |  |
| AgentConnect-33 | 实例已有启用的运行规格，仍需核对授权条件能否命中。 |  |
| AgentConnect-34 | 实例没有启用的运行规格。可以先保存绑定，但还不能确认 Agent 可运行。 |  |
| AgentConnect-35 | 再次保存会新增一条绑定，不会覆盖已有授权。 |  |
| AgentConnect-36 | 此 Agent 定义已停用，请先启用。 |  |
| AgentConnect-37 | 上一步 |  |
| AgentConnect-38 | 下一步 |  |
| AgentConnect-39 | 正在保存… |  |
| AgentConnect-40 | 保存绑定配置 |  |

## 4. 完整补充索引

[打开完整文案索引](manager-copy-inventory-full.md)。包含中文语言包全部文案，以及 pages/components 下直接写在代码中的中文字符串。该索引是源码全集，不代表每条当前都可见：可能包含旧版保留文案、未启用功能、条件分支以及文档内容。修改前应按来源确认使用位置。

语言包中的 `{{productName}}`、`{{count}}` 等是动态占位符；建议保留。重复文案如果来自不同位置，可按各自编号分别改；资源中心七项的引导提示与页面副标题则共用同一个文案键。
