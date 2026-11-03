import { templateReturnPath } from './agentFlowContext';

type RuntimeCatalog = 'service-config-templates' | 'container-templates';

function localId(value: string | null | undefined): string | null {
  return value && /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : null;
}

/** Keep an explicit cluster identity; never accept an arbitrary return URL. */
export function runtimePreparationCluster(params: URLSearchParams): string | null {
  return localId(params.get('cluster') || params.get('clusterId'));
}

function flowContext(params: URLSearchParams): URLSearchParams {
  const query = new URLSearchParams();
  const template = params.get('template') || params.get('templateId');
  if (template) query.set('template', template);
  if (params.get('setup') === '1') query.set('setup', '1');
  if (params.has('returnTo')) query.set('returnTo', templateReturnPath(params.get('returnTo')));
  return query;
}

/** Catalog hops carry preparation context, but never reopen a consumed create action. */
export function runtimeCatalogPath(catalog: RuntimeCatalog, params = new URLSearchParams(), instanceId?: string): string {
  const query = flowContext(params);
  const cluster = localId(instanceId) || runtimePreparationCluster(params);
  if (cluster) query.set('cluster', cluster);
  const prepared = localId(params.get('preparedRuntime'));
  if (cluster && prepared) query.set('preparedRuntime', prepared);
  return `/${catalog}${query.size ? `?${query}` : ''}`;
}

/** A selected catalog template only prefills the binding form; it never creates a binding. */
export function runtimeClusterReturnPath(params: URLSearchParams, templateId?: string): string | null {
  const cluster = runtimePreparationCluster(params);
  if (!cluster) return null;
  const query = flowContext(params);
  const selected = localId(templateId);
  if (selected) {
    query.set('create', '1');
    query.set('runtimeTemplate', selected);
  }
  return `/instances/${encodeURIComponent(cluster)}/service-resources${query.size ? `?${query}` : ''}`;
}
