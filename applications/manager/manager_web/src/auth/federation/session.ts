/** Same-origin handoff consumed by User Web, independently of platform tokens. */
export interface FederationAccessToken {
  connection_id: string;
  user_id: string;
  access_token: string;
  token_type: string;
}

const FEDERATION_KEY = 'openjiuwen_federation';
export const OIDC_CONNECTION_KEY = 'openjiuwen_oidc_connection';

export function setFederationToken(federation?: FederationAccessToken): void {
  if (federation) localStorage.setItem(FEDERATION_KEY, JSON.stringify(federation));
  else localStorage.removeItem(FEDERATION_KEY);
}

export function clearFederationSession(): void {
  localStorage.removeItem(FEDERATION_KEY);
}
