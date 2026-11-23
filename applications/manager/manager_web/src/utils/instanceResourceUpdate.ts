import type { InstanceAgentResource, InstanceServiceResource, MatchExpr } from '../services/api';

interface AgentResourceUpdate {
  ref_template_id: string;
  match_exprs: MatchExpr[];
  resource_name: string;
  resource_desc: string | null;
  enabled: boolean;
  expires_at: string | null;
  data: Record<string, unknown> | null;
}

interface ServiceResourceUpdate extends AgentResourceUpdate {
  priority: number;
}

/** These PATCH endpoints replace the whole binding, including omitted defaulted fields. */
export function agentResourceUpdateBody(
  resource: InstanceAgentResource,
  changes: Partial<AgentResourceUpdate>,
): AgentResourceUpdate {
  return {
    ref_template_id: resource.ref_template_id,
    match_exprs: [resource.match_expr],
    resource_name: resource.resource_name?.trim() || resource.resource_id,
    resource_desc: resource.resource_desc ?? null,
    enabled: resource.enabled,
    expires_at: resource.expires_at ?? null,
    data: resource.data ?? null,
    ...changes,
  };
}

export function serviceResourceUpdateBody(
  resource: InstanceServiceResource,
  changes: Partial<ServiceResourceUpdate>,
): ServiceResourceUpdate {
  return {
    ...agentResourceUpdateBody(resource, changes),
    priority: changes.priority ?? resource.priority,
  };
}

/** Keep the stored precision when the minute-level editor was left unchanged. */
export function resourceExpiryForUpdate(original: string | null | undefined, input: string): string | null {
  if (!input) return null;
  if (original) {
    const date = new Date(original);
    if (Number.isFinite(date.getTime())) {
      const displayed = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      if (input === displayed) return original;
    }
  }
  return new Date(input).toISOString();
}
