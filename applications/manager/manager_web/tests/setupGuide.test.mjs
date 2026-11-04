import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';
const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { readSetupGuide, setupGuideRouteCluster, setupGuideClusterPath } = await server.ssrLoadModule('/src/utils/setupGuide.ts');
  test('shared guide restores selection and rejects old stages, invalid storage and unsafe cluster IDs', () => {
    const saved = { version: 2, stage: 2, clusterId: 'cluster-a', showOptional: true };
    assert.deepEqual(readSetupGuide(JSON.stringify(saved)), { stage: 2, clusterId: 'cluster-a', showOptional: true });
    for (const raw of [null, '{broken', JSON.stringify({ stage: 2 }), JSON.stringify({ version: 1, stage: 3 })]) {
      assert.deepEqual(readSetupGuide(raw), { stage: 0, clusterId: '', showOptional: false });
    }
    assert.deepEqual(readSetupGuide(JSON.stringify({ version: 2, stage: 9, clusterId: '../another', showOptional: 'true' })), { stage: 0, clusterId: '', showOptional: false });
  });
  test('current cluster route overrides catalog context and resource handoffs retain identity', () => {
    assert.equal(setupGuideRouteCluster('/instances/current/access', new URLSearchParams({ cluster: 'other' })), 'current');
    assert.equal(setupGuideRouteCluster('/service-config-templates', new URLSearchParams({ cluster: 'runtime-cluster' })), 'runtime-cluster');
    assert.equal(setupGuideRouteCluster('/users', new URLSearchParams({ admissionCluster: 'admission-cluster' })), 'admission-cluster');
    assert.equal(setupGuideRouteCluster('/instances/%ZZ/access', new URLSearchParams()), '');
  });
  test('missing cluster has no link; switching clusters cannot copy another cluster handoff', () => {
    const params = new URLSearchParams({ template: 'agent-one', returnTo: '/agent-templates', setup: '1', create: '1' });
    assert.equal(setupGuideClusterPath('', 'access', '/overview', params), null);
    assert.equal(setupGuideClusterPath('another', 'access', '/instances/current/cluster-config', params), '/instances/another/access');
    const same = new URL(setupGuideClusterPath('current', 'agent-resources', '/instances/current/access', params), 'http://localhost');
    assert.equal(same.searchParams.get('template'), 'agent-one');
    assert.equal(same.searchParams.get('setup'), '1');
    assert.equal(same.searchParams.has('create'), false);
    assert.equal(setupGuideClusterPath('current', 'cluster-config', '/overview', new URLSearchParams()), '/instances/current/cluster-config');
  });
} finally { await server.close(); }
