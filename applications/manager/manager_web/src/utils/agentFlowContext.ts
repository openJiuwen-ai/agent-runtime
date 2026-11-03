/** Only allow the exact local template catalog as a return target. */
export function templateReturnPath(value: string | null): string {
  if (!value || !/^\/agent-templates(?:\?|$)/.test(value) || /[\\\r\n]/.test(value)) return '/agent-templates';
  const query = new URLSearchParams(value.includes('?') ? value.slice(value.indexOf('?') + 1) : '');
  query.delete('create');
  return `/agent-templates${query.size ? `?${query}` : ''}`;
}
export function templateFlowQuery(params: URLSearchParams): string {
  const query = new URLSearchParams({ returnTo: templateReturnPath(params.get('returnTo')) });
  const cluster = agentPreparationCluster(params);
  if (cluster) query.set('cluster', cluster);
  if (params.get('setup') === '1') query.set('setup', '1');
  return `?${query}`;
}

function clusterIdentity(value: string | null): string | null {
  return value && /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : null;
}

/** Catalog preparation belongs to a specific cluster, never an arbitrary URL. */
export function agentPreparationCluster(params: URLSearchParams): string | null {
  return clusterIdentity(params.get('cluster') || params.get('clusterId'));
}

function clusterPreparationQuery(params: URLSearchParams): URLSearchParams {
  const query = new URLSearchParams();
  if (params.get('setup') === '1') query.set('setup', '1');
  if (params.has('returnTo')) query.set('returnTo', templateReturnPath(params.get('returnTo')));
  const template = params.get('template') || params.get('templateId');
  if (template) query.set('template', template);
  return query;
}

/** Leave the cluster to prepare a template while retaining its setup context. */
export function agentPreparationCatalogPath(instanceId: string, params = new URLSearchParams(), create = false): string {
  const cluster = clusterIdentity(instanceId);
  if (!cluster) return '/agent-templates';
  const query = clusterPreparationQuery(params);
  query.set('cluster', cluster);
  if (create && cluster !== 'mock-cluster-demo') query.set('create', '1');
  return `/agent-templates?${query}`;
}

/** Return to the same binding form. A newly selected template replaces the old one. */
export function agentClusterReturnPath(params: URLSearchParams, selectedTemplate?: string): string | null {
  const cluster = agentPreparationCluster(params);
  if (!cluster) return null;
  const query = clusterPreparationQuery(params);
  if (cluster === 'mock-cluster-demo') query.delete('template');
  else {
    query.set('create', '1');
    if (selectedTemplate) query.set('template', selectedTemplate);
  }
  return `/instances/${encodeURIComponent(cluster)}/agent-resources${query.size ? `?${query}` : ''}`;
}

/** Consumed binding actions must not re-open the modal on later navigation. */
export function consumeAgentBindingAction(path: string, params: URLSearchParams): string {
  const query = new URLSearchParams(params);
  query.delete('template'); query.delete('templateId'); query.delete('create');
  return `${path}${query.size ? `?${query}` : ''}`;
}

/** After saving, drop create/template actions and keep setup only for its original cluster. */
export function savedAgentBindingContext(params: URLSearchParams, instanceId: string): URLSearchParams {
  const context = new URLSearchParams();
  if (params.has('returnTo')) context.set('returnTo', templateReturnPath(params.get('returnTo')));
  if (agentPreparationCluster(params) === instanceId && params.get('setup') === '1') context.set('setup', '1');
  return context;
}
