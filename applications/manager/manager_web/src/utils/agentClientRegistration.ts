export function hasAgentCredentials(entries: Record<string, unknown>[]): boolean {
  return entries.some((entry) => typeof entry.client_id === 'string' && entry.client_id.trim()
    && typeof entry.client_secret === 'string' && entry.client_secret.trim());
}

export function maskAgentClientSecrets(entries: Record<string, unknown>[]): string {
  return JSON.stringify(entries.map((entry) => (
    typeof entry.client_secret === 'string' && entry.client_secret
      ? { ...entry, client_secret: '******' } : entry
  )), null, 2);
}

export function agentRegistrationErrorKey(status: number, response: unknown) {
  if (status === 502) return 'keycloakFailed';
  if (status === 503) return 'unconfigured';
  if (status === 409 && response && typeof response === 'object' && 'detail' in response) {
    const detail = response.detail;
    if (detail && typeof detail === 'object' && 'code' in detail) {
      switch (detail.code) {
        case 'client_id_in_use': return 'clientIdConflict';
        case 'manual_credentials_present': return 'removeManualCredentials';
        case 'template_already_bound': return 'alreadyBound';
        case 'template_busy': return 'templateBusy';
      }
    }
  }
  return 'failed';
}
