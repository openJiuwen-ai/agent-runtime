export interface FederationConnection {
  connection_id: string;
  name: string;
  flow: 'server_code' | 'browser_pkce';
}

export interface FederationConfiguration {
  connection_id: string;
  flow: 'browser_pkce';
  authority: string;
  client_id: string;
  scope: string;
  automatic_silent_renew: boolean;
}
