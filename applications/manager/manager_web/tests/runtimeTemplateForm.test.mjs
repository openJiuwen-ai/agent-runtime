import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { runtimeTemplateForm, runtimeTemplateData } = await server.ssrLoadModule('/src/utils/runtimeTemplateForm.ts');
  test('loading defaults preserves deployment values and bindings rather than generic form defaults', () => {
    const template = {
      template_name: 'Deployment runtime', description: 'Recovered configuration', namespace: 'business', enabled: false,
      node_name: 'worker', fs_group: 0, pod_name: 'pool', sse_path: '/events', kubeconfig: '/config/kube',
      ready_timeout: 90, ready_poll_interval: 5, min_idle_pods: 0, pod_concurrency: 12,
      pod_ttl: 420, message_timeout: 1200, scope_concurrency: 16, session_ttl: 180,
      volumes: [{ name: 'data', persistentVolumeClaim: { claimName: 'user-data' } }],
      main_container_id: 'draft-main', sidecar_container_ids: ['draft-sandbox'],
    };
    const form = runtimeTemplateForm(template);
    for (const key of ['namespace', 'enabled', 'node_name', 'pod_name', 'sse_path', 'kubeconfig', 'ready_timeout', 'ready_poll_interval', 'min_idle_pods', 'pod_concurrency', 'pod_ttl', 'message_timeout', 'scope_concurrency', 'session_ttl', 'main_container_id']) {
      assert.equal(form[key], template[key]);
    }
    assert.equal(form.fs_group, '0');
    assert.deepEqual(JSON.parse(form.volumes_json), template.volumes);
    assert.deepEqual(form.sidecar_ids, template.sidecar_container_ids);
    form.sidecar_ids.push('extra');
    assert.deepEqual(template.sidecar_container_ids, ['draft-sandbox']);
  });
  test('editing retains default provenance, scopes and extension metadata without container upserts', () => {
    const data = { deployment_default: { fingerprint: 'original' }, extension: { custom: 1 }, config_sync: { source_template_id: 'source', scopes: [{ scope_id: 'scope' }], containers: [{ container_id: 'shared' }] } };
    const result = runtimeTemplateData(data);
    assert.deepEqual(result, { deployment_default: data.deployment_default, extension: data.extension, config_sync: { source_template_id: 'source', scopes: data.config_sync.scopes } });
    result.extension.custom = 2;
    assert.equal(data.extension.custom, 1);
    assert.equal(data.config_sync.containers.length, 1);
  });
  test('a new blank form does not retain restored template containers or values', () => {
    const prior = runtimeTemplateForm({ template_name: 'Loaded', main_container_id: 'draft-main', pod_concurrency: 24 });
    const fresh = runtimeTemplateForm();
    assert.equal(prior.main_container_id, 'draft-main');
    assert.equal(fresh.main_container_id, '');
    assert.equal(fresh.pod_concurrency, 2);
    assert.equal(fresh.template_name, '');
    assert.equal(runtimeTemplateData(undefined), undefined);
  });
} finally { await server.close(); }
