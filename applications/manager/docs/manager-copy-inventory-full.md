# Manager 完整中文文案索引

这是补充检索表，不是当前页面可见文案的断言。范围：中文语言包，以及 pages/components 中的中文字符串。包含按钮、字段、校验、弹窗和旧文案，供统一修改时查漏。动态服务端内容及独立 Markdown 帮助文档正文未收录。

## 中文语言包

| 文案键 | 当前文案 | 修改后 |
| --- | --- | --- |
| common.ok | 确定 |  |
| common.cancel | 取消 |  |
| common.close | 关闭 |  |
| common.save | 保存 |  |
| common.delete | 删除 |  |
| common.edit | 编辑 |  |
| common.create | 新建 |  |
| common.search | 搜索 |  |
| common.refresh | 刷新 |  |
| common.loading | 加载中… |  |
| common.empty | 暂无数据 |  |
| common.yes | 是 |  |
| common.no | 否 |  |
| common.enabled | 已启用 |  |
| common.disabled | 已禁用 |  |
| common.detail | 详情 |  |
| common.view | 查看 |  |
| common.actions | 操作 |  |
| common.all | 全部 |  |
| common.sortAsc | 升序 |  |
| common.sortDesc | 降序 |  |
| common.sortDefault | 默认 |  |
| common.filter | 筛选 |  |
| common.confirm | 确认 |  |
| common.unsavedTitle | 放弃未保存的修改？ |  |
| common.unsavedMessage | 当前内容尚未保存，关闭后修改将丢失。确定要关闭吗？ |  |
| common.unsavedDiscard | 放弃并关闭 |  |
| common.submit | 提交 |  |
| common.pagination.total | 共 {{total}} 条 |  |
| common.pagination.itemsPerPage | 条/页 |  |
| common.pagination.prev | 上一页 |  |
| common.pagination.next | 下一页 |  |
| common.pagination.go | 跳转 |  |
| common.pagination.jumpTo | 跳转到页码 |  |
| common.pagination.nav | 分页导航 |  |
| common.pleaseSelect | 请选择 |  |
| common.updatedAt | 更新时间 |  |
| common.configRefreshCost | 保存后将下发到网关并触发运行时强制刷新（config_refresh）：该运行时下全部 AgentServer Pod 会优雅日落并按新配置重建。存量会话不受影响；重建期间新会话可能冷启动变慢，短时间内连续变更可能被容量保护暂时拒绝，稍后会自动恢复。 |  |
| common.configRefreshCostTemplate | 该模板已被实例引用时，保存将把变更下发到网关并触发运行时强制刷新（config_refresh）：对应运行时下全部 AgentServer Pod 优雅日落并按新配置重建。存量会话不受影响；重建期间新会话可能冷启动变慢，短时间内连续变更可能被容量保护暂时拒绝，稍后会自动恢复。 |  |
| importExport.import | 导入 |  |
| importExport.export | 导出 |  |
| importExport.importTitle | 导入配置 |  |
| importExport.importHelp | 请选择由 Manager 导出的 XLSX 文件，系统会先预检，已存在的配置不会被覆盖。 |  |
| importExport.xlsxOnly | 仅支持 .xlsx 文件 |  |
| importExport.preflightPassed | 预检通过，可以导入。 |  |
| importExport.preflightBlocked | 预检未通过，请按下表修正后重新选择文件。 |  |
| importExport.confirmImport | 确认导入 |  |
| importExport.importSuccess | 导入完成：新建 {{created}} 项，复用 {{reused}} 项 |  |
| importExport.exportSuccess | 导出完成 |  |
| importExport.exportFailed | 导出失败：{{detail}} |  |
| importExport.selected | 已选择 {{count}} 项 |  |
| time.secondsAgo | {{count}}s 前 |  |
| time.minutesAgo | {{count}}m 前 |  |
| time.hoursAgo | {{count}}h 前 |  |
| time.daysAgo | {{count}}d 前 |  |
| brand.title | {{productName}} |  |
| brand.sub | Manager |  |
| footer.aiNotice | 内容由AI生成，仅供参考 |  |
| nav.platform | 控制台 |  |
| nav.overview | 总览 |  |
| nav.instances | 服务实例 |  |
| nav.config | 工作台 |  |
| nav.agentResources | Agent资源 |  |
| nav.runtimeResources | 运行时资源 |  |
| nav.containerTemplates | 容器模板 |  |
| nav.modelTemplates | 模型 |  |
| nav.embeddingTemplates | Embedding模型 |  |
| nav.skillWhitelistTemplates | 预置Skill |  |
| nav.expertPackages | 专家包 |  |
| nav.safetyGuardrails | 安全护栏 |  |
| nav.a2aManagement | A2A管理 |  |
| nav.extensionTemplates | 扩展配置 |  |
| nav.mcpTemplates | MCP配置 |  |
| nav.agentManagement | Agent模板 |  |
| nav.serviceConfigTemplates | 运行规格 |  |
| nav.iam | 身份中心 |  |
| nav.users | 用户 |  |
| nav.orgs | 组织 |  |
| nav.bots | Agent模板 |  |
| nav.approvalRequests | 审批申请 |  |
| nav.roles | 角色 |  |
| nav.approvals | 审批中心 |  |
| nav.todo | 待办 |  |
| nav.todoWithCount | 待办，{{count}} 条 |  |
| expertPackages.subtitle | 按集群发布专家和专家团。同一页维护包和授权范围，保存后整份替换并下发。 |  |
| expertPackages.add | 添加 |  |
| expertPackages.addTemplate | 添加专家 |  |
| expertPackages.addGroup | 添加专家团 |  |
| expertPackages.edit | 编辑授权 |  |
| expertPackages.kind | 种类 |  |
| expertPackages.kindTemplate | 专家 |  |
| expertPackages.kindGroup | 专家团 |  |
| expertPackages.packageId | 包 ID |  |
| expertPackages.pickPackage | 从 Hub 目录选择 |  |
| expertPackages.catalogError | Hub 目录暂时不可用：{{message}} |  |
| expertPackages.catalogKindEmpty | 该种类没有可选择的 Hub 包 |  |
| expertPackages.selectAll | 全选 |  |
| expertPackages.alreadyAdded | 已添加 |  |
| expertPackages.scopeHint | 留空表示该集群已准入用户都能用。验证时只填那几位用户或他们所在的组织。 |  |
| expertPackages.scopeUsers | 用户：{{names}} |  |
| expertPackages.scopeOrgs | 组织：{{names}} |  |
| expertPackages.scopeExcludeUsers | 排除用户：{{names}} |  |
| expertPackages.scopeExcludeOrgs | 排除组织：{{names}} |  |
| expertPackages.scopeOr | ，或  |  |
| expertPackages.scopeAnd | ，且  |  |
| expertPackages.scopeNameSep | 、 |  |
| expertPackages.scopeGroupOpen | （ |  |
| expertPackages.scopeGroupClose | ） |  |
| expertPackages.emptyTemplate | 该集群还没有受控专家 |  |
| expertPackages.emptyGroup | 该集群还没有受控专家团 |  |
| expertPackages.deleteConfirm | 从该集群移除 {{id}}？ |  |
| expertPackages.duplicate | 该集群已经有这个包 |  |
| guide.gotoPageLink | 前往 {{page}} 页面 |  |
| guide.missingItem | 未配置 {{item}} |  |
| guide.configGuide | 配置引导 |  |
| guide.gettingStarted | 新手开局 |  |
| guide.advancedConfig | 进阶配置 |  |
| guide.quickEntry | 快捷入口 |  |
| guide.newMember | 新建成员 |  |
| guide.newModel | 新建模型 |  |
| guide.newAgentDefinition | 新建Agent模板 |  |
| guide.newPoolDefinition | 新建运行时模板 |  |
| guide.newContainerTemplate | 新建容器模板 |  |
| guide.noClusters | 暂无集群，请先在集群管理中新建 |  |
| guide.selectCluster | 选择集群 |  |
| guide.clusterAlerts | 集群存在未配置项 |  |
| safetyGuardrails.title | 工具安全策略 |  |
| safetyGuardrails.subtitle | 根据工具风险配置限制，随后在 Agent 中引用。 |  |
| safetyGuardrails.empty | 暂无安全护栏 |  |
| safetyGuardrails.new | 新建安全护栏 |  |
| safetyGuardrails.edit | 编辑安全护栏 |  |
| safetyGuardrails.searchPlaceholder | 搜索模板 ID、名称、描述 |  |
| safetyGuardrails.referenceCount | 被引用 |  |
| safetyGuardrails.referenceHint | 点击查看引用了该模板的 Agent 模板 |  |
| safetyGuardrails.templateName | 模板名称 |  |
| safetyGuardrails.templateDescription | 模板描述 |  |
| safetyGuardrails.updatedAt | 更新时间 |  |
| safetyGuardrails.bodyEnabled | 策略已启用 |  |
| safetyGuardrails.bodyDisabled | 策略已禁用 |  |
| safetyGuardrails.permissionModeTag | 模式：{{value}} |  |
| safetyGuardrails.deleteConfirm | 确认删除该安全护栏模板？ |  |
| safetyGuardrails.fieldRequired | 请填写{{field}} |  |
| safetyGuardrails.unsafeText | {{field}}含有不安全内容 |  |
| connection.ok | 已连接 |  |
| connection.fail | 未连接 |  |
| connection.checking | 检测中 |  |
| overview.title | 总览 |  |
| overview.subtitle | 实时查看管理面与集群整体健康状态 |  |
| overview.managerHealth | Manager 健康 |  |
| overview.totalClusters | 集群总数 |  |
| overview.unitClusters | 集群 |  |
| overview.activeServices | 在线服务数 |  |
| overview.quickNav | 配置检查清单 |  |
| overview.unconfiguredItems | 未配置项 |  |
| overview.noAlerts | 暂无未配置项 |  |
| overview.clusterAlertsTitle | 该集群未配置项 |  |
| overview.noInstances | 暂未发现集群，可在「集群管理」中新建。 |  |
| topology.title | 服务实例 |  |
| topology.subtitle | 管理 {{productName}} 集群 |  |
| topology.createInstance | 新建集群 |  |
| topology.instanceStatus | 集群状态 |  |
| topology.gatewayStatus | Gateway 状态 |  |
| topology.runtimeStatus | Runtime 状态 |  |
| topology.userWebStatus | User Web 状态 |  |
| topology.namespace | 命名空间 |  |
| topology.group | Group |  |
| topology.space | Space |  |
| topology.gateway | Gateway |  |
| topology.runtime | Runtime |  |
| topology.userWeb | User Web |  |
| topology.agentServer | AgentServer |  |
| topology.otherService | 其他服务 |  |
| topology.viewDetail | 详情 |  |
| topology.cardAccess | 准入 |  |
| topology.cardConfig | 配置 |  |
| topology.cardStatus | 状态 |  |
| topology.cardClickHint | 点击进入集群详情 |  |
| topology.noServices | 暂未发现 Gateway / AgentServer 服务（等待探活） |  |
| topology.lastAlive | 最近探活 |  |
| topology.gatewayLastAlive | Gateway 最近探活 |  |
| topology.runtimeLastAlive | Runtime 最近探活 |  |
| topology.userWebLastAlive | User Web 最近探活 |  |
| topology.lastUpdated | 最近更新 |  |
| topology.endpoint | Endpoint |  |
| topology.version | 版本 |  |
| topology.deleteConfirm | 确认删除该集群？ |  |
| topology.filterStatus | 按状态过滤 |  |
| topology.instanceName | 集群名称 |  |
| topology.modifiedAt | 更新时间 |  |
| topology.viewBrief | 卡片视图 |  |
| topology.viewList | 列表视图 |  |
| topology.searchPlaceholder | 搜索集群名称、集群 ID、状态 |  |
| topology.probeFailed | 探活失败：{{detail}} |  |
| topology.import | 导入 |  |
| topology.export | 导出 |  |
| topology.importTitle | 导入集群配置 |  |
| topology.importHelp | 选择 Manager 导出的 .xlsx 文件。系统会先完整预检；同 ID 已存在且配置一致的对象会复用，不会覆盖现有配置。黄色敏感字段若用于新建对象，需要先在 Excel 中填写。 |  |
| topology.importConfirm | 确认导入 |  |
| topology.importXlsxOnly | 请选择 .xlsx 文件 |  |
| topology.preflightPassed | 预检通过，可以导入。 |  |
| topology.preflightBlocked | 预检发现阻塞项，请修改 Excel 或处理目标环境冲突后重新选择文件。 |  |
| topology.importSuccess | 导入完成：创建 {{created}} 项，复用 {{reused}} 项 |  |
| topology.exportSuccess | 集群配置 Excel 已导出 |  |
| topology.exportFailed | 导出失败： |  |
| instanceForm.name | 集群名称 |  |
| instanceForm.description | 集群描述 |  |
| instanceForm.namespace | 命名空间 |  |
| instanceForm.hostMode | 地址填写方式 |  |
| instanceForm.hostModeAuto | 自动生成地址 |  |
| instanceForm.hostModeManual | 手动填写地址 |  |
| instanceForm.hostModeAutoHint | 填写命名空间后，将自动生成五个服务地址（只读预览）。 |  |
| instanceForm.hostModeManualHint | 自行填写全部服务地址 |  |
| instanceForm.namespaceInvalid | 命名空间须为合法的 Kubernetes DNS 标签（小写字母、数字、连字符，不以连字符首尾）。 |  |
| instanceForm.gatewayHost | gateway配置下发地址 |  |
| instanceForm.runtimeHost | runtime配置下发地址 |  |
| instanceForm.userWebHost | 用户web地址 |  |
| instanceForm.gatewayWebHttpHost | gateway用户web HTTP地址 |  |
| instanceForm.gatewayWebWsHost | gateway用户web WS地址 |  |
| instanceForm.createdBy | 创建人 |  |
| instanceForm.spaceId | Space ID |  |
| instanceForm.fieldRequired | 请填写{{field}} |  |
| instanceForm.hostInvalid | {{field}}须为合法的 http(s) 地址 |  |
| instanceForm.unsafeText | {{field}}包含不安全内容 |  |
| instanceDetail.title | 集群详情 |  |
| instanceDetail.services | 纳管服务列表 |  |
| instanceDetail.connectivity | 连通与部署 |  |
| instanceDetail.meta | 状态与元数据 |  |
| instanceDetail.extraData | 扩展数据 (instance_info.data) |  |
| instanceDetail.editConnectivity | 编辑连通与部署 |  |
| instanceDetail.editData | 编辑扩展数据 |  |
| instanceDetail.tabs.access | 集群准入 |  |
| instanceDetail.tabs.clusterConfig | 集群配置 |  |
| instanceDetail.tabs.config | 基础配置 |  |
| instanceDetail.tabs.status | 连接与状态 |  |
| instanceDetail.tabs.quotaManagement | 配额管理 |  |
| instanceDetail.tabs.tokenQuota | Token配额 |  |
| instanceDetail.tabs.cost | 成本统计 |  |
| instanceDetail.tabs.audit | 业务审计 |  |
| instanceDetail.tabs.resources | 集群资源 |  |
| instanceDetail.tabs.workspaceQuota | 用户空间配额 |  |
| instanceDetail.tabs.teamQuota | 团队配额 |  |
| instanceDetail.placeholder.empty | 功能开发中，敬请期待 |  |
| instanceDetail.access.title | 集群准入 |  |
| instanceDetail.access.subtitle | 配置谁可以进入本集群 |  |
| instanceDetail.tokenQuota.title | Token配额 |  |
| instanceDetail.tokenQuota.subtitle | 查看与管理集群 Token 配额 |  |
| instanceDetail.cost.title | 成本统计 |  |
| instanceDetail.cost.subtitle | 查看集群成本与用量统计 |  |
| instanceDetail.audit.title | 业务审计 |  |
| instanceDetail.audit.subtitle | 查看集群业务审计日志 |  |
| instanceDetail.accessPanel.tabs.users | 用户 |  |
| instanceDetail.accessPanel.tabs.orgs | 组织 |  |
| instanceDetail.accessPanel.instance.users | 准入用户 |  |
| instanceDetail.accessPanel.instance.usersHint | 授权用户才能登录到集群 |  |
| instanceDetail.accessPanel.instance.orgs | 准入组织 |  |
| instanceDetail.accessPanel.instance.orgsHint | 授权组织的成员才能登录到集群 |  |
| instanceDetail.accessPanel.instance.addUsers | 添加用户 |  |
| instanceDetail.accessPanel.instance.addOrgs | 添加组织 |  |
| instanceDetail.accessPanel.instance.noUsers | 暂无已授权用户 |  |
| instanceDetail.accessPanel.instance.noOrgs | 暂无已授权组织 |  |
| instanceDetail.accessPanel.instance.searchUsersPlaceholder | 搜索用户 ID、用户名称、授权人、登录权限 |  |
| instanceDetail.accessPanel.instance.searchOrgsPlaceholder | 搜索组织 ID、组织名称、授权人、登录权限 |  |
| instanceDetail.accessPanel.instance.userName | 用户名称 |  |
| instanceDetail.accessPanel.instance.orgName | 组织名称 |  |
| instanceDetail.accessPanel.instance.grantedBy | 授权人 |  |
| instanceDetail.accessPanel.instance.loginPolicy | 登录权限 |  |
| instanceDetail.accessPanel.instance.loginPolicyHint | 拒绝优先级高于允许 |  |
| instanceDetail.accessPanel.instance.loginAllow | 允许 |  |
| instanceDetail.accessPanel.instance.loginDeny | 拒绝 |  |
| instanceDetail.accessPanel.instance.expiresAt | 过期时间 |  |
| instanceDetail.accessPanel.instance.neverExpires | 永不过期 |  |
| instanceDetail.accessPanel.instance.expiresHint | 留空表示永不过期 |  |
| instanceDetail.accessPanel.instance.confirmRemoveUsers | 从本集群移除选中的 {{n}} 个用户？ |  |
| instanceDetail.accessPanel.instance.confirmRemoveOrgs | 从本集群移除选中的 {{n}} 个组织？ |  |
| instanceDetail.resourcePanel.tabs.agent | Agent |  |
| instanceDetail.resourcePanel.tabs.serviceResource | 运行时 |  |
| instanceDetail.resourcePanel.agent.title | Agent |  |
| instanceDetail.resourcePanel.agent.subtitle | 基于 Agent模板 接入并管理 Agent，配置各 Agent 的授权范围 |  |
| instanceDetail.resourcePanel.agent.agentLabel | Agent模板 |  |
| instanceDetail.resourcePanel.agent.agentInstance | Agent实例 |  |
| instanceDetail.resourcePanel.agent.agentTemplate | Agent模板 |  |
| instanceDetail.resourcePanel.agent.scopeLabel | 授权范围 |  |
| instanceDetail.resourcePanel.agent.scopeAll | 全员可用 |  |
| instanceDetail.resourcePanel.agent.grantedBy | 授权人 |  |
| instanceDetail.resourcePanel.agent.expiresAt | 过期时间 |  |
| instanceDetail.resourcePanel.agent.neverExpires | 永不过期 |  |
| instanceDetail.resourcePanel.agent.searchPlaceholder | 搜索资源 ID、资源名称、资源描述、模板 ID/名称、授权人 |  |
| instanceDetail.resourcePanel.agent.add | 添加Agent |  |
| instanceDetail.resourcePanel.agent.editGrant | 编辑授权 · {{name}} |  |
| instanceDetail.resourcePanel.agent.grantHint | match_expr 为空表示该集群上已准入用户均可使用。 |  |
| instanceDetail.resourcePanel.agent.expiresHint | 留空表示永不过期 |  |
| instanceDetail.resourcePanel.agent.empty | 本集群尚未添加 Agent |  |
| instanceDetail.resourcePanel.agent.confirmRemove | 从本集群移除选中的 {{n}} 个 Agent？ |  |
| instanceDetail.resourcePanel.agent.resourceId | 资源 ID |  |
| instanceDetail.resourcePanel.agent.resourceName | 资源名称 |  |
| instanceDetail.resourcePanel.agent.resourceNamePlaceholder | 请输入资源名称 |  |
| instanceDetail.resourcePanel.agent.resourceDesc | 资源描述 |  |
| instanceDetail.resourcePanel.agent.resourceDescPlaceholder | 请输入资源描述（可选） |  |
| instanceDetail.resourcePanel.serviceResource.title | 运行时 |  |
| instanceDetail.resourcePanel.serviceResource.subtitle | 基于 运行时模板 创建并管理实例池，配置各池的调度策略，实例在会话触发时按需分配 |  |
| instanceDetail.resourcePanel.serviceResource.template | 运行时模板 |  |
| instanceDetail.resourcePanel.serviceResource.scopeLabel | 授权范围 |  |
| instanceDetail.resourcePanel.serviceResource.scopeAll | 全员可用 |  |
| instanceDetail.resourcePanel.serviceResource.priority | 优先级 |  |
| instanceDetail.resourcePanel.serviceResource.priorityHint | 越小越优先。同一集群里，启用中的策略不能使用相同优先级。 |  |
| instanceDetail.resourcePanel.serviceResource.grantedBy | 授权人 |  |
| instanceDetail.resourcePanel.serviceResource.expiresAt | 过期时间 |  |
| instanceDetail.resourcePanel.serviceResource.neverExpires | 永不过期 |  |
| instanceDetail.resourcePanel.serviceResource.expiresHint | 留空表示永不过期 |  |
| instanceDetail.resourcePanel.serviceResource.searchPlaceholder | 搜索资源 ID、资源名称、资源描述、模板 ID/名称、授权人、优先级 |  |
| instanceDetail.resourcePanel.serviceResource.add | 添加运行时 |  |
| instanceDetail.resourcePanel.serviceResource.editGrant | 编辑授权 · {{name}} |  |
| instanceDetail.resourcePanel.serviceResource.empty | 本集群尚未分配运行时 |  |
| instanceDetail.resourcePanel.serviceResource.confirmRemove | 从本集群移除选中的 {{n}} 个运行时？ |  |
| instanceDetail.resourcePanel.serviceResource.resourceId | 资源 ID |  |
| instanceDetail.resourcePanel.serviceResource.resourceName | 资源名称 |  |
| instanceDetail.resourcePanel.serviceResource.resourceDesc | 资源描述 |  |
| instanceDetail.workspaceQuota.title | 用户空间配额 |  |
| instanceDetail.workspaceQuota.subtitle | 按匹配范围配置本集群的用户空间配额 |  |
| instanceDetail.workspaceQuota.tabs.policy | 配额策略管理 |  |
| instanceDetail.workspaceQuota.tabs.usage | 用户空间用量统计 |  |
| instanceDetail.workspaceQuota.new | 新建策略 |  |
| instanceDetail.workspaceQuota.edit | 编辑策略 |  |
| instanceDetail.workspaceQuota.searchPlaceholder | 搜索策略ID、策略名称、策略描述、匹配范围 |  |
| instanceDetail.workspaceQuota.policyName | 策略名称 |  |
| instanceDetail.workspaceQuota.policyDesc | 策略描述 |  |
| instanceDetail.workspaceQuota.priority | 优先级 |  |
| instanceDetail.workspaceQuota.priorityHint | 越小越优先。管理员策略须 ≥ 0，且同集群启用中不可重复。负数域留给特殊操作。审批通过写入的策略固定为-1（可多条并存）。 |  |
| instanceDetail.workspaceQuota.matchExpr | 匹配范围 |  |
| instanceDetail.workspaceQuota.fullMatch | 全匹配 |  |
| instanceDetail.workspaceQuota.limit | 配额 |  |
| instanceDetail.workspaceQuota.tier | 档位 |  |
| instanceDetail.workspaceQuota.tierCustom | 自定义 |  |
| instanceDetail.workspaceQuota.tierZero | 0（不分配空间） |  |
| instanceDetail.workspaceQuota.tierUnlimited | 无限制 |  |
| instanceDetail.workspaceQuota.unlimited | 无限制 |  |
| instanceDetail.workspaceQuota.customSize | 自定义大小 |  |
| instanceDetail.workspaceQuota.customUnit | 单位 |  |
| instanceDetail.workspaceQuota.bytesHint | 实际写入 {{bytes}} 字节 |  |
| instanceDetail.workspaceQuota.bytesHintUnlimited | 实际写入 -1（无限制哨兵） |  |
| instanceDetail.workspaceQuota.softPercent | 提醒阈值（%） |  |
| instanceDetail.workspaceQuota.hardPercent | 阻断阈值（%） |  |
| instanceDetail.workspaceQuota.softPercentHint | 达到提醒阈值后提醒用户配额即将超限。 |  |
| instanceDetail.workspaceQuota.hardPercentHint | 达到阻断阈值时阻断增加用户空间用量的操作。 |  |
| instanceDetail.workspaceQuota.sourceOrder | 来源 |  |
| instanceDetail.workspaceQuota.sourceManual | 手动 |  |
| instanceDetail.workspaceQuota.sourceApproval | 审批 |  |
| instanceDetail.workspaceQuota.locked | 已锁定 |  |
| instanceDetail.workspaceQuota.lockedHint | 审批来源策略已锁定，不可编辑配额与匹配范围；可停用或删除。改额度请走扩容审批。 |  |
| instanceDetail.workspaceQuota.invalidPriority | 优先级须为大于等于 0 的整数 |  |
| instanceDetail.workspaceQuota.disableTitle | 停用策略 |  |
| instanceDetail.workspaceQuota.disableMessage | 停用后，原本命中这条策略的主体会改走下一条命中规则。确定停用？ |  |
| instanceDetail.workspaceQuota.lowerTitle | 下调配额 |  |
| instanceDetail.workspaceQuota.lowerMessage | 下调后，已用空间超过新上限的主体会被阻断。确定保存？ |  |
| instanceDetail.workspaceQuota.deleteTitle | 删除策略 |  |
| instanceDetail.workspaceQuota.deleteMessage | 删除后，原本命中这条策略的主体会改走下一条命中规则。确定删除？ |  |
| instanceDetail.workspaceQuota.invalidLimit | 配额须为 0、正整数，或选择无限制 |  |
| instanceDetail.workspaceQuota.invalidPercent | 阻断阈值必须大于提醒阈值，且都不能为负 |  |
| instanceDetail.workspaceQuota.fieldRequired | 请填写{{field}} |  |
| instanceDetail.workspaceQuota.updatedAt | 更新时间 |  |
| instanceDetail.workspaceQuota.usage.title | 用户空间用量统计 |  |
| instanceDetail.workspaceQuota.usage.subtitle | 展示本集群已缓存的用户空间用量。数据来自用户查看工作空间页面后的用量快照，可能略旧于最新用量。 |  |
| instanceDetail.workspaceQuota.usage.searchPlaceholder | 搜索用户 ID、组织 ID、Bot ID |  |
| instanceDetail.workspaceQuota.usage.subjectCount | 主体数 |  |
| instanceDetail.workspaceQuota.usage.subjectCountHint | 按用户、组织、Bot 三元组计数：每个有用量缓存的工作区身份算 1 个主体。 |  |
| instanceDetail.workspaceQuota.usage.totalUsed | 总已用 |  |
| instanceDetail.workspaceQuota.usage.warnCount | 提醒中 |  |
| instanceDetail.workspaceQuota.usage.blockCount | 已阻断 |  |
| instanceDetail.workspaceQuota.usage.statusChart | 状态分布 |  |
| instanceDetail.workspaceQuota.usage.topChart | 用量 Top |  |
| instanceDetail.workspaceQuota.usage.userId | 用户 |  |
| instanceDetail.workspaceQuota.usage.groupId | 组织 |  |
| instanceDetail.workspaceQuota.usage.botId | Bot |  |
| instanceDetail.workspaceQuota.usage.used | 已用 |  |
| instanceDetail.workspaceQuota.usage.limit | 配额 |  |
| instanceDetail.workspaceQuota.usage.percent | 使用率 |  |
| instanceDetail.workspaceQuota.usage.status | 状态 |  |
| instanceDetail.workspaceQuota.usage.statusOk | 正常 |  |
| instanceDetail.workspaceQuota.usage.statusWarn | 提醒 |  |
| instanceDetail.workspaceQuota.usage.statusBlock | 阻断 |  |
| instanceDetail.workspaceQuota.usage.policyId | 命中策略 |  |
| instanceDetail.workspaceQuota.usage.reportedAt | 计量时间 |  |
| instanceDetail.workspaceQuota.usage.empty | 暂无用量缓存。用户打开用量或文件列表后会出现在此。 |  |
| instanceDetail.teamQuota.title | 团队配额 |  |
| instanceDetail.teamQuota.subtitle | 按匹配范围配置本集群每用户最多团队数、每组织最多团队数、每团队最多人数（含 Leader） |  |
| instanceDetail.teamQuota.tabs.policy | 配额策略管理 |  |
| instanceDetail.teamQuota.tabs.usage | 团队占用统计 |  |
| instanceDetail.teamQuota.new | 新建策略 |  |
| instanceDetail.teamQuota.edit | 编辑策略 |  |
| instanceDetail.teamQuota.policyName | 策略名称 |  |
| instanceDetail.teamQuota.policyDesc | 策略描述 |  |
| instanceDetail.teamQuota.priority | 优先级 |  |
| instanceDetail.teamQuota.priorityHint | 越大越优先。多条命中时取优先级最高的一条；无匹配则走实例默认，再否则 5/5。 |  |
| instanceDetail.teamQuota.matchExpr | 匹配范围 |  |
| instanceDetail.teamQuota.fullMatch | 全匹配（实例默认） |  |
| instanceDetail.teamQuota.maxTeams | 每用户最多团队数 |  |
| instanceDetail.teamQuota.maxTeamsHint | 该用户作为 owner 的活跃团队上限。加入别人的队不占自己的额度。 |  |
| instanceDetail.teamQuota.maxTeamsPerOrg | 每组织最多团队数 |  |
| instanceDetail.teamQuota.maxTeamsPerOrgHint | 该组织下所有成员、所有 Bot 作为 owner 的活跃团队总和。0 表示不限制。只对全匹配或只匹配组织的策略生效。 |  |
| instanceDetail.teamQuota.maxTeammates | 每团队最多人数（含 Leader） |  |
| instanceDetail.teamQuota.maxTeammatesHint | 整队上限，含 Leader。填 5 = Leader + 最多 4 名队员。join 不占额，spawn 才占。 |  |
| instanceDetail.teamQuota.updatedAt | 更新时间 |  |
| instanceDetail.teamQuota.invalidPriority | 优先级须为整数 |  |
| instanceDetail.teamQuota.invalidLimit | 上限须为 1 到 1000 的整数 |  |
| instanceDetail.teamQuota.invalidOrgLimit | 每组织最多团队数须为 0 到 1000 的整数，0 表示不限制 |  |
| instanceDetail.teamQuota.fieldRequired | 请填写{{field}} |  |
| instanceDetail.teamQuota.deleteTitle | 删除策略 |  |
| instanceDetail.teamQuota.deleteMessage | 删除后，原本命中这条策略的主体会改走下一条命中规则或默认 5/5。确定删除？ |  |
| instanceDetail.teamQuota.usage.subtitle | 数据来自该实例 Gateway 对占用租约的 COUNT，不扫 AgentServer。 |  |
| instanceDetail.teamQuota.usage.userId | 用户 |  |
| instanceDetail.teamQuota.usage.groupId | 组织 |  |
| instanceDetail.teamQuota.usage.botId | Bot |  |
| instanceDetail.teamQuota.usage.used | 已用 |  |
| instanceDetail.teamQuota.usage.limit | 上限 |  |
| instanceDetail.teamQuota.usage.default | 默认 |  |
| instanceDetail.teamQuota.usage.teamId | 团队 |  |
| instanceDetail.teamQuota.usage.empty | 暂无占用。用户创建团队后会出现在此。 |  |
| instanceDetail.teamQuota.usage.orgsTitle | 组织占用 |  |
| instanceDetail.teamQuota.usage.orgUnlimited | 不限制 |  |
| instanceConfig.tabs.logMasking | 日志脱敏 |  |
| instanceConfig.tabs.logging | 日志级别 |  |
| instanceConfig.tabs.auditLog | 审计日志 |  |
| instanceConfig.tabs.cronPolicy | 定时任务 |  |
| instanceConfig.tabs.permissions | 权限配置 |  |
| instanceConfig.tabs.workspaceQuota | 用户空间配额 |  |
| instanceConfig.logMasking.new | 新建脱敏规则 |  |
| instanceConfig.logMasking.edit | 编辑脱敏规则 |  |
| instanceConfig.logMasking.ruleName | 规则名称 |  |
| instanceConfig.logMasking.ruleDescription | 规则描述 |  |
| instanceConfig.logMasking.pattern | 匹配正则表达式 |  |
| instanceConfig.logMasking.patternPlaceholder | 例如 (?i)(api[_-]?key)\s*[:=]\s*\S+ |  |
| instanceConfig.logMasking.replacement | 替换文本 |  |
| instanceConfig.logMasking.priority | 优先级 |  |
| instanceConfig.logMasking.priorityHint | 按优先级从小到大依次应用脱敏规则。同一集群里，启用中的规则不能使用相同优先级。 |  |
| instanceConfig.logMasking.priorityInvalid | 请填写有效的优先级 |  |
| instanceConfig.logMasking.withFingerprint | 附带指纹 |  |
| instanceConfig.logMasking.withFingerprintHint | 开启后脱敏结果会附带不可逆指纹，便于在日志中关联同一敏感值；关闭则仅使用替换文本。 |  |
| instanceConfig.logMasking.source | 来源 |  |
| instanceConfig.logMasking.source_builtin | 内置 |  |
| instanceConfig.logMasking.source_custom | 自定义 |  |
| instanceConfig.logMasking.data | 扩展数据 (JSON, 可选) |  |
| instanceConfig.logMasking.updatedAt | 更新时间 |  |
| instanceConfig.logMasking.searchPlaceholder | 搜索规则ID、名称、描述、匹配正则表达式、替换文本、优先级、来源 |  |
| instanceConfig.logMasking.fieldRequired | 请填写{{field}} |  |
| instanceConfig.logMasking.deleteConfirm | 确认删除该日志脱敏规则？ |  |
| instanceConfig.cronPolicy.intro | 限制用户在 Web 面板里同时处于运行中的定时任务个数。默认 5。只拦截面板上的新建启用、启动暂停任务，以及把暂停任务改成启用。通过对话、TUI、Cron 表达式创建，以及已经在运行的任务，不受这次调整影响，也不会被删除或停止。0 表示不能再从面板启用新的运行任务。未托管时使用实例本地 config.yaml，缺省同样是 5。 |  |
| instanceConfig.cronPolicy.managed | Manager 托管 |  |
| instanceConfig.cronPolicy.resetToYaml | 删除托管配置 |  |
| instanceConfig.cronPolicy.deleteConfirm | 确认删除 Manager 托管的定时任务个数上限？Gateway 将回退到 config.yaml。 |  |
| instanceConfig.cronPolicy.deleted | 已删除托管配置，Gateway 将回退到 config.yaml |  |
| instanceConfig.cronPolicy.maxJobs | 每个用户允许运行的定时任务数 |  |
| instanceConfig.cronPolicy.maxJobsHint | 只统计当前启用且未过期的定时任务。填 0 时，面板不能再启用新任务。 |  |
| instanceConfig.cronPolicy.invalid | 请填写大于或等于 0 的整数 |  |
| instanceConfig.logging.intro | 对应 config.yaml 的 logging 段。保存后写入 Manager DB 并下发至 Gateway 热更新；未托管时使用实例本地 config.yaml。 |  |
| instanceConfig.logging.managed | Manager 托管 |  |
| instanceConfig.logging.yamlFallback | 使用 config.yaml |  |
| instanceConfig.logging.resetToYaml | 删除托管配置 |  |
| instanceConfig.logging.deleteConfirm | 确认删除 Manager 托管的 logging 配置？Gateway 将回退到 config.yaml。 |  |
| instanceConfig.logging.deleted | 已删除托管配置，Gateway 将回退到 config.yaml |  |
| instanceConfig.logging.level | 默认级别 (level) |  |
| instanceConfig.logging.levelHint | 未单独指定各日志文件级别时使用 |  |
| instanceConfig.logging.consoleLevel | 控制台级别 (console_level) |  |
| instanceConfig.logging.consoleLevelHint | 控制台输出；省略时沿用 level |  |
| instanceConfig.logging.gateway | Gateway 日志 (gateway) |  |
| instanceConfig.logging.channel | Channel 日志 (channel) |  |
| instanceConfig.logging.agentServer | AgentServer 日志 (agent_server) |  |
| instanceConfig.logging.full | 汇总日志 (full) |  |
| instanceConfig.logging.fullHint | full.log 汇总输出级别 |  |
| instanceConfig.auditLog.intro | 配置安全审计日志字段清单与运行标识。保存后写入 Manager DB 并下发 Gateway（热加载）；AgentServer 经实例重建后生效。未托管时使用进程侧默认行为。 |  |
| instanceConfig.auditLog.otelDeployHint | OTEL 导出（开关 / Endpoint / 协议）由企业部署环境变量与 telemetry 配置，不在此页修改；审计查询见 Observability。 |  |
| instanceConfig.auditLog.managed | Manager 托管 |  |
| instanceConfig.auditLog.resetToDefault | 删除托管配置 |  |
| instanceConfig.auditLog.deleteConfirm | 确认删除 Manager 托管的审计日志配置？将通知 Gateway 删除后再清除 Manager 库中的配置。 |  |
| instanceConfig.auditLog.deleted | 已删除托管配置 |  |
| instanceConfig.auditLog.sections.identity | 运行标识 |  |
| instanceConfig.auditLog.sections.format | 字段清单 |  |
| instanceConfig.auditLog.dataCenter | 数据中心 (data_center) |  |
| instanceConfig.auditLog.systemCode | 系统编码 (system_code) |  |
| instanceConfig.auditLog.node | 节点 (node) |  |
| instanceConfig.auditLog.serviceHint | service / service.name 由各进程本地推导，不在此配置。 |  |
| instanceConfig.auditLog.schemaVersion | Schema 版本 |  |
| instanceConfig.auditLog.placeholder | 空值占位符 |  |
| instanceConfig.auditLog.timestampFormat | 时间戳格式 |  |
| instanceConfig.auditLog.timestampFormatHint | 本阶段固定，不可修改 |  |
| instanceConfig.auditLog.headerFields | 头部字段 |  |
| instanceConfig.auditLog.contentFields | 内容字段 |  |
| instanceConfig.auditLog.requiredFields | 必填字段 |  |
| instanceConfig.auditLog.requiredSubsetHint | 必填字段必须同时勾选在头部或内容中；带 * 的启用字段不可直接取消（请先取消必填）。 |  |
| instanceConfig.auditLog.restoreDefaultFields | 恢复默认字段 |  |
| instanceConfig.auditLog.cannotUncheckRequired | 该字段仍为必填，请先取消必填后再取消启用 |  |
| instanceConfig.auditLog.validation.fieldsEmpty | 头部 / 内容 / 必填字段均不能为空 |  |
| instanceConfig.auditLog.validation.requiredNotSubset | 必填字段必须是已启用字段的子集 |  |
| instanceConfig.auditLog.validation.placeholderRequired | 请填写空值占位符 |  |
| instanceConfig.auditLog.validation.schemaVersionRequired | 请填写 Schema 版本 |  |
| instanceConfig.permissions.sections.general | 基础设置 |  |
| instanceConfig.permissions.sections.tools | 工具权限 |  |
| instanceConfig.permissions.sections.rules | 规则 |  |
| instanceConfig.permissions.sections.fileGuard | 文件路径 |  |
| instanceConfig.permissions.resetToYaml | 删除托管配置 |  |
| instanceConfig.permissions.deleteConfirm | 确认删除 Manager 托管的 permissions 配置？Gateway 将回退到 config.yaml。 |  |
| instanceConfig.permissions.deleted | 已删除托管配置，Gateway 将回退到 config.yaml |  |
| instanceConfig.permissions.enabled | 启用权限管控 |  |
| instanceConfig.permissions.skillAuthorizationEnabled | 启用技能动态授权 |  |
| instanceConfig.permissions.skillAuthorizationHint | 加载技能时，根据 SKILL.md 中声明的权限动态请求用户授权；需先启用权限管控 |  |
| instanceConfig.permissions.permissionMode | 权限模式 (permission_mode) |  |
| instanceConfig.permissions.permissionModeHint | normal：LOW/MEDIUM→allow，HIGH/CRITICAL→ask；strict：LOW→allow，MEDIUM/HIGH→ask，CRITICAL→deny |  |
| instanceConfig.permissions.modes.normal | normal |  |
| instanceConfig.permissions.modes.strict | strict |  |
| instanceConfig.permissions.defaults | 未列明工具的默认动作 (defaults.*) |  |
| instanceConfig.permissions.defaultsHint | 工具未在「工具权限」中单独配置、且未命中规则时使用 |  |
| instanceConfig.permissions.toolName | 工具名称 |  |
| instanceConfig.permissions.toolNameRequired | 请填写工具名称 |  |
| instanceConfig.permissions.action | 动作 |  |
| instanceConfig.permissions.actions.allow | 允许 |  |
| instanceConfig.permissions.actions.ask | 询问 |  |
| instanceConfig.permissions.actions.deny | 拒绝 |  |
| instanceConfig.permissions.newTool | 添加工具权限 |  |
| instanceConfig.permissions.editTool | 编辑工具权限 |  |
| instanceConfig.permissions.toolsHint | 整工具级允许 / 询问 / 拒绝 |  |
| instanceConfig.permissions.ruleId | 规则 ID |  |
| instanceConfig.permissions.ruleIdRequired | 请填写规则 ID |  |
| instanceConfig.permissions.ruleConfig | 配置 |  |
| instanceConfig.permissions.rulePattern | 匹配模式 |  |
| instanceConfig.permissions.rulePatternRequired | 请填写匹配模式 |  |
| instanceConfig.permissions.ruleTools | 适用工具 |  |
| instanceConfig.permissions.ruleToolsRequired | 请至少填写一个适用工具 |  |
| instanceConfig.permissions.ruleToolsHint | 逗号分隔，例如 bash, mcp_exec_command, create_terminal |  |
| instanceConfig.permissions.ruleSeverity | 严重级别 |  |
| instanceConfig.permissions.severities.LOW | LOW |  |
| instanceConfig.permissions.severities.MEDIUM | MEDIUM |  |
| instanceConfig.permissions.severities.HIGH | HIGH |  |
| instanceConfig.permissions.severities.CRITICAL | CRITICAL |  |
| instanceConfig.permissions.newRule | 添加规则 |  |
| instanceConfig.permissions.editRule | 编辑规则 |  |
| instanceConfig.permissions.rulesHint | 与 config.yaml rules 对齐：id、tools、pattern、severity |  |
| instanceConfig.permissions.fileGuardJson | file_guard (JSON) |  |
| modelTemplate.title | 模型 |  |
| modelTemplate.subtitle | 新建或检查要用于本次 Agent 的模型。 |  |
| modelTemplate.new | 新建模型 |  |
| modelTemplate.edit | 编辑模型 |  |
| modelTemplate.templateName | 模板名称 |  |
| modelTemplate.templateDescription | 模板描述 |  |
| modelTemplate.modelType | 模型类型 |  |
| modelTemplate.modelTypePlaceholder | 请选择模型类型 |  |
| modelTemplate.modelTags | 模型标签 |  |
| modelTemplate.modelTagsHint | 逗号分隔，例如 fast,zh,qa |  |
| modelTemplate.apiBase | API Base |  |
| modelTemplate.apiKey | API Key |  |
| modelTemplate.modelId | 模型ID |  |
| modelTemplate.modelProvider | Provider |  |
| modelTemplate.parameters | 模型参数 |  |
| modelTemplate.headers | 请求头 |  |
| modelTemplate.headersHint | 调用 LLM 时作为自定义请求头 |  |
| modelTemplate.headersMustBeObject | 请求头须为 JSON 对象 |  |
| modelTemplate.timeout | 超时（秒） |  |
| modelTemplate.retryCount | 重试次数 |  |
| modelTemplate.enableStreaming | 启用流式 |  |
| modelTemplate.enableFunctionCalling | 启用 Function Calling |  |
| modelTemplate.verifySsl | 校验 SSL |  |
| modelTemplate.filterType | 按类型筛选 |  |
| modelTemplate.searchPlaceholder | 搜索模板 ID、模板名称、模板描述、Provider、模型ID、模型类型、API Base |  |
| modelTemplate.referenceCount | 被引用 |  |
| modelTemplate.referenceHint | 点击查看引用了该模板的 Agent 模板 |  |
| modelTemplate.updatedAt | 更新时间 |  |
| modelTemplate.fieldRequired | 请填写{{field}} |  |
| modelTemplate.apiBaseInvalid | API Base 须为合法的 http(s) URL，例如 https://api.example.com/v1 |  |
| modelTemplate.timeoutMin | 超时需大于 0 |  |
| modelTemplate.retryCountMin | 重试次数不能小于 0 |  |
| modelTemplate.unsafeText | {{field}} 含有非法 HTML/脚本或疑似 SQL 注入内容，请修改后重试 |  |
| modelTemplate.deleteConfirm | 确认删除该模型模板？该模板可能被多个实例资源引用，请确认无副作用。 |  |
| embeddingTemplate.title | 向量模型 |  |
| embeddingTemplate.subtitle | 需要知识库或向量检索时配置 Embedding 模型。 |  |
| embeddingTemplate.new | 新建 Embedding模型 |  |
| embeddingTemplate.edit | 编辑 Embedding模型 |  |
| embeddingTemplate.templateName | 模板名称 |  |
| embeddingTemplate.templateDescription | 模板描述 |  |
| embeddingTemplate.embedTags | Embedding 标签 |  |
| embeddingTemplate.embedTagsHint | 逗号分隔，例如 memory,task_memory |  |
| embeddingTemplate.apiBase | API Base |  |
| embeddingTemplate.apiKey | API Key |  |
| embeddingTemplate.modelId | 模型 ID |  |
| embeddingTemplate.modelProvider | Provider |  |
| embeddingTemplate.parameters | 请求参数 |  |
| embeddingTemplate.clientConfig | 客户端配置 |  |
| embeddingTemplate.data | 扩展数据 |  |
| embeddingTemplate.updatedAt | 更新时间 |  |
| embeddingTemplate.searchPlaceholder | 搜索模板 ID、名称、描述、Provider、模型 ID 或 API Base |  |
| embeddingTemplate.referenceCount | 被引用 |  |
| embeddingTemplate.referenceHint | 点击查看引用了该模板的 Agent 模板 |  |
| embeddingTemplate.fieldRequired | 请填写{{field}} |  |
| embeddingTemplate.apiBaseInvalid | API Base 须为合法的 http(s) URL，例如 https://api.example.com/v1 |  |
| embeddingTemplate.unsafeText | {{field}} 含有非法 HTML/脚本或疑似 SQL 注入内容，请修改后重试 |  |
| embeddingTemplate.deleteConfirm | 确认删除该 Embedding 模板？该模板可能被多个实例资源引用，请确认无副作用。 |  |
| mcpTemplate.title | MCP服务 |  |
| mcpTemplate.subtitle | 需要外部系统能力时配置 MCP。 |  |
| mcpTemplate.new | 新建 MCP配置 |  |
| mcpTemplate.edit | 编辑 MCP配置 |  |
| mcpTemplate.templateName | 模板名称 |  |
| mcpTemplate.templateDescription | 模板描述 |  |
| mcpTemplate.mcpName | MCP 名称 |  |
| mcpTemplate.mcpNameHint | 运行时 servers[] 中的唯一名称，如 demo-tools |  |
| mcpTemplate.transport | 传输方式 |  |
| mcpTemplate.url | 服务地址 |  |
| mcpTemplate.urlHint | 例如 https://mcp.example.com/mcp |  |
| mcpTemplate.headers | 请求头 |  |
| mcpTemplate.headersHint | 可选 JSON 对象，如 {"Authorization": "Bearer xxx"} |  |
| mcpTemplate.timeoutS | 超时（秒） |  |
| mcpTemplate.timeoutSHint | 可选，正数 |  |
| mcpTemplate.updatedAt | 修改时间 |  |
| mcpTemplate.searchPlaceholder | 搜索模板 ID、名称、描述或 MCP 名称 |  |
| mcpTemplate.referenceCount | 被引用 |  |
| mcpTemplate.referenceHint | 点击查看引用了该模板的 Agent 模板 |  |
| mcpTemplate.fieldRequired | 请填写{{field}} |  |
| mcpTemplate.urlInvalid | 服务地址须为合法的 http(s) URL |  |
| mcpTemplate.timeoutInvalid | 超时须为正数 |  |
| mcpTemplate.headersMustBeObject | 请求头须为 JSON 对象 |  |
| mcpTemplate.unsafeText | {{field}} 含有非法 HTML/脚本或疑似 SQL 注入内容，请修改后重试 |  |
| mcpTemplate.deleteConfirm | 确认删除该 MCP 模板？若已被 Agent 模板引用将无法删除。 |  |
| mcpTemplate.customConfig | 自定义配置（JSON对象，可选） |  |
| mcpTemplate.customConfigHint | 填写服务级扩展配置；留空或填写 {} 清空自定义配置。 |  |
| mcpTemplate.customConfigInvalid | 自定义配置须为 JSON 对象。 |  |
| mcpTemplate.customConfigReserved | 自定义配置不能包含基础字段：{{fields}} |  |
| extensionTemplate.title | 扩展配置 |  |
| extensionTemplate.subtitle | 按需添加高级运行或集成扩展。 |  |
| extensionTemplate.new | 新建扩展配置 |  |
| extensionTemplate.edit | 编辑扩展配置 |  |
| extensionTemplate.templateName | 模板名称 |  |
| extensionTemplate.templateDescription | 模板描述 |  |
| extensionTemplate.component | 目标组件 |  |
| extensionTemplate.componentHint | gateway / agent_server |  |
| extensionTemplate.hookType | 钩子类型 |  |
| extensionTemplate.hookTypeHint | pre_request / post_request / error / schedule |  |
| extensionTemplate.hookConfig | 钩子配置 |  |
| extensionTemplate.hookHandler | handler |  |
| extensionTemplate.hookHandlerHint | 钩子实现路径或模块标识，如 hooks.auth.pre_request |  |
| extensionTemplate.hookParams | params |  |
| extensionTemplate.hookParamsHint | 传入钩子函数的静态参数 (JSON) |  |
| extensionTemplate.hookSchedule | schedule |  |
| extensionTemplate.hookScheduleHint | 仅钩子类型为 schedule 时必填；cron 表达式，如 0 */5 * * * |  |
| extensionTemplate.hookScheduleInvalid | schedule 须为合法 cron（5/6/7 段且字段取值在有效范围内，如 0 */5 * * *） |  |
| extensionTemplate.hookData | data |  |
| extensionTemplate.hookDataHint | 单条钩子扩展配置 (JSON) |  |
| extensionTemplate.customConfig | 自定义配置 |  |
| extensionTemplate.customConfigHint | 用户自定义配置项 (JSON) |  |
| extensionTemplate.updatedAt | 更新时间 |  |
| extensionTemplate.searchPlaceholder | 搜索模板 ID、模板名称、模板描述、目标组件、钩子类型 |  |
| extensionTemplate.referenceCount | 被引用 |  |
| extensionTemplate.referenceHint | 点击查看引用了该模板的 Agent 模板 |  |
| extensionTemplate.fieldRequired | 请填写{{field}} |  |
| extensionTemplate.unsafeText | {{field}} 含有非法 HTML/脚本或疑似 SQL 注入内容，请修改后重试 |  |
| extensionTemplate.deleteConfirm | 确认删除该扩展模板？ |  |
| skillWhitelistTemplate.title | 预置技能源 |  |
| skillWhitelistTemplate.subtitle | 按业务用途添加预置技能。 |  |
| skillWhitelistTemplate.new | 新建预置 Skill |  |
| skillWhitelistTemplate.edit | 编辑预置 Skill |  |
| skillWhitelistTemplate.templateName | 模板名称 |  |
| skillWhitelistTemplate.templateDescription | 模板描述 |  |
| skillWhitelistTemplate.skillId | Skill ID |  |
| skillWhitelistTemplate.skillIdHint | 如 search/weather、crm/lead_lookup |  |
| skillWhitelistTemplate.skillVersion | Skill版本 |  |
| skillWhitelistTemplate.skillSource | Skill来源 |  |
| skillWhitelistTemplate.installSource | 安装来源 |  |
| skillWhitelistTemplate.modeSpi | SPI |  |
| skillWhitelistTemplate.modeUrl | URL |  |
| skillWhitelistTemplate.packageUrl | 技能包 URL |  |
| skillWhitelistTemplate.packageUrlHint | URL 安装时必填，须为合法 http(s) 直链 |  |
| skillWhitelistTemplate.sourceId | 技能源 ID |  |
| skillWhitelistTemplate.sourceIdHint | 如 swarmskillhub |  |
| skillWhitelistTemplate.versionId | 版本 ID |  |
| skillWhitelistTemplate.versionIdHint | 1.0.0 |  |
| skillWhitelistTemplate.author | 作者（可选） |  |
| skillWhitelistTemplate.authorHint | Skill技能的作者 |  |
| skillWhitelistTemplate.sha256 | SHA256（可选，仅 url） |  |
| skillWhitelistTemplate.sha256Hint | 64 位小写 hex；provider 路径忽略 |  |
| skillWhitelistTemplate.sha256Invalid | SHA256 须为 64 位小写十六进制 |  |
| skillWhitelistTemplate.modeHint | 填齐 source_id + version_id 走 SPI Provider；否则填 package_url 走受控 URL 安装 |  |
| skillWhitelistTemplate.providerIncomplete | provider 路径须同时填写 source_id 与 version_id |  |
| skillWhitelistTemplate.installPathRequired | 请填写 source_id+version_id，或填写合法的 package_url |  |
| skillWhitelistTemplate.updatedAt | 更新时间 |  |
| skillWhitelistTemplate.searchPlaceholder | 搜索模板 ID、模板名称、模板描述、安装来源、Skill ID、版本 ID |  |
| skillWhitelistTemplate.referenceCount | 被引用 |  |
| skillWhitelistTemplate.referenceHint | 点击查看引用了该模板的 Agent 模板 |  |
| skillWhitelistTemplate.filterSkillId | 按 Skill ID 筛选 |  |
| skillWhitelistTemplate.filterSkillSource | 按 Skill来源筛选 |  |
| skillWhitelistTemplate.fieldRequired | 请填写{{field}} |  |
| skillWhitelistTemplate.skillSourceInvalid | 技能包 URL 须为合法的 http(s) URL，例如 https://artifacts.example.com/skills/a.zip |  |
| skillWhitelistTemplate.unsafeText | {{field}} 含有非法 HTML/脚本或疑似 SQL 注入内容，请修改后重试 |  |
| skillWhitelistTemplate.deleteConfirm | 确认删除该预置 Skill 模板？该模板可能被多个实例资源引用，请确认无副作用。 |  |
| serviceConfigTemplate.title | 运行规格 |  |
| serviceConfigTemplate.subtitle | 维护实例池参数和容器运行模板 |  |
| serviceConfigTemplate.new | 新建运行时模板 |  |
| serviceConfigTemplate.edit | 编辑运行时模板 |  |
| serviceConfigTemplate.backToList | 返回运行时模板列表 |  |
| serviceConfigTemplate.tabTemplate | 实例池参数 |  |
| serviceConfigTemplate.mainContainerBinding | 主容器模板 |  |
| serviceConfigTemplate.mainContainerBindingHint | 从容器模板目录中选择 AgentServer 主容器；容器规格在「运行时资源 → 容器模板」维护 |  |
| serviceConfigTemplate.sidecarContainerBinding | SideCar 容器模板 |  |
| serviceConfigTemplate.sidecarContainerBindingHint | 从容器模板目录中选择 Sandbox sidecar 容器，可添加多项 |  |
| serviceConfigTemplate.sidecarRowMissing | SideCar 容器模板列表存在未选项，请选择或移除 |  |
| serviceConfigTemplate.sidecarListEmpty | 暂无 SideCar 容器模板，可点击下方按钮添加 |  |
| serviceConfigTemplate.sidecarListAdd | 添加 SideCar 容器模板 |  |
| serviceConfigTemplate.sidecarListRemove | 移除 |  |
| serviceConfigTemplate.bindingPlaceholder | 请选择容器模板 |  |
| serviceConfigTemplate.bindingSummaryImage | 镜像 |  |
| serviceConfigTemplate.bindingSummaryContainerId | 容器 ID |  |
| serviceConfigTemplate.bindingNone | 未绑定 |  |
| serviceConfigTemplate.goCreateContainerTemplate | 还没有合适的容器模板？去新建 → |  |
| serviceConfigTemplate.bindingMissingWarn | 尚未绑定{{role}}容器模板 |  |
| serviceConfigTemplate.bindingNotFoundWarn | 绑定的容器模板不存在或已删除：{{id}}，请重新选择 |  |
| serviceConfigTemplate.containerId | 容器 ID |  |
| serviceConfigTemplate.containerIdConflict | 主容器与 Sandbox 的 container_id 不能相同 |  |
| serviceConfigTemplate.sandboxImage | Sandbox 镜像 |  |
| serviceConfigTemplate.ports | ports（JSON 数组） |  |
| serviceConfigTemplate.portsHint | K8s wire 格式；主容器须含 {"name":"sse","containerPort":…} 端口（Runtime SSE 路由依赖） |  |
| serviceConfigTemplate.securityContextGroup | securityContext |  |
| serviceConfigTemplate.readinessProbeGroup | readinessProbe（就绪探针） |  |
| serviceConfigTemplate.ssePortRequired | 主容器 ports 须含 name=sse 端口项（Runtime SSE 路由依赖） |  |
| serviceConfigTemplate.cpuRequest | CPU 请求量 |  |
| serviceConfigTemplate.memoryRequest | 内存请求量 |  |
| serviceConfigTemplate.cpuLimit | CPU 上限 |  |
| serviceConfigTemplate.memoryLimit | 内存上限 |  |
| serviceConfigTemplate.cpuHint | 支持 1、1.5、500m 格式（1000m = 1 核），留空不限制 |  |
| serviceConfigTemplate.memoryHint | 支持 128Mi、1Gi 格式（1024Mi = 1Gi），留空不限制 |  |
| serviceConfigTemplate.envFrom | envFrom（JSON 数组） |  |
| serviceConfigTemplate.volumeMounts | volumeMounts（JSON 数组） |  |
| serviceConfigTemplate.volumesHint | Pod 级 volumes，须被主容器或 Sandbox 的 volumeMounts 按名引用 |  |
| serviceConfigTemplate.privileged | privileged |  |
| serviceConfigTemplate.capabilitiesAdd | capabilities.add（逗号分隔） |  |
| serviceConfigTemplate.seccompType | seccompProfile.type |  |
| serviceConfigTemplate.apparmorType | appArmorProfile.type |  |
| serviceConfigTemplate.probeType | 就绪探针类型 |  |
| serviceConfigTemplate.probeTypeMainHint | 主容器探针固定为 httpGet（Runtime 渲染不变式，tcpSocket 会拒收） |  |
| serviceConfigTemplate.probePort | 探针端口 |  |
| serviceConfigTemplate.probeTimeout | 探针超时（秒） |  |
| serviceConfigTemplate.probeTimeoutMainHint | 主容器探针不支持 timeoutSeconds（Runtime 会拒收），保存时不下发该字段 |  |
| serviceConfigTemplate.templateName | 模板名称 |  |
| serviceConfigTemplate.templateDescription | 模板描述 |  |
| serviceConfigTemplate.fieldRequired | 请填写{{field}} |  |
| serviceConfigTemplate.numberOutOfRange | {{field}} 须在 {{min}}–{{max}} 之间 |  |
| serviceConfigTemplate.cpuQuantityInvalid | {{field}} 须为合法 K8s CPU 规格，如 500m、2、0.5 |  |
| serviceConfigTemplate.memoryQuantityInvalid | {{field}} 须为带单位的内存规格（Ki/Mi/Gi/K/M/G），如 512Mi、2Gi |  |
| serviceConfigTemplate.pathInvalid | {{field}} 须为合法绝对路径，如 / 或 /data/nfs |  |
| serviceConfigTemplate.unsafeText | {{field}} 含有非法 HTML/脚本或疑似 SQL 注入内容，请修改后重试 |  |
| serviceConfigTemplate.agentEnvInvalid | 环境变量须为 KEY=value 格式，每行一项 |  |
| serviceConfigTemplate.jsonArrayInvalid | {{field}} 须为合法 JSON 对象数组 |  |
| serviceConfigTemplate.searchPlaceholder | 搜索模板 ID、模板名称、模板描述、AgentServer镜像 |  |
| serviceConfigTemplate.agentImage | AgentServer镜像 |  |
| serviceConfigTemplate.mainImage | 镜像 |  |
| serviceConfigTemplate.agentImageOrContainer | AgentServer镜像或主容器 ID |  |
| serviceConfigTemplate.nodeName | 节点名称（可选） |  |
| serviceConfigTemplate.fsGroup | fsGroup（可选，Pod 卷属主） |  |
| serviceConfigTemplate.runAsUser | runAsUser（可选） |  |
| serviceConfigTemplate.runAsGroup | runAsGroup（可选） |  |
| serviceConfigTemplate.podName | Pod名称 |  |
| serviceConfigTemplate.containerName | 容器名称 |  |
| serviceConfigTemplate.containerPort | 容器端口 |  |
| serviceConfigTemplate.portName | 容器端口名称 |  |
| serviceConfigTemplate.ssePath | SSE 路径 |  |
| serviceConfigTemplate.healthPath | 健康检查路径 |  |
| serviceConfigTemplate.agentEnv | 环境变量（KEY=value，每行一项） |  |
| serviceConfigTemplate.imagePullPolicy | 镜像拉取策略 |  |
| serviceConfigTemplate.kubeconfig | K8S凭据文件路径 |  |
| serviceConfigTemplate.mainContainerId | 主容器 ID（引用） |  |
| serviceConfigTemplate.sidecarContainerIds | Sidecar 容器 ID（逗号分隔） |  |
| serviceConfigTemplate.volumes | Volumes（JSON 数组） |  |
| serviceConfigTemplate.sidecars | Sidecars 内联规格（JSON 数组，兼容旧形态） |  |
| serviceConfigTemplate.agentHostPathMounts | HostPath 挂载（JSON 数组） |  |
| serviceConfigTemplate.agentConfigmapMounts | ConfigMap 挂载（JSON 数组） |  |
| serviceConfigTemplate.agentPvcMounts | PVC 挂载（JSON 数组） |  |
| serviceConfigTemplate.readinessInitialDelay | K8S就绪探针初始延迟（秒） |  |
| serviceConfigTemplate.readinessPeriod | K8S就绪探针探测间隔（秒） |  |
| serviceConfigTemplate.readyTimeout | Gateway就绪探针超时时间（秒） |  |
| serviceConfigTemplate.readyPollInterval | Gateway就绪探针探测间隔（秒） |  |
| serviceConfigTemplate.nfsServer | NFS服务器地址 |  |
| serviceConfigTemplate.nfsPath | NFS服务器导出路径 |  |
| serviceConfigTemplate.nfsMountPath | 容器内挂载路径 |  |
| serviceConfigTemplate.minIdlePods | 最小空闲 Pod 数 |  |
| serviceConfigTemplate.podConcurrency | 单 Pod 并发上限 |  |
| serviceConfigTemplate.podTtl | 空闲 Pod 回收 TTL（秒） |  |
| serviceConfigTemplate.messageTimeout | 单次消息超时（秒） |  |
| serviceConfigTemplate.scopeConcurrency | Scope 并发数 |  |
| serviceConfigTemplate.sessionTtl | 会话TTL（秒） |  |
| serviceConfigTemplate.updatedAt | 更新时间 |  |
| serviceConfigTemplate.sectionContainer | 容器与K8S（内联） |  |
| serviceConfigTemplate.sectionContainerRef | 容器引用与挂载 |  |
| serviceConfigTemplate.sectionReadiness | 就绪探针 |  |
| serviceConfigTemplate.sectionNfs | NFS存储 |  |
| serviceConfigTemplate.sectionResources | 资源配额 |  |
| serviceConfigTemplate.sectionPool | 动态池与超时 |  |
| serviceConfigTemplate.export | 导出 |  |
| serviceConfigTemplate.import | 导入 |  |
| serviceConfigTemplate.importHint | 实例池定义文件（json格式） |  |
| serviceConfigTemplate.exportOk | 已导出配置 JSON |  |
| serviceConfigTemplate.exportFailed | 导出失败：{{detail}} |  |
| serviceConfigTemplate.importOk | 已导入并创建运行时模板 |  |
| serviceConfigTemplate.importOkFirstOfMany | 文件含 {{count}} 个模板，已导入第一个并创建运行时模板 |  |
| serviceConfigTemplate.importFailed | 导入失败：{{detail}} |  |
| serviceConfigTemplate.deleteConfirm | 确认删除该运行时模板？该模板可能被多个实例资源引用，请确认无副作用。 |  |
| policies.fieldRequired | 请填写{{field}} |  |
| policies.templateRef.title | 模板引用 |  |
| policies.templateRef.slot | 槽位 |  |
| policies.templateRef.addSlot | 添加槽位 |  |
| policies.templateRef.addRef | 添加引用 |  |
| policies.templateRef.removeSlot | 删除槽位 |  |
| policies.templateRef.removeRef | 删除引用 |  |
| policies.templateRef.empty | 尚未配置模板引用，点击下方添加槽位。 |  |
| policies.templateRef.loadingTemplates | 正在加载模板列表… |  |
| policies.templateRef.pickTemplate | 请选择模板 |  |
| policies.templateRef.modeTemplate | 模板 |  |
| policies.templateRef.modeUser | 用户映射 |  |
| policies.templateRef.modeGroup | 群组映射 |  |
| policies.templateRef.modeBot | 机器人映射 |  |
| policies.templateRef.userIdPlaceholder | 用户 ID，如 alice |  |
| policies.templateRef.groupIdPlaceholder | 群组 ID，如 g_demo_sales |  |
| policies.templateRef.botIdPlaceholder | 机器人 ID，如 bot_main |  |
| policies.templateRef.refItem | 引用项 |  |
| policies.templateRef.orLabel | or |  |
| policies.templateRef.addOrSegment | 添加 or 段 |  |
| policies.templateRef.removeOrSegment | 删除 or 段 |  |
| policies.templateRef.singleValueHint | 该槽位仅允许配置一条引用 |  |
| policies.templateRef.singleValueOnly | {{slot}} 仅允许配置一条引用 |  |
| policies.templateRef.slots.default_model | 默认模型 |  |
| policies.templateRef.slots.video_model | 视频模型 |  |
| policies.templateRef.slots.audio_model | 音频模型 |  |
| policies.templateRef.slots.vision_model | 视觉模型 |  |
| policies.templateRef.slots.image_gen_model | 生图模型 |  |
| policies.templateRef.slots.embedding_model | Embedding 模型 |  |
| policies.templateRef.slots.skill_prebuilt | 预置 Skill |  |
| policies.templateRef.slots.extension_config | 扩展配置 |  |
| policies.templateRef.slots.mcp | MCP配置 |  |
| policies.templateRef.slots.permissions | 安全护栏 |  |
| policies.templateRef.slots.a2a_access_policy | A2A 访问策略 |  |
| policies.templateRef.none | 不选择 |  |
| policies.templateRef.noOptions | 暂无可选模板 |  |
| policies.matchExpr.modeAll | 全员可用 |  |
| policies.matchExpr.modeCustom | 部分可用 |  |
| policies.matchExpr.allHint | 留空表示任意 user_id / group_id / bot_id 均命中。 |  |
| policies.matchExpr.combineOp | 连接方式 |  |
| policies.matchExpr.rootCombineHint | 同级条件之间的默认连接方式 |  |
| policies.matchExpr.nestedGroup | 子条件组 |  |
| policies.matchExpr.parenHint | （括号内） |  |
| policies.matchExpr.condition | 条件 |  |
| policies.matchExpr.orLabel | OR |  |
| policies.matchExpr.andLabel | AND |  |
| policies.matchExpr.addCondition | 添加条件 |  |
| policies.matchExpr.addSubgroupHint | 添加括号子组 |  |
| policies.matchExpr.removeGroup | 删除子组 |  |
| policies.matchExpr.removeCondition | 删除条件 |  |
| policies.matchExpr.valuePlaceholder | 值，如 g_demo_sales |  |
| policies.matchExpr.preview | 表达式预览 |  |
| policies.matchExpr.rawHint | 当前表达式无法解析为可视化条件，可继续编辑原文或重置为可视化编辑。 |  |
| policies.matchExpr.resetToVisual | 重置为可视化编辑 |  |
| policies.matchExpr.invalid | 请至少填写一条匹配条件的值 |  |
| policies.matchExpr.fields.group_id | 群组 |  |
| policies.matchExpr.fields.user_id | 用户 |  |
| policies.matchExpr.fields.bot_id | Agent |  |
| errors.loadFailed | 加载失败：{{detail}} |  |
| errors.saveFailed | 保存失败：{{detail}} |  |
| errors.deleteFailed | 删除失败：{{detail}} |  |
| errors.invalidJson | JSON 格式不正确：{{detail}} |  |
| success.saved | 保存成功 |  |
| success.deleted | 删除成功 |  |
| success.created | 创建成功 |  |
| iam.users | 用户管理 |  |
| iam.usersSubtitle | 管理用户与角色 |  |
| iam.usersSearchPlaceholder | 搜索用户 ID、显示名 |  |
| iam.orgs | 组织管理 |  |
| iam.orgsSubtitle | 管理组织与成员归属 |  |
| iam.orgsSearchPlaceholder | 搜索组织 ID、显示名 |  |
| iam.gotoOrgsLink | 前往 组织管理 页面 |  |
| iam.gotoUsersLink | 前往 用户管理 页面 |  |
| iam.bots | Agent 模板 |  |
| iam.newUser | 新建用户 |  |
| iam.batchNewUser | 批量新建 |  |
| iam.batchImport | 确认导入 |  |
| iam.batchDownloadTemplate | 下载模板 |  |
| iam.batchHint | 上传 .xlsx/.csv。列：username、password、display_name、orgs。username 仅允许英文字母/数字/下划线/连字符。无效组织自动忽略（→ 无组织）。 |  |
| iam.batchPreview | 预览（{{n}} 行） |  |
| iam.batchInvalid | {{n}} 行无效（缺用户名/密码，或用户名含非法字符） |  |
| iam.batchNoPwd | 无密码 |  |
| iam.batchSummary | 共 {{total}}，成功 {{ok}}，失败 {{failed}} |  |
| iam.editUser | 编辑用户 |  |
| iam.newOrg | 新建组织 |  |
| iam.editOrg | 编辑组织 |  |
| iam.tabBasic | 基本信息 |  |
| iam.backToOrgs | 返回组织列表 |  |
| iam.groupIdImmutableHint | 组织 ID 创建后不可修改 |  |
| iam.saveBeforeMembers | 请先保存组织，再配置成员。 |  |
| iam.fieldRequired | 请填写{{field}} |  |
| iam.builtinOrg | 系统内置 |  |
| iam.selectMemberRequired | 请至少选择一名用户 |  |
| iam.confirmRemoveMember | 将「{{name}}」移出该组织？ |  |
| iam.newBot | 新建 Agent 模板 |  |
| iam.editBot | 编辑 Agent 模板 |  |
| iam.displayName | 显示名 |  |
| iam.username | 用户ID(登录用) |  |
| iam.usernameHint | 仅英文字母、数字、下划线和连字符 |  |
| iam.idCharsetInvalid | {{field}}仅允许英文字母、数字、下划线和连字符 |  |
| iam.password | 密码 |  |
| iam.resetPassword | 重置密码(留空不改) |  |
| iam.admin | 管理员 |  |
| iam.roleAdmin | 管理员 |  |
| iam.roleUser | 普通用户 |  |
| iam.status | 状态 |  |
| iam.name | 名称 |  |
| iam.description | 描述 |  |
| iam.groupId | 组织 ID |  |
| iam.groupIdHint | 留空自动生成。仅英文字母、数字、下划线和连字符 |  |
| iam.userId | 用户 ID |  |
| iam.botId | 模板 ID |  |
| iam.role | 角色 |  |
| iam.belongOrgs | 所属组织(可多选;不选=无组织) |  |
| iam.noOrgs | 暂无组织 |  |
| iam.noUsers | 暂无用户 |  |
| iam.noBots | 暂无 Agent 模板 |  |
| iam.visibility | 授权（谁能用这个 Agent） |  |
| iam.visibilityHint | 用 match_expr 判定当前请求是否可用。全部匹配 = 该实例上已准入用户均可用。 |  |
| iam.globalAll | 全局可用(所有人) |  |
| iam.visibleOrgs | 对这些组织可见 |  |
| iam.visibleUsers | 对这些用户可见 |  |
| iam.confirmDeleteOrg | 删除组织「{{name}}」？(会解除其成员关系与可见性) |  |
| iam.confirmDeleteUser | 删除用户「{{name}}」({{id}})？ |  |
| iam.confirmDeleteBot | 删除 Agent 模板「{{name}}」？(会清除其在各实例上的授权) |  |
| iam.addUser | 添加用户 |  |
| iam.userPickerHint | 左边选组织,右边勾选其中的用户,再点「添加」 |  |
| iam.pickOrg | 选择组织… |  |
| iam.add | 添加 |  |
| iam.selectedUsers | 已选用户 |  |
| iam.noMembers | 该组织暂无用户 |  |
| iam.members | 成员 |  |
| iam.currentMembers | 当前成员 |  |
| iam.removeMember | 移出 |  |
| iam.addMember | 添加成员 |  |
| iam.searchUser | 搜索用户(用户名/ID)… |  |
| iam.noCandidates | 无匹配用户 |  |
| iam.noOrgReadonly | 「无组织」由系统自动归类(未加入任何组织的用户),不支持手动增删。 |  |
| iam.noOrgHint | 不勾选任何组织即自动归为「无组织」。 |  |
| iam.dupUsers | 这些用户已在列表中:{{names}} |  |
| iam.remove | 移除 |  |
| iam.allInstances | 全部实例 |  |
| iam.belongInstances | 所属实例 |  |
| iam.addToInstance | 添加到 {{name}} |  |
| iam.removeFromInstance | 移除出 {{name}} |  |
| iam.confirmRemoveFromInstance | 从实例「{{name}}」移除选中的 {{n}} 项？ |  |
| iam.batchWillJoin | 将同时加入实例：{{name}} |  |
| iam.batchNoInstance | 未选实例：仅创建用户，暂不加入任何实例（可选实例后再添加） |  |
| iam.bindInstanceFailed | 加入实例失败 |  |
| iam.visibilityScope | 可见范围 |  |
| iam.editVisibility | 编辑可见范围 |  |
| iam.removeFromInstanceShort | 移出 |  |
| iam.addBotToInstance | 添加 bot 到 {{name}} |  |
| iam.confirmRemoveBotFromInstance | 把「{{bot}}」移出实例「{{name}}」？ |  |
| iam.noInstanceBots | 该实例暂无 bot，点右上「添加 bot」 |  |
| iam.scopeNone | （无人可见） |  |
| iam.scopeOrgs | 部门 {{n}} |  |
| iam.scopeUsers | 个人 {{n}} |  |
| iam.botVisibilityHint | 可见范围按实例设置：在「实例」下拉选中实例后，对该 bot「编辑可见范围」。 |  |
| iam.globalAllOnInstance | 全局（该实例全员可见） |  |
| iam.noInstanceForVis | 暂无实例，无法设置可见范围。 |  |
| iam.visibilityAllInstancesHint | 授权按实例设置，请到具体实例选项卡编辑；此处仅改基本信息。 |  |
| iam.tplBinding | 模板绑定 |  |
| iam.tplBindingHint | 给该 Agent 模板在本实例上指定各槽位用哪个配置模板；留「无」则不绑（回落默认）。 |  |
| iam.tplNone | 无 |  |
| iam.tplNoOptions | 无可选模板 |  |
| iam.tpl_default_model | 默认模型 |  |
| iam.tpl_video_model | 视频模型 |  |
| iam.tpl_audio_model | 音频模型 |  |
| iam.tpl_vision_model | 视觉模型 |  |
| iam.tpl_image_gen_model | 生图模型 |  |
| iam.tpl_skill_prebuilt | 预置 Skill |  |
| iam.tpl_extension_config | 扩展配置 |  |
| iam.tpl_permissions | 安全护栏 |  |
| iam.enabled | 启用 |  |
| iam.agentTags | 标签 |  |
| iam.templateRef | 配置模板引用 |  |
| iam.templateRefHint | 本 Agent 模板引用的各槽位配置；未配置的槽位运行时回落策略默认。 |  |
| iam.roleManager | 管理面角色 |  |
| auth.loginTitle | {{productName}} 管理台登录 |  |
| auth.username | 用户名 |  |
| auth.password | 密码 |  |
| auth.login | 登录 |  |
| auth.loggingIn | 登录中… |  |
| auth.loginFailed | 登录失败,请重试。 |  |
| auth.federationFailed | 企业联合登录失败或登录请求已过期。 |  |
| auth.orFederated | 或使用企业账号 |  |
| auth.signInWith | 使用 {{name}} 登录 |  |
| auth.logout | 退出登录 |  |
| auth.backToLogin | 返回登录页 |  |
| auth.hello | 你好，{{name}} |  |
| auth.userHomeHint | 用户面建设中：后续将在这里选择组织 → 选择 bot → 进入对话(内嵌 web_enterprise)。 |  |
| auth.loading | 加载中… |  |
| auth.noClusterAccess | 当前账号没有可访问的集群，请联系管理员开通准入。 |  |
| auth.clusterSelectFailed | 选择集群失败，请重试。 |  |
| auth.errBadCredentials | 用户名或密码错误。 |  |
| auth.errDisabled | 该账户已被禁用,请联系管理员。 |  |
| auth.noPagePermission | 当前角色没有访问此管理页面的权限。 |  |
| auth.switchToUser | 进入用户面 |  |
| docs.entry | DOCS |  |
| docs.title | {{productName}} Enterprise Edition 文档 |  |
| docs.backToConsole | 返回控制台 |  |
| docs.onThisPage | 本页目录 |  |
| docs.toc.getting-started | Getting Started |  |
| docs.toc.overview | Product Overview |  |
| docs.toc.guides | 功能指南 |  |
| docs.toc.guideAgent | Agent Management |  |
| docs.toc.guidePool | Runtime Template Management |  |
| docs.toc.guideCluster | Cluster Management |  |
| userConsole.brandSub | Web Server |  |
| userConsole.myOrgs | 我的组织 |  |
| userConsole.pickOrgFirst | 请先选择一个组织进入 |  |
| userConsole.availableBots | 可用 Agent |  |
| userConsole.noBots | 该组织下暂无可用 Agent |  |
| userConsole.switchOrg | 切换组织 |  |
| userConsole.pickBot | 请选择一个 Agent |  |
| userConsole.noOrgs | 你还没有任何组织,请联系管理员 |  |
| userConsole.tabChat | 聊天 |  |
| userConsole.tabSchedule | 定时任务 |  |
| userConsole.tabSkills | 技能 |  |
| userConsole.tabMemory | 记忆 |  |
| userConsole.tabWip | 建设中… |  |
| userConsole.chatLoading | 正在加载… |  |
| userConsole.chatLoadingSlow | 加载较慢,请稍候… |  |
| agentTemplate.title | Agent模板 |  |
| agentTemplate.subtitle | 维护可复用的Agent模板，供实例绑定使用 |  |
| agentTemplate.new | 新建 Agent模板 |  |
| agentTemplate.edit | 编辑 Agent模板 |  |
| agentTemplate.templateName | 模板名称 |  |
| agentTemplate.templateDescription | 模板描述 |  |
| agentTemplate.agentTags | 标签 |  |
| agentTemplate.agentTagsHint | 逗号分隔，例如 chat,ops |  |
| agentTemplate.templateRef | 配置模板引用 |  |
| agentTemplate.templateRefHint | 本 Agent 模板引用的各槽位配置；未配置的槽位运行时回落策略默认。 |  |
| agentTemplate.customConfig | 自定义配置（JSON数组，可选） |  |
| agentTemplate.customConfigHint | 填写 JSON 对象数组，由 AgentServer 读取；引用此模板的 Agent 共用该配置。留空或填写 [] 可清空原配置。 |  |
| agentTemplate.customConfigInvalid | 请输入 JSON 对象数组，每个元素必须为对象。 |  |
| agentTemplate.searchPlaceholder | 搜索模板 ID、模板名称、模板描述、标签 |  |
| agentTemplate.updatedAt | 更新时间 |  |
| agentTemplate.fieldRequired | 请填写{{field}} |  |
| agentTemplate.deleteConfirm | 确认删除 Agent 模板「{{name}}」？会清除其在各实例上的授权。 |  |
| containerTemplate.title | 容器模板 |  |
| containerTemplate.subtitle | 维护 AgentServer 与 Sandbox 容器的可复用运行规格，供运行时模板绑定 |  |
| containerTemplate.empty | 暂无容器模板 |  |
| containerTemplate.new | 新建容器模板 |  |
| containerTemplate.edit | 编辑容器模板 |  |
| containerTemplate.searchPlaceholder | 搜索模板 ID、名称、描述、容器 ID、镜像 |  |
| containerTemplate.templateName | 模板名称 |  |
| containerTemplate.description | 模板描述 |  |
| containerTemplate.containerId | 容器 ID |  |
| containerTemplate.containerIdHint | 业务唯一键：运行时模板通过该 ID 绑定容器 |  |
| containerTemplate.containerName | 容器名称 |  |
| containerTemplate.image | 镜像 |  |
| containerTemplate.imagePullPolicy | 镜像拉取策略 |  |
| containerTemplate.ports | ports（JSON 数组） |  |
| containerTemplate.portsHint | K8s wire 格式；AgentServer 主容器须含 {"name":"sse","containerPort":…} 端口（Runtime SSE 路由依赖） |  |
| containerTemplate.agentEnv | 环境变量（KEY=value，每行一项） |  |
| containerTemplate.envFrom | envFrom（JSON 数组） |  |
| containerTemplate.volumeMounts | volumeMounts（JSON 数组） |  |
| containerTemplate.securityContextGroup | securityContext |  |
| containerTemplate.readinessProbeGroup | readinessProbe（就绪探针） |  |
| containerTemplate.runAsUser | runAsUser（可选） |  |
| containerTemplate.runAsGroup | runAsGroup（可选） |  |
| containerTemplate.privileged | privileged |  |
| containerTemplate.capabilitiesAdd | capabilities.add（逗号分隔） |  |
| containerTemplate.seccompType | seccompProfile.type |  |
| containerTemplate.apparmorType | appArmorProfile.type |  |
| containerTemplate.probeType | 就绪探针类型 |  |
| containerTemplate.probePort | 探针端口 |  |
| containerTemplate.probeTimeout | 探针超时（秒） |  |
| containerTemplate.healthPath | 健康检查路径 |  |
| containerTemplate.readinessInitialDelay | K8S就绪探针初始延迟（秒） |  |
| containerTemplate.readinessPeriod | K8S就绪探针探测间隔（秒） |  |
| containerTemplate.cpuRequest | CPU 请求量 |  |
| containerTemplate.memoryRequest | 内存请求量 |  |
| containerTemplate.cpuLimit | CPU 上限 |  |
| containerTemplate.memoryLimit | 内存上限 |  |
| containerTemplate.referenceCount | 被引用 |  |
| containerTemplate.referenceHint | 点击查看绑定了该容器模板的运行时模板 |  |
| containerTemplate.importRuntimeAndContainerHint | 导入运行时模板和容器模板 |  |
| containerTemplate.updatedAt | 修改时间 |  |
| containerTemplate.deleteConfirm | 确认删除容器模板「{{name}}」？若仍被运行时模板绑定，删除会被拒绝。 |  |
| containerTemplate.fieldRequired | 请填写{{field}} |  |
| containerTemplate.agentEnvInvalid | 环境变量须为 KEY=value 格式，每行一项 |  |
| containerTemplate.jsonArrayInvalid | {{field}} 须为合法 JSON 对象数组 |  |
| containerTemplate.cpuQuantityInvalid | {{field}} 须为合法 K8s CPU 规格，如 500m、2、0.5 |  |
| containerTemplate.memoryQuantityInvalid | {{field}} 须为带单位的内存规格（Ki/Mi/Gi/K/M/G），如 512Mi、2Gi |  |
| containerTemplate.unsafeText | {{field}} 含有非法 HTML/脚本或疑似 SQL 注入内容，请修改后重试 |  |
| roles.title | 角色管理 |  |
| roles.subtitle | 配置角色、权限和授权 |  |
| roles.new | 新建角色 |  |
| roles.edit | 编辑角色 |  |
| roles.backToList | 返回角色列表 |  |
| roles.name | 角色名称 |  |
| roles.description | 角色描述 |  |
| roles.roleId | 角色 ID |  |
| roles.roleIdImmutableHint | （创建后不可修改） |  |
| roles.roleIdHint | 仅英文字母、数字、下划线和连字符 |  |
| roles.scope | 角色类型 |  |
| roles.permissions | 权限 |  |
| roles.assignees | 已指派用户 |  |
| roles.system | 系统角色 |  |
| roles.builtin | 内置 |  |
| roles.systemHint | 系统预置角色，权限与启用状态不可修改 |  |
| roles.confirmDelete | 删除角色「{{name}}」({{id}})？相关用户指派将一并清除。 |  |
| roles.searchPlaceholder | 搜索角色 ID、角色名称、角色描述 |  |
| roles.searchPermissionPlaceholder | 搜索权限 ID、名称、描述 |  |
| roles.assign | 指派用户 |  |
| roles.assignTitle | 为“{{name}}”指派用户 |  |
| roles.assigneesSearchPlaceholder | 搜索用户 ID、授权人 |  |
| roles.addAssignee | 添加授权 |  |
| roles.editAssignee | 编辑授权 |  |
| roles.addAssigneeHint | 勾选用户后填写过期时间，点击确认一次性保存 |  |
| roles.selectAllVisible | 全选当前结果 |  |
| roles.selectedCount | 已选 {{count}} 人 |  |
| roles.selectAssigneeRequired | 请至少勾选一名用户 |  |
| roles.confirmRemoveAssignee | 移除用户「{{userId}}」的角色授权？ |  |
| roles.assigneeAlreadyExists | 该用户已拥有此角色授权 |  |
| roles.grantedBy | 授权人 |  |
| roles.expiresAt | 过期时间 |  |
| roles.neverExpires | 长期有效 |  |
| roles.expiresHint | 留空表示长期有效 |  |
| roles.expiresInvalid | 过期时间格式无效 |  |
| roles.expiresMustBeFuture | 过期时间必须晚于当前时间 |  |
| roles.createdAt | 创建时间 |  |
| roles.noAssignees | 该角色暂未指派用户 |  |
| roles.noPermissions | 无权限 |  |
| roles.noScopePermissions | 当前角色类型还没有可选权限码 |  |
| roles.systemPermissionsLocked | 系统角色的权限和启用状态不可修改。 |  |
| roles.tabBasic | 基本信息 |  |
| roles.tabPermissions | 权限点 |  |
| roles.tabAssignees | 用户授权 |  |
| roles.saveBeforeAssign | 请先保存角色，再配置用户授权。 |  |
| roles.fieldRequired | 请填写{{field}} |  |
| roles.permissionGroups.quota | 配额 |  |
| roles.permissionGroups.approval | 审批 |  |
| roles.permissionGroups.iam | 角色管理 |  |
| roles.permissionGroups.other | 其他 |  |
| roles.scopes.admin | admin |  |
| roles.scopes.org | org |  |
| roles.scopes.user | user |  |
| approvals.title | 审批中心 |  |
| approvals.subtitle | 查看和处理平台各类审批申请 |  |
| approvals.searchPlaceholder | 搜索审批单号、标题或申请说明 |  |
| approvals.businessType | 审批类型 |  |
| approvals.subjectTitle | 标题 |  |
| approvals.types.workspace_quota_expand | 配额审批 |  |
| approvals.orderNum | 审批单号 |  |
| approvals.applicant | 申请人 |  |
| approvals.approver | 审批人 |  |
| approvals.subject | 审批对象 |  |
| approvals.cluster | 集群 |  |
| approvals.group | 组织 |  |
| approvals.bot | Bot |  |
| approvals.workspace | 工作区 |  |
| approvals.createdAt | 申请时间 |  |
| approvals.noOrg | 无组织 |  |
| approvals.noApprover | 无审批人 |  |
| approvals.detailTitle | 审批单 {{orderNum}} |  |
| approvals.backToList | 返回审批列表 |  |
| approvals.sectionBasic | 基本信息 |  |
| approvals.sectionApply | 申请内容 |  |
| approvals.sectionDecision | 审批处理 |  |
| approvals.finishedAt | 办结时间 |  |
| approvals.usagePercent | 用量占比 {{percent}}% |  |
| approvals.policyId | 策略 ID |  |
| approvals.effectedLimit | 生效配额 |  |
| approvals.syncStatus | 同步状态 |  |
| approvals.syncDetail | 同步详情 |  |
| approvals.commentPlaceholder | 可选填写审批意见 |  |
| approvals.currentLimit | 当前配额 |  |
| approvals.requestedLimit | 申请配额 |  |
| approvals.used | 当前用量 |  |
| approvals.reason | 申请说明 |  |
| approvals.comment | 审批意见 |  |
| approvals.approve | 通过 |  |
| approvals.reject | 驳回 |  |
| approvals.approved | 审批已通过 |  |
| approvals.rejected | 审批已驳回 |  |
| approvals.retrySync | 重试生效 |  |
| approvals.effectResult | 生效结果 |  |
| approvals.records | 操作记录 |  |
| approvals.status.pending | 待审批 |  |
| approvals.status.approved | 已通过 |  |
| approvals.status.rejected | 已驳回 |  |
| approvals.status.cancelled | 已撤回 |  |
| approvals.actions.approve | 通过 |  |
| approvals.actions.reject | 驳回 |  |
| approvals.actions.cancel | 撤回 |  |
| approvals.sync.success | 已同步到 Gateway |  |
| approvals.sync.failed | Gateway 同步失败 |  |
| redesign.advancedDeployment | 高级部署设置 |  |
| redesign.capacityTimeout | 容量与超时 |  |
| redesign.connected | 组件连接正常 |  |
| redesign.inspect | 连接状态待检查 |  |
| redesign.inspectInstance | 检查实例 |  |
| a2aManagement.title | A2A管理 |  |
| a2aManagement.subtitle | 需要 Agent 间协作时配置 A2A 能力。 |  |

## 直接写在组件中的中文

### pages/AgentConnectPage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 74 | Agent 管理 |  |
| 74 | 配置 Agent 使用范围 |  |
| 76 | 无法暂存草稿，离开前请保存配置。 |  |
| 76 | 清除草稿 |  |
| 76 | 输入已暂存于当前标签页，返回此 Agent 可继续。尚未提交到服务器。 |  |
| 77 | 清除尚未保存的绑定配置？此操作会清空名称、实例和授权条件。 |  |
| 77 | 确认清除 |  |
| 77 | 继续编辑 |  |
| 78 | 授权范围 |  |
| 78 | 检查并保存 |  |
| 78 | 选择环境 |  |
| 79 | 查看实例配置 |  |
| 79 | 绑定配置已保存 |  |
| 79 | 这不代表运行时已经生效。请继续检查实例准入、运行规格及组件状态。 |  |
| 80 | 实例中的 Agent 名称 |  |
| 80 | 暂无服务实例，请先在运行环境中接入实例。 |  |
| 80 | 正在读取服务实例… |  |
| 80 | 草稿中的实例已不可用，请重新选择。 |  |
| 80 | 请选择 |  |
| 80 | 运行环境 |  |
| 80 | 选择服务实例 |  |
| 81 | 全员指此实例中已获准入的用户，不会自动开通实例准入。 |  |
| 81 | 谁可以使用 |  |
| 82 | 实例内已准入的全体用户 |  |
| 82 | 授权表达式 |  |
| 82 | 服务实例 |  |
| 82 | 本操作只保存 Agent 绑定。运行规格匹配和最终有效权限仍需在实例中核对。 |  |
| 82 | 正在检查实例配置… |  |
| 82 | 确认绑定配置 |  |
| 82 | 资源名称 |  |
| 83 | 重试检查 |  |
| 84 | 再次保存会新增一条绑定，不会覆盖已有授权。 |  |
| 84 | 同一 Agent 已有绑定： |  |
| 84 | 实例已有启用的运行规格，仍需核对授权条件能否命中。 |  |
| 84 | 实例没有启用的运行规格。可以先保存绑定，但还不能确认 Agent 可运行。 |  |
| 86 | 此 Agent 定义已停用，请先启用。 |  |
| 88 | 上一步 |  |
| 88 | 下一步 |  |
| 88 | 保存绑定配置 |  |
| 88 | 正在保存… |  |

### pages/AgentDetailPage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 22 | 正在读取 Agent… |  |
| 23 | 重试 |  |
| 29 | Agent 管理 |  |
| 29 | 尚未填写描述 |  |
| 30 | 刷新 |  |
| 30 | 绑定服务实例 |  |
| 30 | 编辑定义 |  |
| 32 | 定义状态 |  |
| 32 | 已停用 |  |
| 32 | 已启用 |  |
| 32 | 技术标识 |  |
| 32 | 更新时间 |  |
| 32 | 标签 |  |
| 33 | 定义引用，不代表运行时已加载 |  |
| 33 | 能力与安全策略 |  |
| 34 | 正在解析资源… |  |
| 36 | 没有配置资源引用 |  |
| 37 | 待检查 |  |
| 37 | 打开资源目录 |  |
| 37 | 资源读取失败： |  |
| 39 | 以下为已保存的绑定。实例准入和运行规格需要分别检查。 |  |
| 39 | 实例绑定与使用范围 |  |
| 40 | 正在检查实例绑定… |  |
| 42 | 尚未绑定服务实例 |  |
| 43 | 实例内已准入的全体用户 |  |
| 43 | 已过期： |  |
| 43 | 有效期格式异常： |  |
| 43 | 有效期至： |  |
| 43 | 查看授权条件 |  |
| 43 | 管理绑定 |  |
| 43 | 绑定已停用 |  |
| 43 | 绑定已启用 |  |
| 43 | 长期有效 |  |

### pages/AgentWorkspacePage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 27 | Agent 管理 |  |
| 27 | 新建 Agent |  |
| 27 | 模板目录与导入 |  |
| 27 | 管理 Agent 定义、能力配置与实例使用范围。 |  |
| 28 | Agent 列表 |  |
| 29 | 全部状态 |  |
| 29 | 刷新 |  |
| 29 | 定义状态 |  |
| 29 | 已停用 |  |
| 29 | 已启用 |  |
| 29 | 搜索 Agent |  |
| 29 | 搜索名称、描述或标签 |  |
| 30 | Agent 名称 |  |
| 30 | 先选择模型与工具，再配置服务实例中的使用范围。 |  |
| 30 | 创建 Agent |  |
| 30 | 创建第一个 Agent |  |
| 30 | 尚未填写描述 |  |
| 30 | 尝试其他关键词或清除筛选条件。 |  |
| 30 | 操作 |  |
| 30 | 更新时间 |  |
| 30 | 查看详情 |  |
| 30 | 正在读取 Agent… |  |
| 30 | 没有匹配的 Agent |  |
| 30 | 清除筛选 |  |
| 30 | 能力配置 |  |
| 30 | 配置使用范围 |  |
| 33 | Agent 摘要 |  |
| 33 | 关闭 |  |
| 33 | 启用状态不代表运行可用。打开完整详情查看资源名称、资源状态和实例绑定。 |  |
| 33 | 打开完整详情 |  |
| 33 | 标签 |  |
| 33 | 编辑定义 |  |

### pages/DocsPage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 31 | 快速开局 |  |
| 32 | 产品总览 |  |
| 33 | Agent模板管理 |  |
| 34 | 运行时模板管理 |  |
| 35 | 集群管理 |  |

### pages/OverviewPage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 22 | 准备模型与工具 |  |
| 22 | 连接模型，配置技能和外部工具。 |  |
| 23 | 定义 Agent |  |
| 23 | 组合可复用能力和工具安全策略。 |  |
| 24 | 接入服务实例，配置容器与运行规格。 |  |
| 24 | 接入运行环境 |  |
| 25 | 先配置成员/组织准入，再绑定 Agent、运行时及授权条件。 |  |
| 25 | 配置使用范围 |  |
| 28 | 从服务连接到 Agent 使用，掌握配置与运行环境。 |  |
| 28 | 管理总览 |  |
| 29 | 刷新状态 |  |
| 31 | 仅表示管理服务可访问 |  |
| 31 | 状态未知 |  |
| 31 | 管理 API |  |
| 31 | 连接失败 |  |
| 31 | 连接正常 |  |
| 32 | 已接入管理的服务环境 |  |
| 32 | 服务实例 |  |
| 33 | 组件连接正常 |  |
| 34 | 存在离线或未知组件 |  |
| 34 | 待检查实例 |  |
| 36 | 无法读取实例： |  |
| 37 | 探活状态不代表 Agent 请求成功；进入实例进一步检查配置。 |  |
| 37 | 服务环境 |  |
| 37 | 管理实例 |  |
| 38 | 先准备能力资源，再接入环境并配置 Agent 使用范围。 |  |
| 38 | 名称 |  |
| 38 | 接入服务实例 |  |
| 38 | 操作 |  |
| 38 | 检查 |  |
| 38 | 正在读取服务状态… |  |
| 38 | 还没有接入服务实例 |  |
| 40 | 让 Agent 可用的四个步骤 |  |
| 40 | 配置路径 |  |

### pages/iam/UsersPage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 375 | 张三 |  |

### pages/instance/InstanceListPage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 539 | 组件连接正常 |  |
| 539 | 连接状态待检查 |  |
| 541 | 检查实例 |  |

### pages/instance/instanceAccessPanel/instanceBinding.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 19 | 添加到实例 |  |
| 87 | 确定 |  |
| 94 | 搜索 |  |
| 108 | 没有可添加的候选（都已在该实例） |  |
| 115 | 登录权限 |  |
| 118 | 拒绝优先级高于允许 |  |
| 128 | 允许 |  |
| 131 | 拒绝 |  |
| 137 | 过期时间 |  |
| 147 | 留空表示永不过期 |  |

### pages/templates/A2AAccessPoliciesPage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 27 | 操作成功 |  |
| 31 | 搜索策略名称或描述 |  |
| 32 | 全部模式 |  |
| 33 | 全部状态 |  |
| 33 | 已停用 |  |
| 33 | 已启用 |  |
| 34 | + 新建访问策略 |  |
| 34 | 刷新 |  |
| 37 | 加载中… |  |
| 37 | 引用数 |  |
| 37 | 成员数 |  |
| 37 | 操作 |  |
| 37 | 模式 |  |
| 37 | 版本 / 更新时间 |  |
| 37 | 状态 |  |
| 37 | 策略 |  |
| 38 | 暂无访问策略 |  |
| 39 | 点击查看引用了该策略的 Agent 模板 |  |
| 39 | 白名单 |  |
| 39 | 黑名单 |  |
| 41 | 删除 |  |
| 41 | 编辑 |  |
| 45 | 确认删除该访问策略？ |  |

### pages/templates/A2AAccessPolicyModal.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 57 | 保存成功 |  |
| 62 | 新建访问策略 |  |
| 62 | 编辑访问策略 |  |
| 63 | 保存 |  |
| 65 | 策略名称 |  |
| 66 | 白名单 |  |
| 66 | 策略模式 |  |
| 66 | 黑名单 |  |
| 67 | 描述 |  |
| 68 | 启用策略 |  |
| 69 | A2A Agent 成员 |  |
| 70 | 空白名单将拒绝访问全部 A2A Agent。 |  |
| 70 | 空黑名单将允许访问全部已注册 A2A Agent；停用状态会在执行时另行过滤。 |  |
| 71 | 搜索 A2A Agent |  |
| 71 | 搜索 Agent 名称或 ID |  |
| 73 | 暂无已注册 A2A Agent |  |
| 73 | 未找到匹配的 A2A Agent |  |

### pages/templates/A2ADiscoverySettingsModal.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 49 | 网络访问设置已保存 |  |
| 61 | 网络访问设置 |  |
| 63 | 保存 |  |
| 66 | 统一控制 A2A 注册、凭据同步和调用。 |  |
| 68 | 使用 HTTP 时，还需开启对应的内网或公网开关。 |  |
| 69 | 允许 HTTP 地址 |  |
| 72 | 允许 Agent 和 Gateway 使用 10.x、172.16–31.x、192.168.x 地址。 |  |
| 73 | 允许内网地址 |  |
| 76 | 需同时开启“允许 HTTP 地址”。 |  |
| 77 | 允许公网 HTTP |  |
| 79 | HTTP 会明文传输凭据，仅用于可信网络。回环地址始终禁止。 |  |

### pages/templates/A2AManagementPage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 20 | A2A 管理 |  |

### pages/templates/A2AOutboundTemplateModal.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 91 | 保存成功 |  |
| 101 | 发现并注册 A2A Agent |  |
| 101 | 编辑 A2A Agent |  |
| 102 | 保存 |  |
| 104 | Agent Base URL 或 Card URL |  |
| 104 | 发现 |  |
| 105 | 未知版本 |  |
| 106 | 显示名称 |  |
| 107 | 标签（逗号分隔） |  |
| 108 | 描述 |  |
| 109 | 凭据 |  |
| 109 | 清除已保存凭据 |  |
| 110 | 连接超时（秒） |  |
| 111 | 同步等待（秒） |  |

### pages/templates/A2AOutboundTemplatesPage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 35 | 操作成功 |  |
| 42 | A2A 管理 |  |
| 42 | 发现、注册并维护企业可用的出站 A2A Agent |  |
| 43 | + 注册 A2A Agent |  |
| 43 | 刷新 |  |
| 43 | 搜索名称、地址或标签 |  |
| 43 | 网络访问设置 |  |
| 47 | 加载中… |  |
| 48 | Card 版本 |  |
| 48 | 地址 / 接口 |  |
| 48 | 引用数 |  |
| 48 | 操作 |  |
| 48 | 最近检查 |  |
| 48 | 状态 |  |
| 49 | 暂无 A2A Agent |  |
| 52 | 待确认 |  |
| 54 | 点击查看生效策略覆盖了该 Agent 的模板 |  |
| 56 | 删除 |  |
| 56 | 拒绝 |  |
| 56 | 接受 |  |
| 56 | 检查 Card |  |
| 56 | 编辑 |  |
| 63 | 确认删除该 A2A Agent？ |  |

### pages/templates/ServiceConfigTemplatesPage.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 161 | 服务器未找到 agentserver.json，请检查部署工具目录配置。 |  |
| 161 | 服务端未配置部署工具目录；请使用本地文件导入。 |  |
| 269 | 从 Manager 服务端读取部署期间生成的 agentserver.json |  |
| 270 | 从服务器发现 |  |

### components/GuideLink.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 6 | 跳转到对应页面 |  |

### components/MatchExprEditor.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 162 | 搜索... |  |
| 174 | 无匹配项 |  |

### components/SetupJourney.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 24 | 使用范围 |  |
| 24 | 准备资源 |  |
| 24 | 定义 Agent |  |
| 24 | 运行环境 |  |
| 27 | 可选 |  |
| 27 | 必需 |  |
| 30 | 创建或编辑 Agent |  |
| 30 | 明确用途，选择本次使用的默认模型、技能与安全策略。 |  |
| 32 | 接入服务实例 |  |
| 32 | 检查 Gateway、Runtime 和 User Web 的连接状态。 |  |
| 33 | 准备容器模板 |  |
| 33 | 按部署方式检查容器配置。 |  |
| 34 | 配置运行规格 |  |
| 34 | 配置运行资源，并在目标实例中检查运行规格绑定。 |  |
| 37 | 创建可登录用户，之后才能登录用户面进行对话。 |  |
| 37 | 新建成员 |  |
| 38 | 新建组织并绑定成员 |  |
| 38 | 组织用于集群准入和授权范围。 |  |
| 39 | 绑定 Agent |  |
| 39 | 选择 Agent → 配置使用范围，选择目标实例和授权条件。 |  |
| 40 | 检查准入与运行条件 |  |
| 40 | 进入目标实例核对用户准入、运行规格和组件状态；保存绑定不代表执行成功。 |  |
| 43 | Agent 配置引导 |  |
| 45 | 展开更多资源配置 |  |
| 45 | 当前页面 |  |
| 45 | 按本次 Agent 的用途选择资源；此引导不会自动判定配置完成。 |  |
| 45 | 收起更多资源配置 |  |
| 45 | 继续： |  |
| 45 | 配置阶段 |  |

### components/Sidebar.tsx

| 源码行 | 当前中文字符串 | 修改后 |
| --- | --- | --- |
| 15 | 工作台 |  |
| 16 | Agent 管理 |  |
| 16 | 总览 |  |
| 18 | 资源中心 |  |
| 19 | 运行环境 |  |
| 20 | 服务实例 |  |
| 20 | 运行规格 |  |
| 21 | 容器模板 |  |
| 23 | 安全与访问 |  |
| 24 | 用户 |  |
| 24 | 组织 |  |
| 25 | 角色与权限 |  |
| 27 | 审批中心 |  |
| 27 | 运营 |  |
| 30 | 主导航 |  |
| 38 | JiuwenSwarm 管理工作台 |  |

