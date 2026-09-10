export type ModelTypeValue = string[];

export interface ModelTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  model_type: ModelTypeValue;
  model_tags?: string[] | null;
  api_base: string;
  api_key: string;
  model_id: string;
  model_provider: string;
  parameters?: Record<string, unknown> | null;
  timeout: number;
  retry_count: number;
  enable_streaming: boolean;
  enable_function_calling: boolean;
  verify_ssl: boolean;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ModelTemplateCreateBody {
  template_name: string;
  description?: string;
  model_type: ModelTypeValue;
  model_tags?: string[];
  api_base: string;
  api_key: string;
  model_id: string;
  model_provider: string;
  parameters?: Record<string, unknown>;
  timeout?: number;
  retry_count?: number;
  enable_streaming?: boolean;
  enable_function_calling?: boolean;
  verify_ssl?: boolean;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ModelTemplateUpdateBody = Partial<ModelTemplateCreateBody>;

export interface EmbeddingTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  embed_tags?: string[] | null;
  api_base: string;
  api_key: string;
  model_id: string;
  model_provider: string;
  parameters?: Record<string, unknown> | null;
  client_config?: Record<string, unknown> | null;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface EmbeddingTemplateCreateBody {
  template_name: string;
  description?: string;
  embed_tags?: string[];
  api_base: string;
  api_key: string;
  model_id: string;
  model_provider: string;
  parameters?: Record<string, unknown>;
  client_config?: Record<string, unknown>;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type EmbeddingTemplateUpdateBody = Partial<EmbeddingTemplateCreateBody>;

/** 与设计文档 hook_config 字段说明一致 */
export interface HookConfig {
  handler: string;
  params?: Record<string, unknown>;
  schedule?: string;
  data?: Record<string, unknown>;
}

export interface ExtensionConfigTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  component: string;
  hook_type: string;
  hook_config: HookConfig;
  custom_config?: Record<string, unknown> | null;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ExtensionConfigTemplateCreateBody {
  template_name: string;
  description?: string;
  component: string;
  hook_type: string;
  hook_config: HookConfig;
  custom_config?: Record<string, unknown>;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ExtensionConfigTemplateUpdateBody = Partial<ExtensionConfigTemplateCreateBody>;

export interface SkillPrebuiltTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  skill_id: string;
  package_url?: string | null;
  source_id?: string | null;
  version_id?: string | null;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SkillPrebuiltTemplateCreateBody {
  template_name: string;
  description?: string;
  skill_id: string;
  package_url?: string;
  source_id?: string;
  version_id?: string;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type SkillPrebuiltTemplateUpdateBody = Partial<
  Omit<SkillPrebuiltTemplateCreateBody, 'package_url'>
> & {
  package_url?: string | null;
};


export interface PermissionsTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  enabled: boolean;
  body: Record<string, unknown>;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PermissionsTemplateCreateBody {
  template_name: string;
  description?: string;
  enabled?: boolean;
  body: Record<string, unknown>;
  data?: Record<string, unknown>;
}

export type PermissionsTemplateUpdateBody = Partial<PermissionsTemplateCreateBody>;

export interface ServiceConfigTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  agent_image: string;
  namespace: string;
  node_name?: string | null;
  run_as_user?: number | null;
  run_as_group?: number | null;
  pod_name: string;
  container_name: string;
  container_port: number;
  port_name: string;
  sse_port: number;
  sse_path: string;
  health_path: string;
  agent_env?: Record<string, string> | null;
  image_pull_policy: string;
  kubeconfig?: string | null;
  readiness_initial_delay: number;
  readiness_period: number;
  ready_timeout: number;
  ready_poll_interval: number;
  nfs_server?: string | null;
  nfs_path?: string | null;
  nfs_mount_path?: string | null;
  agent_cpu_request?: string | null;
  agent_memory_request?: string | null;
  agent_cpu_limit?: string | null;
  agent_memory_limit?: string | null;
  sidecars?: Record<string, unknown>[] | null;
  agent_host_path_mounts?: Record<string, unknown>[] | null;
  agent_configmap_mounts?: Record<string, unknown>[] | null;
  agent_pvc_mounts?: Record<string, unknown>[] | null;
  main_container_id?: string | null;
  sidecar_container_ids?: string[] | null;
  volumes?: Record<string, unknown>[] | null;
  min_idle_services: number;
  service_concurrency: number;
  service_ttl: number;
  message_timeout: number;
  session_concurrency: number;
  session_ttl: number;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ServiceConfigTemplateCreateBody {
  template_name: string;
  description?: string;
  agent_image?: string;
  namespace?: string;
  node_name?: string;
  run_as_user?: number | null;
  run_as_group?: number | null;
  pod_name?: string;
  container_name?: string;
  container_port?: number;
  port_name?: string;
  sse_port?: number;
  sse_path?: string;
  health_path?: string;
  agent_env?: Record<string, string>;
  image_pull_policy?: string;
  kubeconfig?: string;
  readiness_initial_delay?: number;
  readiness_period?: number;
  ready_timeout?: number;
  ready_poll_interval?: number;
  nfs_server?: string;
  nfs_path?: string;
  nfs_mount_path?: string;
  agent_cpu_request?: string;
  agent_memory_request?: string;
  agent_cpu_limit?: string;
  agent_memory_limit?: string;
  sidecars?: Record<string, unknown>[];
  agent_host_path_mounts?: Record<string, unknown>[];
  agent_configmap_mounts?: Record<string, unknown>[];
  agent_pvc_mounts?: Record<string, unknown>[];
  main_container_id?: string;
  sidecar_container_ids?: string[];
  volumes?: Record<string, unknown>[];
  min_idle_services?: number;
  service_concurrency?: number;
  service_ttl?: number;
  message_timeout?: number;
  session_concurrency?: number;
  session_ttl?: number;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ServiceConfigTemplateUpdateBody = Partial<ServiceConfigTemplateCreateBody>;

export interface A2AOutboundTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  a2a_tags?: string[] | null;
  source_url: string;
  card_path: string;
  agent_card: Record<string, unknown>;
  card_fingerprint: string;
  card_revision: number;
  selected_interface: Record<string, unknown>;
  credential_configured: boolean;
  connect_timeout_seconds: number;
  sync_wait_seconds: number;
  enabled: boolean;
  pending_revision?: Record<string, unknown> | null;
  last_checked_at?: string | null;
  last_error_code?: string | null;
  last_error_summary?: string | null;
  updated_at?: string | null;
}

export interface A2ADiscoveryCandidate {
  discovery_id: string;
  expires_at: string;
  source_url: string;
  card_path: string;
  card_url: string;
  card_fingerprint: string;
  agent_card: Record<string, unknown>;
  selected_interface: Record<string, unknown>;
}

export interface A2ADiscoverySettings {
  allow_http: boolean;
  allow_private_network: boolean;
  allow_public_http: boolean;
}

export interface A2AOutboundTemplateCreateBody {
  discovery_id: string;
  template_name: string;
  description?: string;
  a2a_tags?: string[];
  credential?: string;
  connect_timeout_seconds?: number;
  sync_wait_seconds?: number;
  enabled?: boolean;
}

export interface A2AOutboundTemplateUpdateBody {
  template_name?: string;
  description?: string;
  a2a_tags?: string[];
  credential?: string;
  clear_credential?: boolean;
  connect_timeout_seconds?: number;
  sync_wait_seconds?: number;
  enabled?: boolean;
}

export type A2AAccessPolicyMode = 'allowlist' | 'denylist';

export interface A2AAccessPolicyTemplate {
  id: number;
  policy_id: string;
  policy_name: string;
  description?: string | null;
  mode: A2AAccessPolicyMode;
  member_template_ids: string[];
  enabled: boolean;
  revision: number;
  reference_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface A2AAccessPolicyTemplateBody {
  policy_name: string;
  description?: string;
  mode: A2AAccessPolicyMode;
  member_template_ids: string[];
  enabled?: boolean;
}

/** 容器规格（service_config_container），供模板引用。 */
export interface ServiceConfigContainer {
  id: number;
  container_id: string;
  name: string;
  image: string;
  image_pull_policy: string;
  ports?: unknown[] | null;
  env?: unknown[] | null;
  env_from?: unknown[] | null;
  resources?: Record<string, unknown> | null;
  volume_mounts?: unknown[] | null;
  security_context?: Record<string, unknown> | null;
  readiness_probe?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}
