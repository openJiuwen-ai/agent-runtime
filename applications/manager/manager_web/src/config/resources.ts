/** Shared display order and terminology for the Agent resource catalog and setup guide. */
export const resourceCatalog = [
  { path: '/model-templates', key: 'modelTemplate', guide: '模型', guideEn: 'Models', required: true },
  { path: '/skill-prebuilt-templates', key: 'skillWhitelistTemplate', guide: '预置技能', guideEn: 'Preset skills', required: false },
  { path: '/mcp-templates', key: 'mcpTemplate', guide: 'MCP服务', guideEn: 'MCP services', required: false },
  { path: '/safety-guardrails', key: 'safetyGuardrails', guide: '工具安全策略', guideEn: 'Tool safety policies', required: false },
  { path: '/execution-guard-templates', key: 'executionGuardrails', guide: '执行护栏', guideEn: 'Execution guard', required: false },
  { path: '/advanced-config-templates', key: 'advancedConfigTemplates', guide: '高级配置', guideEn: 'Advanced config', required: false },
  { path: '/embedding-templates', key: 'embeddingTemplate', guide: '向量模型', guideEn: 'Embedding models', required: false },
  { path: '/a2a-management', key: 'a2aManagement', guide: 'A2A管理', guideEn: 'A2A management', required: false },
  { path: '/expert-package-templates', key: 'expertPackageTemplate', guide: '专家包', guideEn: 'Expert packages', required: false },
  { path: '/extension-config-templates', key: 'extensionTemplate', guide: '扩展配置', guideEn: 'Extensions', required: false },
];
