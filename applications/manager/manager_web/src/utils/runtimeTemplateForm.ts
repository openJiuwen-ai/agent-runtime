import type { ServiceConfigTemplate, ServiceConfigTemplateCreateBody } from '../types';

export type RuntimeTemplateForm = {
  template_name: string;
  enabled: boolean;
  description: string;
  namespace: string;
  node_name: string;
  fs_group: string;
  pod_name: string;
  sse_path: string;
  kubeconfig: string;
  ready_timeout: number;
  ready_poll_interval: number;
  min_idle_pods: number;
  pod_concurrency: number;
  pod_ttl: number;
  message_timeout: number;
  scope_concurrency: number;
  session_ttl: number;
  volumes_json: string;
  main_container_id: string;
  sidecar_ids: string[];
};

/** The same mapping is used for saved templates and deployment-file previews. */
export function runtimeTemplateForm(template?: ServiceConfigTemplateCreateBody | ServiceConfigTemplate): RuntimeTemplateForm {
  return {
    template_name: template?.template_name ?? '',
    enabled: template?.enabled ?? true,
    description: template?.description ?? '',
    namespace: template?.namespace ?? 'default',
    node_name: template?.node_name ?? '',
    fs_group: template?.fs_group != null ? String(template.fs_group) : '',
    pod_name: template?.pod_name ?? 'agentserver',
    sse_path: template?.sse_path ?? '/api/v1/events/stream',
    kubeconfig: template?.kubeconfig ?? '',
    ready_timeout: template?.ready_timeout ?? 300,
    ready_poll_interval: template?.ready_poll_interval ?? 2,
    min_idle_pods: template?.min_idle_pods ?? 0,
    pod_concurrency: template?.pod_concurrency ?? 2,
    pod_ttl: template?.pod_ttl ?? 300,
    message_timeout: template?.message_timeout ?? 600,
    scope_concurrency: template?.scope_concurrency ?? 3,
    session_ttl: template?.session_ttl ?? 60,
    volumes_json: template?.volumes == null ? '' : JSON.stringify(template.volumes, null, 2),
    main_container_id: template?.main_container_id ?? '',
    sidecar_ids: [...(template?.sidecar_container_ids ?? [])],
  };
}

/** Keep metadata not editable in this form, including deployment provenance. */
export function runtimeTemplateData(data?: Record<string, unknown> | null): Record<string, unknown> | undefined {
  if (!data) return undefined;
  const copy = structuredClone(data);
  const sync = copy.config_sync;
  if (sync && typeof sync === 'object' && !Array.isArray(sync)) {
    // Containers are managed by their catalog or the restore endpoint, never an upsert from this form.
    delete (sync as Record<string, unknown>).containers;
  }
  return copy;
}
