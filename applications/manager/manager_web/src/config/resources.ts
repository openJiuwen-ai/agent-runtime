/** Shared display order and terminology for the resource catalog and setup guide. */
export const resourceCatalog = [
  { path: '/model-templates', key: 'modelTemplate', guide: '模型', guideEn: 'Models', required: true },
  { path: '/skill-prebuilt-templates', key: 'skillWhitelistTemplate', guide: '技能', guideEn: 'Skills', required: false },
  { path: '/mcp-templates', key: 'mcpTemplate', guide: 'MCP', guideEn: 'MCP', required: false },
  { path: '/safety-guardrails', key: 'safetyGuardrails', guide: '工具安全', guideEn: 'Tool safety', required: false },
  { path: '/embedding-templates', key: 'embeddingTemplate', guide: '向量模型', guideEn: 'Embedding models', required: false },
  { path: '/a2a-management', key: 'a2aManagement', guide: 'A2A', guideEn: 'A2A', required: false },
  { path: '/expert-packages', key: 'expertPackages', guide: '专家与专家包', guideEn: 'Experts & packages', required: false },
  { path: '/extension-config-templates', key: 'extensionTemplate', guide: '扩展配置', guideEn: 'Extensions', required: false },
];
