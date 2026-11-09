import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';

const server = await createServer({
  appType: 'custom', configFile: false, server: { middlewareMode: true, ws: false },
});
after(() => server.close());
const { agentRegistrationErrorKey, hasAgentCredentials, maskAgentClientSecrets } =
  await server.ssrLoadModule('/src/utils/agentClientRegistration.ts');
const credentials = { client_id: 'agent-test', client_secret: 'real-secret' };

for (const entry of [
  { client_secret: 'third-party-secret', service: 'other' },
  { client_id: 'existing-client', client_secret: '' },
  { client_id: 'existing-client' },
  { client_id: 42, client_secret: null },
]) {
  test(`partial or unrelated credentials do not count as a complete pair: ${JSON.stringify(entry)}`, () => {
    assert.equal(hasAgentCredentials([entry]), false);
  });
}

test('a complete manual credential pair is detected', () => {
  assert.equal(hasAgentCredentials([{ client_id: 'id', client_secret: 'secret' }]), true);
});

test('masking affects only presentation and leaves the value saved and dispatched unchanged', () => {
  const entries = [{ custom: 'keep' }, { ...credentials }];
  assert.deepEqual(JSON.parse(maskAgentClientSecrets(entries)), [{ custom: 'keep' }, { client_id: 'agent-test', client_secret: '******' }]);
  assert.equal(entries[1].client_secret, 'real-secret');
});

test('registration conflicts use the server reason rather than a generic Client ID message', () => {
  const error = (code) => ({ detail: { code } });
  assert.equal(agentRegistrationErrorKey(409, error('client_id_in_use')), 'clientIdConflict');
  assert.equal(agentRegistrationErrorKey(409, error('manual_credentials_present')), 'removeManualCredentials');
  assert.equal(agentRegistrationErrorKey(409, error('template_already_bound')), 'alreadyBound');
  assert.equal(agentRegistrationErrorKey(409, error('template_busy')), 'templateBusy');
  assert.equal(agentRegistrationErrorKey(409, error('unknown')), 'failed');
});
