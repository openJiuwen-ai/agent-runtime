import { INT32_MAX } from './intBounds';
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
  /** Raw text of the number boxes, so a cleared box stays empty instead of turning into 0. */
  ready_timeout: string;
  ready_poll_interval: string;
  min_idle_pods: string;
  pod_concurrency: string;
  pod_ttl: string;
  message_timeout: string;
  scope_concurrency: string;
  session_ttl: string;
  volumes_json: string;
  main_container_id: string;
  sidecar_ids: string[];
};

export type IntFieldProblem = 'missing' | 'range';

/** Bounds mirror the manager-server schema: the upper bound is the integer column limit. */
export function intFieldProblem(text: string, min: number): IntFieldProblem | null {
  if (!text.trim()) return 'missing';
  const value = Number(text);
  return Number.isInteger(value) && value >= min && value <= INT32_MAX ? null : 'range';
}

/** The same mapping is used for saved templates and deployment-file previews. */
export function runtimeTemplateForm(template?: ServiceConfigTemplateCreateBody | ServiceConfigTemplate): RuntimeTemplateForm {
  const int = (value: number | undefined, fallback: number) => String(value ?? fallback);
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
    ready_timeout: int(template?.ready_timeout, 300),
    ready_poll_interval: int(template?.ready_poll_interval, 2),
    min_idle_pods: int(template?.min_idle_pods, 0),
    pod_concurrency: int(template?.pod_concurrency, 2),
    pod_ttl: int(template?.pod_ttl, 300),
    message_timeout: int(template?.message_timeout, 600),
    scope_concurrency: int(template?.scope_concurrency, 3),
    session_ttl: int(template?.session_ttl, 60),
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
