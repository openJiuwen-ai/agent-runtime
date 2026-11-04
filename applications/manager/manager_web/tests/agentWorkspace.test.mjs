import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

globalThis.window = { location: { protocol: 'http:' } };
globalThis.document = { cookie: '' };
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const api = await server.ssrLoadModule('/src/services/api.ts');
  const { loadAgentReferences, loadAgentBindings, loadAgentExpertPackages } = await server.ssrLoadModule('/src/services/agentWorkspace.ts');
  await test('reference details preserve successful resources when one lookup fails', async () => {
    api.ModelTemplateApi.get = async () => ({ template_name: 'Office model', enabled: true });
    api.McpTemplateApi.get = async () => { throw new Error('Access denied'); };
    api.A2AAccessPolicyTemplateApi.get = async () => ({ policy_name: 'Restricted partners', enabled: false });
    const result = await loadAgentReferences({ template_ref: { default_model: ['model-1'], mcp: ['mcp-1'], a2a_access_policy: ['policy-1'] } });
    assert.equal(result[0].name, 'Office model');
    assert.equal(result[0].error, null);
    assert.equal(result[1].error, 'Access denied');
    assert.equal(result[2].name, 'Restricted partners');
    assert.equal(result[2].enabled, false);
  });
  await test('expert summaries and resource details use the persisted template reference', async () => {
    api.AgentTemplateApi.get = async id => {
      assert.equal(id, 'agent-1');
      return { template_ref: { default_model: ['unrelated-model'], expert_package: ['experts-1'] } };
    };
    const lookups = [];
    api.ExpertPackageTemplateApi.get = async id => {
      lookups.push(id);
      return { template_name: 'Support experts', enabled: true };
    };
    const rows = await loadAgentExpertPackages('agent-1');
    assert.deepEqual(lookups, ['experts-1']);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].name, 'Support experts');
    assert.equal(rows[0].path, '/expert-package-templates');
    api.AgentTemplateApi.get = async () => ({ template_ref: {} });
    assert.deepEqual(await loadAgentExpertPackages('unbound'), []);
    api.ExpertPackageTemplateApi.get = async () => { throw new Error('Package template unavailable'); };
    const unavailable = await loadAgentReferences({ template_ref: { expert_package: ['experts-1'] } });
    assert.equal(unavailable[0].error, 'Package template unavailable');
    assert.equal(unavailable[0].enabled, false);
  });
  await test('dynamic and fallback references are resolved without treating expressions as template IDs', async () => {
    const lookups = [];
    api.ModelTemplateApi.get = async id => { lookups.push(id); return { template_name: 'Fallback model', enabled: true }; };
    const rows = await loadAgentReferences({ template_ref: { default_model: ['${user::u} or fallback-model'] } });
    assert.deepEqual(lookups, ['fallback-model']);
    assert.equal(rows[0].dynamic, true);
    assert.equal(rows[1].name, 'Fallback model');
    assert.equal(rows[1].dynamic, false);
  });
  await test('bindings include later pages and retain failed instance checks', async () => {
    api.InstanceApi.list = async () => ({ items: [{ jiuwenclaw_id: 'a' }, { jiuwenclaw_id: 'b' }], total: 2, page_size: 100 });
    api.InstanceAgentResourceApi.listInstanceAgentResources = async (id, { page }) => {
      if (id === 'b') throw new Error('Unavailable');
      return { items: [{ ref_template_id: page === 1 ? 'other' : 'target' }], total: 2, page_size: 1 };
    };
    const result = await loadAgentBindings('target');
    assert.equal(result[0].resources.length, 1);
    assert.equal(result[0].resources[0].ref_template_id, 'target');
    assert.equal(result[1].error, 'Unavailable');
  });
} finally { await server.close(); }
