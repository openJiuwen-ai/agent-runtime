import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';
const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false }, appType: 'custom' });
try {
  const { collectPages, allComponentsConnected } = await server.ssrLoadModule('/src/utils/overview.ts');
  test('overview follows backend page size and includes all instances', async () => {
    const calls = [];
    const rows = await collectPages(async (page, size) => {
      calls.push([page, size]);
      return { items: page === 1 ? [1, 2] : [3], total: 3, page_size: 2 };
    });
    assert.deepEqual(rows, [1, 2, 3]);
    assert.deepEqual(calls, [[1, 100], [2, 2]]);
  });
  test('a failed later page rejects instead of displaying incomplete counts', async () => {
    await assert.rejects(collectPages(async page => {
      if (page === 2) throw new Error('network unavailable');
      return { items: [1], total: 2, page_size: 1 };
    }), /network unavailable/);
  });
  test('Gateway alone cannot make an instance healthy', () => {
    assert.equal(allComponentsConnected({ gateway_status: 'online' }), false);
    assert.equal(allComponentsConnected({ gateway_status: 'online', runtime_status: 'offline', user_web_status: 'online' }), false);
    assert.equal(allComponentsConnected({ gateway_status: 'ONLINE', runtime_status: 'ready', user_web_status: 'online' }), true);
  });
} finally { await server.close(); }
