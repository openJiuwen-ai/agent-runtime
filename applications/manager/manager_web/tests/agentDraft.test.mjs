import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';
const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { applyAgentTemplateChoice, bindingDraftKey, bindingDraftForCluster, readBindingDraft, bindingExpiry, clusterBindingDraftKey, emptyClusterBindingDraft, readClusterBindingDraft, readClusterBindingHandoff, writeClusterBindingDraft } = await server.ssrLoadModule('/src/utils/agentDraft.ts');
  const { agentPreparationCatalogPath, agentClusterReturnPath, consumeAgentBindingAction, savedAgentBindingContext, templateReturnPath, templateFlowQuery } = await server.ssrLoadModule('/src/utils/agentFlowContext.ts');
  test('cluster preparation returns to its binding form without losing setup context', () => {
    const context = new URLSearchParams({ setup: '1', template: 'old-agent', returnTo: '/agent-templates?search=sales&create=1', focusTemplate: 'unrelated' });
    const catalog = agentPreparationCatalogPath('cluster-a', context, true);
    assert.equal(catalog.split('?')[0], '/agent-templates');
    const catalogParams = new URLSearchParams(catalog.split('?')[1]);
    assert.equal(catalogParams.get('cluster'), 'cluster-a');
    assert.equal(catalogParams.get('create'), '1');
    assert.equal(catalogParams.get('focusTemplate'), null);
    const back = agentClusterReturnPath(catalogParams, 'new-agent');
    assert.equal(back.split('?')[0], '/instances/cluster-a/agent-resources');
    const backParams = new URLSearchParams(back.split('?')[1]);
    assert.equal(backParams.get('setup'), '1');
    assert.equal(backParams.get('template'), 'new-agent');
    assert.equal(backParams.get('create'), '1');
    assert.equal(backParams.get('returnTo'), '/agent-templates?search=sales');
    assert.equal(backParams.has('cluster'), false);
    const consumed = new URLSearchParams(consumeAgentBindingAction('/instances/cluster-a/agent-resources', backParams).split('?')[1]);
    assert.equal(consumed.get('setup'), '1');
    for (const key of ['template', 'create', 'templateId']) assert.equal(consumed.has(key), false);
    assert.equal(agentClusterReturnPath(new URLSearchParams({ cluster: '//other.test' })), null);
    assert.equal(agentPreparationCatalogPath('../other'), '/agent-templates');
  });
  test('chosen Agent keeps the existing binding scope and rejects stale templates', () => {
    const draft = { addAgentId: 'old', addResourceName: '客服', addResourceDesc: 'desc', addMatchExpr: 'user_id == "u"', addExpiresAt: '2027-01-01T10:00' };
    const catalog = [{ template_id: 'new', template_name: '新模板', enabled: true }, { template_id: 'disabled', template_name: '禁用', enabled: false }];
    assert.deepEqual(applyAgentTemplateChoice(draft, 'new', catalog), { draft: { ...draft, addAgentId: 'new' }, error: null });
    assert.equal(applyAgentTemplateChoice({ ...draft, addResourceName: '' }, 'new', catalog).draft.addResourceName, '新模板');
    for (const [id, error] of [['missing', 'missing'], ['disabled', 'disabled']]) {
      assert.deepEqual(applyAgentTemplateChoice(draft, id, catalog), { draft: { ...draft, addAgentId: '' }, error });
    }
  });
  test('saved binding navigation consumes create actions and avoids carrying setup into another cluster', () => {
    const params = new URLSearchParams({ cluster: 'a', setup: '1', template: 'agent', create: '1', returnTo: '/agent-templates?search=sales' });
    const same = savedAgentBindingContext(params, 'a');
    assert.equal(same.get('setup'), '1');
    assert.equal(same.get('returnTo'), '/agent-templates?search=sales');
    assert.equal(same.has('template'), false);
    assert.equal(same.has('create'), false);
    assert.equal(savedAgentBindingContext(params, 'b').has('setup'), false);
    assert.equal(new URLSearchParams(templateFlowQuery(params)).get('setup'), '1');
  });
  test('cluster handoff isolates drafts and never restores another cluster scope', () => {
    const saved = { instance: 'old', name: '客服', expr: 'user_id == "a"' };
    assert.notEqual(bindingDraftKey('admin', 'a', 'old'), bindingDraftKey('admin', 'a', 'new'));
    assert.deepEqual(bindingDraftForCluster(saved, 'new'), { instance: 'new', name: '', expr: '' });
    assert.deepEqual(bindingDraftForCluster(saved, 'old'), saved);
    assert.deepEqual(bindingDraftForCluster(saved, ''), saved);
  });
  test('template flow retains catalog filters without reopening create or following external returns', () => {
    const back = '/agent-templates?search=why?&refType=model&refId=m&create=1';
    assert.equal(templateReturnPath(back), '/agent-templates?search=why%3F&refType=model&refId=m');
    for (const path of ['https://other.test', '//other.test', '/agents', '/agent-templates/123', '/agent-templates\\other', null]) assert.equal(templateReturnPath(path), '/agent-templates');
    const flow = new URLSearchParams(templateFlowQuery(new URLSearchParams({ returnTo: back, cluster: 'c1' })));
    assert.equal(flow.get('cluster'), 'c1');
    assert.equal(flow.get('returnTo'), templateReturnPath(back));
  });
  test('cluster-to-template round trip retains all binding fields without mixing accounts or clusters', () => {
    assert.notEqual(clusterBindingDraftKey('u1', 'c1'), clusterBindingDraftKey('u2', 'c1'));
    assert.notEqual(clusterBindingDraftKey('u1', 'c1'), clusterBindingDraftKey('u1', 'c2'));
    const draft = { addAgentId: 'a', addResourceName: '客服', addResourceDesc: 'desc', addMatchExpr: 'user_id == "u"', addExpiresAt: '2027-01-01T10:00' };
    assert.deepEqual(readClusterBindingDraft({ getItem: () => JSON.stringify(draft) }, 'key'), draft);
    assert.deepEqual(readClusterBindingHandoff({ getItem: () => JSON.stringify(draft) }, 'key'), draft);
    assert.equal(readClusterBindingHandoff({ getItem: () => null }, 'key'), null);
    assert.equal(readClusterBindingHandoff({ getItem: () => '{bad' }, 'key'), null);
    for (const raw of ['null', '{bad', '{"addAgentId":1}']) assert.deepEqual(readClusterBindingDraft({ getItem: () => raw }, 'key'), emptyClusterBindingDraft);
    assert.deepEqual(readClusterBindingDraft({ getItem: () => { throw new Error('denied'); } }, 'key'), emptyClusterBindingDraft);
  });
  test('binding drafts are isolated by account and Agent without delimiter collisions', () => {
    assert.notEqual(bindingDraftKey('admin', 'a'), bindingDraftKey('user', 'a'));
    assert.notEqual(bindingDraftKey('admin', 'a'), bindingDraftKey('admin', 'b'));
    assert.notEqual(bindingDraftKey('a:b', 'c'), bindingDraftKey('a', 'b:c'));
  });
  test('refresh restores the latest Agent binding edits after template handoff and cancellation clears them', () => {
    const values = new Map();
    const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
    const key = clusterBindingDraftKey('admin', 'cluster-a');
    const otherKey = clusterBindingDraftKey('user', 'cluster-a');
    const draft = { addAgentId: 'old', addResourceName: '客服', addResourceDesc: '', addMatchExpr: 'user_id == "u"', addExpiresAt: '' };
    writeClusterBindingDraft(storage, otherKey, draft);
    writeClusterBindingDraft(storage, key, draft);
    const selected = applyAgentTemplateChoice(readClusterBindingDraft(storage, key), 'new', [{ template_id: 'new', template_name: '新模板', enabled: true }]).draft;
    writeClusterBindingDraft(storage, key, selected);
    const edited = { ...selected, addResourceName: '返回后修改的名称', addResourceDesc: '最新说明', addMatchExpr: 'user_id == "updated-user"', addExpiresAt: '2027-02-01T12:00' };
    assert.equal(writeClusterBindingDraft(storage, key, edited), true);
    assert.deepEqual(readClusterBindingHandoff(storage, key), edited);
    assert.deepEqual(readClusterBindingHandoff(storage, otherKey), draft);
    storage.removeItem(key);
    assert.equal(readClusterBindingHandoff(storage, key), null);
  });
  test('Agent draft storage failures are reported without discarding the existing draft', () => {
    const draft = { ...emptyClusterBindingDraft, addResourceName: '保留的草稿' };
    const storage = { getItem: () => JSON.stringify(draft), setItem: () => { throw new Error('storage disabled'); } };
    assert.equal(writeClusterBindingDraft(storage, 'key', { ...draft, addResourceName: 'new' }), false);
    assert.deepEqual(readClusterBindingHandoff(storage, 'key'), draft);
  });
  test('draft restoration preserves entered values and rejects malformed storage', () => {
    const draft = { instance: 'instance-1', name: '客服', expr: 'user_id == "a"' };
    assert.deepEqual(readBindingDraft({ getItem: () => JSON.stringify(draft) }, 'key'), draft);
    for (const value of ['{bad', 'null', '{"instance":1,"name":"x","expr":""}']) {
      assert.deepEqual(readBindingDraft({ getItem: () => value }, 'key'), { instance: '', name: '', expr: '' });
    }
    assert.deepEqual(readBindingDraft({ getItem: () => { throw new Error('denied'); } }, 'key'), { instance: '', name: '', expr: '' });
  });
  test('binding expiry distinguishes expired, unlimited and invalid dates', () => {
    const now = Date.parse('2026-10-30T00:00:00Z');
    assert.equal(bindingExpiry(null, now), 'unlimited');
    assert.equal(bindingExpiry('2026-10-30T00:00:00Z', now), 'expired');
    assert.equal(bindingExpiry('2026-10-31T00:00:00Z', now), 'scheduled');
    assert.equal(bindingExpiry('bad-date', now), 'unknown');
  });
} finally { await server.close(); }
