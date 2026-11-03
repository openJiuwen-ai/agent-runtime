export interface AgentClientCredentials {
  registration_id: string;
  client_uuid: string;
  client_id: string;
  client_secret: string;
}

export function createRegistrationId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function hasAgentCredentials(entries: Record<string, unknown>[]): boolean {
  return entries.some((entry) => typeof entry.client_id === 'string' && entry.client_id.trim()
    && typeof entry.client_secret === 'string' && entry.client_secret.trim());
}

export function mergeAgentCredentials(
  entries: Record<string, unknown>[], credentials: AgentClientCredentials,
): Record<string, unknown>[] {
  if (hasAgentCredentials(entries)) throw new Error('Agent credentials already exist');
  const result = entries.map((entry) => ({ ...entry }));
  const pair = { client_id: credentials.client_id, client_secret: credentials.client_secret };
  result.push(pair);
  return result;
}

export function maskAgentClientSecrets(entries: Record<string, unknown>[]): string {
  return JSON.stringify(entries.map((entry) => (
    typeof entry.client_secret === 'string' && entry.client_secret
      ? { ...entry, client_secret: '******' } : entry
  )), null, 2);
}
