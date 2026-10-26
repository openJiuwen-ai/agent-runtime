import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'vite';

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

async function withClient(run) {
  const saved = Object.fromEntries(
    ['window', 'sessionStorage', 'localStorage', '__federationTest'].map((name) => [name, globalThis[name]]),
  );
  const session = memoryStorage();
  const local = memoryStorage();
  const calls = [];
  const user = {
    access_token: 'customer-access', token_type: 'Bearer', expired: false,
    profile: { sub: 'customer-subject' }, state: { connection_id: 'enterprise' },
  };
  const handlers = new Map();
  class UserManager {
    constructor(settings) { calls.push(['settings', settings]); }
    async clearStaleState() {}
    async signinRedirect(args) { calls.push(['redirect', args]); }
    async signinCallback(url) { calls.push(['loginCallback', url]); return user; }
    async signinSilentCallback(url) { calls.push(['silentCallback', url]); }
    async removeUser() { calls.push(['removeUser']); }
    async getUser() { return user; }
    startSilentRenew() { calls.push(['startRenew']); }
    stopSilentRenew() { calls.push(['stopRenew']); }
    async signinSilent() {
      await handlers.get('loaded')?.({ ...user, access_token: 'renewed-access', expired: false });
    }
    events = {
      addUserLoaded: (callback) => handlers.set('loaded', callback),
      removeUserLoaded: () => handlers.delete('loaded'),
      addAccessTokenExpired: (callback) => handlers.set('expired', callback),
      removeAccessTokenExpired: () => handlers.delete('expired'),
    };
  }
  const config = {
    connection_id: 'enterprise', flow: 'browser_pkce', authority: 'https://sso.example.test',
    client_id: 'web-login', scope: 'openid', automatic_silent_renew: true,
  };
  const AuthApi = {
    federationConfiguration: async () => config,
    refreshFederationHandoff: async (connectionId, token) => {
      calls.push(['handoff', connectionId, token]);
      return { connection_id: connectionId, user_id: 'local-user', access_token: token, token_type: 'Bearer' };
    },
  };
  globalThis.window = {
    location: {
      origin: 'https://app.example.test',
      href: 'https://app.example.test/auth/federation/enterprise/callback',
    },
    sessionStorage: session,
    parent: {},
  };
  globalThis.sessionStorage = session;
  globalThis.localStorage = local;
  globalThis.__federationTest = { UserManager, WebStorageStateStore: class {}, AuthApi };
  const server = await createServer({
    appType: 'custom', configFile: false, server: { middlewareMode: true, hmr: false, ws: false },
    ssr: { noExternal: ['oidc-client-ts'] },
    plugins: [{
      name: 'federation-test-doubles',
      enforce: 'pre',
      resolveId(source, importer) {
        if (source === 'oidc-client-ts') return '\0federation-oidc-test';
        if (source === '../../services/api' && importer?.endsWith('/auth/federation/client.ts')) {
          return '\0federation-api-test';
        }
      },
      load(id) {
        if (id === '\0federation-oidc-test') {
          return 'export const { UserManager, WebStorageStateStore } = globalThis.__federationTest;';
        }
        if (id === '\0federation-api-test') return 'export const { AuthApi } = globalThis.__federationTest;';
      },
    }],
  });
  try {
    const client = await server.ssrLoadModule('/src/auth/federation/client.ts');
    await run({ client, calls, user, session, local, handlers });
  } finally {
    await server.close();
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete globalThis[name];
      else globalThis[name] = value;
    }
  }
}

test('normal login retains its pending context and uses the login callback', () => withClient(async (context) => {
  const { client, calls, session } = context;
  await client.beginBrowserLogin('enterprise');
  assert.equal(session.getItem('openjiuwen_oidc_pending'), 'enterprise');
  const settings = calls.find(([name]) => name === 'settings')[1];
  assert.equal(settings.silent_redirect_uri, 'https://app.example.test/auth/federation/enterprise/silent-callback');
  assert.equal(await client.consumeBrowserCallback('enterprise'), 'customer-access');
  assert.equal(session.getItem('openjiuwen_oidc_pending'), null);
  assert.equal(calls.filter(([name]) => name === 'loginCallback').length, 1);
  assert.equal(calls.filter(([name]) => name === 'silentCallback').length, 0);
}));

test('silent iframe callback preserves credentials without a pending login', () => withClient(async (context) => {
  const { client, calls, local, session } = context;
  local.setItem('openjiuwen_access_token', 'platform-access');
  local.setItem('openjiuwen_federation', 'existing-customer-session');
  assert.equal(session.getItem('openjiuwen_oidc_pending'), null);
  await client.consumeSilentCallback('enterprise');
  assert.equal(calls.filter(([name]) => name === 'silentCallback').length, 1);
  assert.equal(calls.filter(([name]) => name === 'loginCallback' || name === 'removeUser').length, 0);
  assert.equal(local.getItem('openjiuwen_access_token'), 'platform-access');
  assert.equal(local.getItem('openjiuwen_federation'), 'existing-customer-session');
}));

test('top-level silent callback is rejected before creating an OIDC client', () => withClient(async (context) => {
  const { client, calls } = context;
  window.parent = window;
  await assert.rejects(client.consumeSilentCallback('enterprise'), /requires an iframe/);
  assert.equal(calls.length, 0);
}));

test('normal callback still rejects a missing pending login', () => withClient(async ({ client, calls }) => {
  await assert.rejects(client.consumeBrowserCallback('enterprise'), /Missing login context/);
  assert.equal(calls.length, 0);
}));

test('renewal updates the bound customer token and cleans up its handlers', () => withClient(async (context) => {
  const { client, calls, local, user, handlers } = context;
  local.setItem('openjiuwen_oidc_connection', 'enterprise');
  local.setItem('openjiuwen_oidc_renew', 'true');
  local.setItem('openjiuwen_federation', JSON.stringify({ user_id: 'local-user', connection_id: 'enterprise' }));
  local.setItem('openjiuwen_access_token', 'platform-access');
  local.setItem('openjiuwen_refresh_token', 'platform-refresh');
  user.expired = true;
  const stop = await client.maintainBrowserSession();
  assert.equal(JSON.parse(local.getItem('openjiuwen_federation')).access_token, 'renewed-access');
  assert.equal(local.getItem('openjiuwen_access_token'), 'platform-access');
  assert.equal(local.getItem('openjiuwen_refresh_token'), 'platform-refresh');
  assert.deepEqual(calls.find(([name]) => name === 'handoff'), ['handoff', 'enterprise', 'renewed-access']);
  stop();
  assert.equal(handlers.size, 0);
  assert.equal(calls.filter(([name]) => name === 'stopRenew').length, 1);
}));
