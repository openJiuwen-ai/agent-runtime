import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';

const server = await createServer({
  appType: 'custom', configFile: false, server: { middlewareMode: true, ws: false },
});
after(() => server.close());
const { createRegistrationId, hasAgentCredentials, mergeAgentCredentials, maskAgentClientSecrets } =
  await server.ssrLoadModule('/src/utils/agentClientRegistration.ts');
const credentials = { client_id: 'agent-test', client_secret: 'real-secret' };

test('merging generated credentials appends a new entry and preserves existing configuration', () => {
  const entries = [{ options: { nested: [1, 2] } }, { client_id: '', other: true }];
  const before = structuredClone(entries);
  const result = mergeAgentCredentials(entries, credentials);
  assert.deepEqual(result, [...entries, credentials]);
  assert.deepEqual(entries, before);
});

for (const entry of [
  { client_secret: 'third-party-secret', service: 'other' },
  { client_id: 'existing-client', client_secret: '' },
  { client_id: 'existing-client' },
  { client_id: 42, client_secret: null },
]) {
  test(`partial or unrelated credentials remain unchanged: ${JSON.stringify(entry)}`, () => {
    const before = structuredClone(entry);
    const result = mergeAgentCredentials([entry], credentials);
    assert.deepEqual(result, [before, credentials]);
    assert.deepEqual(entry, before);
  });
}

test('a complete credential pair is never overwritten or hidden by appending a second pair', () => {
  assert.equal(hasAgentCredentials([{ client_id: 'id', client_secret: 'secret' }]), true);
  assert.throws(() => mergeAgentCredentials([{ client_id: 'id', client_secret: 'secret' }], credentials));
});

test('masking affects only presentation and leaves the value saved and dispatched unchanged', () => {
  const entries = [{ custom: 'keep' }, { ...credentials }];
  assert.deepEqual(JSON.parse(maskAgentClientSecrets(entries)), [{ custom: 'keep' }, { client_id: 'agent-test', client_secret: '******' }]);
  assert.equal(entries[1].client_secret, 'real-secret');
});

test('registration identifiers use getRandomValues and work without randomUUID on HTTP deployments', () => {
  const id = createRegistrationId();
  assert.match(id, /^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/);
  assert.notEqual(id, createRegistrationId());
});
