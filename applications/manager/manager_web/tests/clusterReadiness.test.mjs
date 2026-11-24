import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { clusterReadiness } = await server.ssrLoadModule('/src/utils/clusterReadiness.ts');
  const ready = { configurationComplete: true, connected: true, identityLoading: false, identityError: false, contextCount: 1 };
  test('saved configurations alone never authorize opening the user console', () => {
    assert.equal(clusterReadiness({ ...ready, connected: false }), 'connection');
    assert.equal(clusterReadiness({ ...ready, contextCount: 0 }), 'identity');
    assert.equal(clusterReadiness({ ...ready, configurationComplete: false }), 'configuration');
  });
  test('loading or failed identity requests cannot reuse an old positive context count', () => {
    assert.equal(clusterReadiness({ ...ready, identityLoading: true }), 'checking');
    assert.equal(clusterReadiness({ ...ready, identityError: true }), 'identity-error');
  });
  test('complete connected configuration with current identity access permits verification', () => {
    assert.equal(clusterReadiness(ready), 'ready');
  });
} finally { await server.close(); }
