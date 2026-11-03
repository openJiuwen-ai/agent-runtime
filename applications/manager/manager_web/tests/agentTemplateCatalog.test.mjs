import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

globalThis.window = { location: { protocol: 'http:' } };
globalThis.document = { cookie: '' };
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const api = await server.ssrLoadModule('/src/services/api.ts');
  const { loadAgentTemplateCatalog, referencesResource } = await server.ssrLoadModule('/src/services/agentTemplateCatalog.ts');
  const { canonicalAgentPath, agentResourceReferencesPath, agentResourceCatalogPath } = await server.ssrLoadModule('/src/utils/agentTemplateNavigation.ts');
  await test('resource links round-trip safely to their corresponding catalogs and A2A tabs', () => {
    for (const [type, path] of Object.entries({ model: '/model-templates', embedding: '/embedding-templates', skill: '/skill-prebuilt-templates', mcp: '/mcp-templates', extension: '/extension-config-templates', permission: '/safety-guardrails', 'a2a-policy': '/a2a-management?tab=policies', 'a2a-outbound': '/a2a-management?tab=agents' })) {
      assert.equal(agentResourceCatalogPath(type), path);
      const url = new URL(agentResourceReferencesPath(type, 'resource / & 1', '名称 & 1'), 'http://localhost');
      assert.equal(url.pathname, '/agent-templates');
      assert.equal(url.searchParams.get('refType'), type);
      assert.equal(url.searchParams.get('refId'), 'resource / & 1');
      assert.equal(url.searchParams.get('refName'), '名称 & 1');
    }
    assert.equal(agentResourceCatalogPath('unknown'), null);
    assert.equal(agentResourceCatalogPath('__proto__'), null);
    assert.equal(agentResourceCatalogPath(null), null);
  });
  await test('legacy Agent routes retain encoded IDs and query context', () => {
    assert.equal(canonicalAgentPath('/agents'), '/agent-templates');
    assert.equal(canonicalAgentPath('/agents/a%2Fb/connect'), '/agent-templates/a%2Fb/connect');
    assert.equal(canonicalAgentPath('/agents-other'), null);
    assert.equal(canonicalAgentPath('/agent-templates'), null);
    const query = new URLSearchParams({ cluster: '集群/a', returnTo: '/agent-templates?search=a&status=enabled' });
    const url = `${canonicalAgentPath('/agents/a/connect')}?${query}`;
    assert.equal(new URL(url, 'http://localhost').searchParams.get('cluster'), '集群/a');
    assert.equal(new URL(url, 'http://localhost').searchParams.get('returnTo'), '/agent-templates?search=a&status=enabled');
  });
  await test('resource filter matches precise references in all model slots and fallback chains', () => {
    const agent = { template_ref: { vision_model: ['${user::same-id} or model-2'], mcp: ['same-id'], a2a_access_policy: ['policy-a'] } };
    assert.equal(referencesResource(agent, 'model', 'model-2'), true);
    assert.equal(referencesResource(agent, 'model', 'same-id'), false);
    assert.equal(referencesResource(agent, 'mcp', 'same-id'), true);
    assert.equal(referencesResource(agent, 'mcp', 'same'), false);
    assert.equal(referencesResource(agent, 'a2a-policy', 'policy-a'), true);
    assert.equal(referencesResource(agent, '__proto__', 'policy-a'), false);
  });
  await test('resource filtering reads every server page before slicing and does not hide errors', async () => {
    const calls = [];
    api.AgentTemplateApi.list = async query => {
      calls.push(query);
      return { items: [{ template_id: String(query.page), template_ref: { default_model: [query.page === 1 ? 'other' : 'wanted'] } }], page: query.page, total: 3, page_size: 1 };
    };
    const result = await loadAgentTemplateCatalog({ page: 2, page_size: 1, enabled: true, search: 'office', sort_by: 'template_name', sort_order: 'asc' }, 'model', 'wanted');
    assert.equal(result.total, 2);
    assert.equal(result.items[0].template_id, '3');
    assert.equal(calls.length, 3);
    assert.ok(calls.every(query => query.enabled && query.search === 'office' && query.sort_by === 'template_name'));
    api.AgentTemplateApi.list = async ({ page }) => { if (page > 1) throw new Error('page unavailable'); return { items: [], total: 2, page_size: 1 }; };
    await assert.rejects(() => loadAgentTemplateCatalog({}, 'model', 'wanted'), /page unavailable/);
  });
  await test('template editor loads all resource pages and surfaces failed loads', async () => {
    const { loadTemplateOptions } = await server.ssrLoadModule('/src/services/templateOptions.ts');
    for (const key of ['EmbeddingTemplateApi', 'ExtensionTemplateApi', 'McpTemplateApi', 'PermissionsTemplateApi', 'SkillPrebuiltTemplateApi', 'A2AAccessPolicyTemplateApi']) {
      api[key].list = async () => ({ items: [], total: 0, page_size: 100 });
    }
    api.ModelTemplateApi.list = async ({ page, enabled }) => {
      assert.equal(enabled, true);
      return { items: [{ template_id: `model-${page}`, template_name: `Model ${page}` }], total: 2, page_size: 1 };
    };
    const options = await loadTemplateOptions();
    assert.deepEqual(options.default_model.map(option => option.template_id), ['model-1', 'model-2']);
    api.McpTemplateApi.list = async () => { throw new Error('MCP unavailable'); };
    await assert.rejects(loadTemplateOptions, /MCP unavailable/);
  });
  await test('unfiltered catalog uses server pagination directly', async () => {
    let query;
    api.AgentTemplateApi.list = async args => { query = args; return { items: [], total: 0 }; };
    await loadAgentTemplateCatalog({ page: 4, page_size: 20 });
    assert.deepEqual(query, { page: 4, page_size: 20 });
  });
  await test('outbound A2A filtering respects allowlists and denylist exclusions', async () => {
    api.A2AAccessPolicyTemplateApi.list = async () => ({ items: [
      { policy_id: 'allow', mode: 'allowlist', member_template_ids: ['outbound'], enabled: false },
      { policy_id: 'deny', mode: 'denylist', member_template_ids: ['other'] },
      { policy_id: 'excluded', mode: 'denylist', member_template_ids: ['outbound'] },
    ], total: 3, page_size: 100 });
    api.AgentTemplateApi.list = async () => ({ items: ['allow', 'deny', 'excluded', 'missing'].map(id => ({
      template_id: id, template_ref: { a2a_access_policy: [id] },
    })), total: 4, page_size: 100 });
    const result = await loadAgentTemplateCatalog({}, 'a2a-outbound', 'outbound');
    assert.deepEqual(result.items.map(agent => agent.template_id), ['allow', 'deny']);
  });
} finally { await server.close(); }
