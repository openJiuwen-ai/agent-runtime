export type ClusterReadiness = 'configuration' | 'connection' | 'checking' | 'identity-error' | 'identity' | 'ready';

/** Saved bindings and an accessible Agent context are separate from a verified conversation. */
export function clusterReadiness(input: {
  configurationComplete: boolean;
  connected: boolean;
  identityLoading: boolean;
  identityError: boolean;
  contextCount: number;
}): ClusterReadiness {
  if (!input.configurationComplete) return 'configuration';
  if (!input.connected) return 'connection';
  if (input.identityLoading) return 'checking';
  if (input.identityError) return 'identity-error';
  if (!input.contextCount) return 'identity';
  return 'ready';
}
