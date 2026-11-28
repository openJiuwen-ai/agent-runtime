import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

globalThis.window = { location: { origin: 'http://localhost', protocol: 'http:' } };
globalThis.document = { cookie: '', documentElement: { lang: '' } };
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const originalFetch = globalThis.fetch;
const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });

try {
  const { loadMatchAgentOptions } = await server.ssrLoadModule('/src/services/matchAgentOptions.ts');
  const reply = (items, total = items.length, page_size = 100) =>
    new Response(JSON.stringify({ code: 200, data: { items, total, page_size } }), { status: 200 });

  await test('Runtime options read every page of the current cluster and distinguish resources sharing a template', async () => {
    const calls = [];
    globalThis.fetch = async url => {
      const parsed = new URL(url);
      calls.push(parsed);
      assert.equal(parsed.pathname, '/api/v1/instances/cluster%2Fa/agent-resources');
      const page = Number(parsed.searchParams.get('page'));
      return reply([{ resource_id: `resource-${page}`, resource_name: `Agent ${page}`, ref_template_id: 'shared-config' }], 2, 1);
    };
    const options = await loadMatchAgentOptions('cluster/a');
    assert.deepEqual(options, [
      { id: 'resource-1', label: 'Agent 1' },
      { id: 'resource-2', label: 'Agent 2' },
    ]);
    assert.deepEqual(calls.map(url => [url.searchParams.get('page'), url.searchParams.get('page_size')]), [['1', '100'], ['2', '1']]);
  });

  await test('changing clusters reads its own resources and unnamed resources display their ID', async () => {
    globalThis.fetch = async url => {
      const cluster = new URL(url).pathname.split('/')[4];
      return reply([{ resource_id: `${cluster}-agent`, resource_name: null, ref_template_id: 'shared-config' }]);
    };
    assert.deepEqual(await loadMatchAgentOptions('first'), [{ id: 'first-agent', label: 'first-agent' }]);
    assert.deepEqual(await loadMatchAgentOptions('second'), [{ id: 'second-agent', label: 'second-agent' }]);
  });

  await test('an empty cluster ID or cluster roster never falls back to global templates', async () => {
    globalThis.fetch = async () => { throw new Error('must not fetch without a cluster'); };
    assert.deepEqual(await loadMatchAgentOptions(''), []);
    globalThis.fetch = async url => {
      assert.match(new URL(url).pathname, /\/instances\/empty\/agent-resources$/);
      return reply([]);
    };
    assert.deepEqual(await loadMatchAgentOptions('empty'), []);
  });

  await test('a failed resource page rejects the entire load instead of exposing partial or global choices', async () => {
    globalThis.fetch = async url => {
      const page = new URL(url).searchParams.get('page');
      return page === '1'
        ? reply([{ resource_id: 'first-agent', resource_name: 'First' }], 2, 1)
        : new Response(JSON.stringify({ detail: 'resource page failed' }), { status: 503 });
    };
    await assert.rejects(() => loadMatchAgentOptions('cluster'), /resource page failed/);
  });

  await test('consumers without a cluster scope retain paginated template choices', async () => {
    globalThis.fetch = async url => {
      const parsed = new URL(url);
      assert.match(parsed.pathname, /\/agent-templates\/?$/);
      const page = parsed.searchParams.get('page');
      return reply([{ template_id: `config-${page}`, template_name: `Config ${page}` }], 2, 1);
    };
    assert.deepEqual(await loadMatchAgentOptions(), [
      { id: 'config-1', label: 'Config 1' },
      { id: 'config-2', label: 'Config 2' },
    ]);
  });

  await test('rule editing retains disabled and expired resources for configuration before activation', async () => {
    globalThis.fetch = async url => {
      assert.equal(new URL(url).searchParams.has('enabled'), false);
      return reply([
        { resource_id: 'disabled', resource_name: 'Disabled Agent', enabled: false },
        { resource_id: 'expired', resource_name: 'Expired Agent', enabled: true, expires_at: '2000-01-01T00:00:00Z' },
      ]);
    };
    assert.deepEqual(await loadMatchAgentOptions('cluster'), [
      { id: 'disabled', label: 'Disabled Agent' },
      { id: 'expired', label: 'Expired Agent' },
    ]);
  });
} finally {
  globalThis.fetch = originalFetch;
  await server.close();
}
