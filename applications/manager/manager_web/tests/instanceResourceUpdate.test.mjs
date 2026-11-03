import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

globalThis.window = { location: { protocol: 'http:', origin: 'http://localhost:5273' } };
globalThis.document = { cookie: '' };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const originalFetch = globalThis.fetch;
const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });

try {
  const { agentResourceUpdateBody, serviceResourceUpdateBody } = await server.ssrLoadModule('/src/utils/instanceResourceUpdate.ts');
  const { InstanceAgentResourceApi, InstanceServiceResourceApi } = await server.ssrLoadModule('/src/services/api.ts');
  const binding = {
    id: 4,
    jiuwenclaw_id: 'cluster-1',
    resource_id: 'binding-1',
    resource_name: 'Saved binding',
    resource_desc: 'Saved description',
    ref_template_id: 'template-1',
    match_expr: ["user_id in ['a']", "group_id in ['team']"],
    enabled: false,
    expires_at: '2027-01-02T03:04:05.123Z',
    data: { deployment: { revision: 7 }, labels: ['managed'] },
    priority: -3,
    granted_by: 'admin',
    created_at: null,
    updated_at: null,
  };

  for (const [kind, build, api] of [
    ['Agent', agentResourceUpdateBody, InstanceAgentResourceApi],
    ['runtime', serviceResourceUpdateBody, InstanceServiceResourceApi],
  ]) {
    await test(`${kind}: editing a disabled binding preserves its status, metadata and unchanged expiry`, () => {
      const before = structuredClone(binding);
      const body = build(binding, { resource_name: 'Renamed', resource_desc: 'Edited' });
      assert.equal(body.resource_name, 'Renamed');
      assert.equal(body.resource_desc, 'Edited');
      assert.equal(body.enabled, false);
      assert.equal(body.expires_at, binding.expires_at);
      assert.deepEqual(body.data, binding.data);
      assert.deepEqual(body.match_exprs, [binding.match_expr]);
      assert.deepEqual(binding, before);
      if (kind === 'runtime') assert.equal(body.priority, -3);
    });

    await test(`${kind}: enabling and disabling transmit the full saved binding through the PATCH API`, async () => {
      const calls = [];
      globalThis.fetch = async (url, options) => {
        calls.push({ url, method: options.method, body: JSON.parse(options.body) });
        return new Response(JSON.stringify({ code: 200, message: 'success', data: { items: [] } }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      };
      for (const enabled of [true, false]) {
        await api.update('cluster-1', 'binding-1', build(binding, { enabled }));
        const request = calls.at(-1);
        assert.equal(request.method, 'PATCH');
        assert.equal(request.url, `http://localhost:5273/api/v1/instances/cluster-1/${kind === 'Agent' ? 'agent' : 'service'}-resources/binding-1`);
        assert.deepEqual(request.body, {
          match_exprs: [binding.match_expr],
          resource_name: binding.resource_name,
          resource_desc: binding.resource_desc,
          enabled,
          expires_at: binding.expires_at,
          data: binding.data,
          ...(kind === 'runtime' ? { priority: binding.priority } : {}),
        });
      }
    });

    await test(`${kind}: explicit clearing and replacing expiry remain supported`, () => {
      assert.equal(build(binding, { expires_at: null }).expires_at, null);
      assert.equal(build(binding, { expires_at: '2028-02-03T00:00:00Z' }).expires_at, '2028-02-03T00:00:00Z');
      const body = build({ ...binding, data: null, expires_at: null }, { enabled: true });
      assert.equal(body.data, null);
      assert.equal(body.expires_at, null);
      if (kind === 'runtime') assert.equal(build(binding, { priority: 0 }).priority, 0);
    });
  }
} finally {
  globalThis.fetch = originalFetch;
  await server.close();
}
