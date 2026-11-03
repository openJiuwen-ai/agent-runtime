import type { InstanceDetail, InstanceSummary } from '../types';
import type { InstanceAgentResource } from '../services/api';

/**
 * 本地 UI 预览数据。它只在管理面没有真实服务实例时展示，所有字段都明确标记为演示数据，
 * 不会调用创建接口，也不会写入 Manager 数据库。
 */
export const MOCK_INSTANCE_ID = 'mock-cluster-demo';

export const mockInstance: InstanceSummary = {
  jiuwenclaw_id: MOCK_INSTANCE_ID,
  jiuwenclaw_name: '测试集群 · 本地演示',
  namespace: 'manager-demo',
  space_id: 'local-demo',
  gateway_host: 'http://demo-gateway.invalid',
  gateway_status: 'online',
  gateway_last_alive: new Date().toISOString(),
  runtime_host: 'http://demo-runtime.invalid',
  runtime_status: 'online',
  runtime_last_alive: new Date().toISOString(),
  user_web_status: 'online',
  user_web_last_alive: new Date().toISOString(),
  user_web_host: 'http://demo-user-web.invalid',
  gateway_web_http_host: 'http://demo-gateway.invalid',
  gateway_web_ws_host: 'http://demo-gateway.invalid',
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

export const mockInstanceDetail: InstanceDetail = {
  ...mockInstance,
  description: '用于查看管理面低保真实现效果的本地演示集群。',
  created_by: 'local-demo',
  updated_by: 'local-demo',
  data: { demo: true },
};

export const mockAgentResources: InstanceAgentResource[] = [
  {
    id: 1,
    jiuwenclaw_id: MOCK_INSTANCE_ID,
    resource_id: 'mock-agent-customer-service',
    resource_name: '客服助手',
    resource_desc: '用于验证 Agent 列表、授权范围和集群配置工作台。',
    ref_template_id: 'mock-agent-customer-service',
    match_expr: '实例内已准入的全体用户',
    granted_by: 'local-demo',
    expires_at: null,
    enabled: true,
    data: { demo: true },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
];

export function isMockInstanceId(instanceId: string): boolean {
  return instanceId === MOCK_INSTANCE_ID;
}
