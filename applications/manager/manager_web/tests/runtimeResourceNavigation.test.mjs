import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { runtimeCatalogPath, runtimeClusterReturnPath, runtimePreparationCluster } = await server.ssrLoadModule('/src/utils/runtimeResourceNavigation.ts');

  test('preparing runtime resources keeps the current cluster and pending Agent handoff', () => {
    const context = new URLSearchParams({ setup: '1', template: 'agent-a', returnTo: '/agent-templates?search=customer&create=1', create: '1', runtimeTemplate: 'old-runtime', filter: 'old' });
    const target = runtimeCatalogPath('service-config-templates', context, 'cluster-a');
    const query = new URLSearchParams(target.split('?')[1]);
    assert.equal(target.split('?')[0], '/service-config-templates');
    assert.equal(query.get('cluster'), 'cluster-a');
    assert.equal(query.get('template'), 'agent-a');
    assert.equal(query.get('setup'), '1');
    assert.equal(query.get('returnTo'), '/agent-templates?search=customer');
    for (const key of ['create', 'runtimeTemplate', 'filter']) assert.equal(query.has(key), false);
    const containers = runtimeCatalogPath('container-templates', query);
    const back = runtimeCatalogPath('service-config-templates', new URLSearchParams(containers.split('?')[1]));
    assert.equal(back, target);
  });

  test('ordinary return does not open a binding form; explicit template choice only prefills it', () => {
    const params = new URLSearchParams({ cluster: 'cluster-a', setup: '1', template: 'agent-a', preparedRuntime: 'runtime-b' });
    assert.equal(runtimeClusterReturnPath(params), '/instances/cluster-a/service-resources?template=agent-a&setup=1');
    const target = runtimeClusterReturnPath(params, 'runtime-b');
    const query = new URLSearchParams(target.split('?')[1]);
    assert.equal(query.get('create'), '1');
    assert.equal(query.get('runtimeTemplate'), 'runtime-b');
    assert.equal(query.get('template'), 'agent-a');
    assert.equal(query.has('cluster'), false);
    assert.equal(query.has('preparedRuntime'), false);
  });

  test('catalog paths retain a recently saved selection through refresh and container preparation', () => {
    const params = new URLSearchParams({ cluster: 'cluster-a', preparedRuntime: 'runtime-b' });
    const catalog = runtimeCatalogPath('container-templates', params);
    assert.equal(new URLSearchParams(catalog.split('?')[1]).get('preparedRuntime'), 'runtime-b');
    assert.equal(runtimeCatalogPath('service-config-templates', new URLSearchParams({ preparedRuntime: 'runtime-b' })), '/service-config-templates');
  });

  test('return routes reject arbitrary paths and cannot leave the local cluster workspace', () => {
    for (const cluster of ['', '../other', '//evil.test', 'https://evil.test', 'cluster/a', 'cluster\\a', 'cluster?admin=1', '%2F%2Fevil.test', 'c\n']) {
      const params = new URLSearchParams({ cluster, returnTo: '//evil.test' });
      assert.equal(runtimePreparationCluster(params), null);
      assert.equal(runtimeClusterReturnPath(params), null);
    }
    const target = runtimeClusterReturnPath(new URLSearchParams({ clusterId: 'safe-cluster', returnTo: 'https://evil.test' }), 'https://evil.test');
    assert.equal(target, '/instances/safe-cluster/service-resources?returnTo=%2Fagent-templates');
    assert.equal(runtimeClusterReturnPath(new URLSearchParams({ returnTo: '/instances/safe-cluster/service-resources' })), null);
  });
} finally {
  await server.close();
}
