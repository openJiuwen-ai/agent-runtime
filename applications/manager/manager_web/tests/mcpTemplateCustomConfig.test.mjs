import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';

const server = await createServer({
  appType: 'custom', configFile: false, server: { middlewareMode: true, ws: false },
});
after(() => server.close());
const { customConfigFromMcpEntry, parseMcpCustomConfig, reservedMcpCustomFields } = await server.ssrLoadModule(
  '/src/pages/templates/mcpTemplateCustomConfig.ts',
);

test('editing restores all additional fields without including base fields', () => {
  const extra = {
    dynamic_token_url: 'https://keycloak.example.com/realms/company/protocol/openid-connect/token',
    options: { nested: [{ enabled: false, label: '工具' }], name: 'nested-name' },
    nullable: null,
    retry: 0,
  };
  const entry = {
    name: 'customer-tools', transport: 'streamable-http', url: 'https://egress.example.com/mcp',
    headers: { 'X-Custom-Header': 'example' }, auth_headers: null, timeout_s: 10, enabled: true, ...extra,
  };
  const before = structuredClone(entry);
  assert.deepEqual(parseMcpCustomConfig(customConfigFromMcpEntry(entry)), extra);
  assert.deepEqual(entry, before);
  assert.deepEqual(reservedMcpCustomFields(extra), []);
});

test('blank input and an empty object clear only additional fields', () => {
  for (const raw of ['', '   ', '{}', '// example\n{}']) {
    assert.deepEqual(parseMcpCustomConfig(raw), {});
  }
  assert.equal(customConfigFromMcpEntry({ name: 'tools', transport: 'http', url: 'https://example.com' }), '');
});

for (const raw of ['[]', '[{}]', 'null', '"text"', '1', 'true', '{"broken":}']) {
  test(`custom configuration rejects a non-object or invalid JSON: ${raw}`, () => {
    assert.equal(parseMcpCustomConfig(raw), undefined);
  });
}

test('custom fields cannot replace any base field or its legacy alias', () => {
  const fields = ['name', 'transport', 'url', 'headers', 'auth_headers', 'timeout_s', 'enabled'];
  for (const field of fields) {
    assert.deepEqual(reservedMcpCustomFields({ [field]: 'override' }), [field]);
  }
});

test('JSON object values preserve empty token URLs and arbitrary nested settings', () => {
  const extra = { dynamic_token_url: '', options: { headers: { name: 'allowed-nested-key' } } };
  assert.deepEqual(parseMcpCustomConfig(JSON.stringify(extra)), extra);
  assert.deepEqual(reservedMcpCustomFields(extra), []);
});
