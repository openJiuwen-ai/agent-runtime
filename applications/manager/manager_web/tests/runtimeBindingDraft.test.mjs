import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { runtimeBindingDraftKey, readRuntimeBindingDraft, writeRuntimeBindingDraft, applyRuntimeTemplateChoice, consumeRuntimeBindingAction } = await server.ssrLoadModule('/src/utils/runtimeBindingDraft.ts');
  const { runtimeCatalogPath, runtimeClusterReturnPath } = await server.ssrLoadModule('/src/utils/runtimeResourceNavigation.ts');
  const { collectPages } = await server.ssrLoadModule('/src/utils/overview.ts');
  const draft = { addTemplateId: 'old', addResourceName: '客服运行时', addResourceDesc: '范围暂存', addPriority: 7, addMatchExpr: 'user_id == "user-a"', addExpiresAt: '2027-01-01T10:30' };

  test('runtime binding drafts isolate users, clusters and separator-like identifiers', () => {
    assert.notEqual(runtimeBindingDraftKey('admin', 'cluster-a'), runtimeBindingDraftKey('user', 'cluster-a'));
    assert.notEqual(runtimeBindingDraftKey('admin', 'cluster-a'), runtimeBindingDraftKey('admin', 'cluster-b'));
    assert.notEqual(runtimeBindingDraftKey('a:b', 'c'), runtimeBindingDraftKey('a', 'b:c'));
    assert.deepEqual(readRuntimeBindingDraft({ getItem: () => JSON.stringify(draft) }, 'key'), draft);
  });

  test('missing or damaged storage does not open a false draft; blank priority stays invalid', () => {
    for (const raw of [null, 'null', '{bad', '{}', '[]', JSON.stringify({ ...draft, addPriority: '7' }), JSON.stringify({ ...draft, addMatchExpr: 1 })]) {
      assert.equal(readRuntimeBindingDraft({ getItem: () => raw }, 'key'), null);
    }
    assert.equal(readRuntimeBindingDraft({ getItem: () => { throw new Error('disabled'); } }, 'key'), null);
    assert.deepEqual(readRuntimeBindingDraft({ getItem: () => JSON.stringify({ ...draft, addPriority: null }) }, 'key'), { ...draft, addPriority: null });
  });

  test('refresh restores the latest runtime edits after a catalog return without affecting other clusters', () => {
    const values = new Map();
    const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
    const key = runtimeBindingDraftKey('admin', 'cluster-a');
    const otherKey = runtimeBindingDraftKey('admin', 'cluster-b');
    writeRuntimeBindingDraft(storage, otherKey, draft);
    writeRuntimeBindingDraft(storage, key, draft);
    const selected = applyRuntimeTemplateChoice(readRuntimeBindingDraft(storage, key), 'new', [{ template_id: 'new', template_name: '新规格', enabled: true }]).draft;
    writeRuntimeBindingDraft(storage, key, selected);
    const edited = { ...selected, addResourceName: '目录返回后继续编辑', addResourceDesc: '最新说明', addPriority: NaN, addMatchExpr: 'user_id == "updated-user"' };
    assert.equal(writeRuntimeBindingDraft(storage, key, edited), true);
    assert.deepEqual(readRuntimeBindingDraft(storage, key), { ...edited, addPriority: null });
    assert.deepEqual(readRuntimeBindingDraft(storage, otherKey), draft);
    storage.removeItem(key);
    assert.equal(readRuntimeBindingDraft(storage, key), null);
  });

  test('runtime storage failures remain local failures and preserve the previous saved draft', () => {
    const values = new Map([['key', JSON.stringify(draft)]]);
    const storage = { getItem: key => values.get(key) ?? null, setItem: () => { throw new Error('storage disabled'); } };
    assert.equal(writeRuntimeBindingDraft(storage, 'key', { ...draft, addResourceName: 'new' }), false);
    assert.deepEqual(readRuntimeBindingDraft(storage, 'key'), draft);
  });

  test('runtime catalog round trip preserves scope and priority while consuming only its own action', () => {
    const context = new URLSearchParams({ setup: '1', template: 'pending-agent', returnTo: '/agent-templates?search=service' });
    const catalogPath = runtimeCatalogPath('service-config-templates', context, 'cluster-a');
    const back = runtimeClusterReturnPath(new URLSearchParams(catalogPath.split('?')[1]), 'new-runtime');
    const [path, query] = back.split('?');
    const saved = readRuntimeBindingDraft({ getItem: () => JSON.stringify(draft) }, 'key');
    const result = applyRuntimeTemplateChoice(saved, new URLSearchParams(query).get('runtimeTemplate'), [{ template_id: 'new-runtime', template_name: '新规格', enabled: true }]);
    assert.equal(result.error, null);
    assert.deepEqual(result.draft, { ...draft, addTemplateId: 'new-runtime' });
    const consumed = consumeRuntimeBindingAction(path, new URLSearchParams(query));
    assert.equal(consumed.split('?')[0], '/instances/cluster-a/service-resources');
    const remaining = new URLSearchParams(consumed.split('?')[1]);
    assert.equal(remaining.get('template'), 'pending-agent');
    assert.equal(remaining.get('setup'), '1');
    assert.equal(remaining.get('returnTo'), '/agent-templates?search=service');
    assert.equal(remaining.has('create'), false);
    assert.equal(remaining.has('runtimeTemplate'), false);
  });

  test('returned choice cannot bind missing or disabled runtime templates', () => {
    const catalog = [{ template_id: 'disabled', template_name: '停用规格', enabled: false }];
    for (const [id, error] of [['missing', 'missing'], ['disabled', 'disabled']]) {
      const result = applyRuntimeTemplateChoice(draft, id, catalog);
      assert.equal(result.error, error);
      assert.deepEqual(result.draft, { ...draft, addTemplateId: '' });
    }
    const filled = applyRuntimeTemplateChoice({ ...draft, addResourceName: ' ' }, 'runtime', [{ template_id: 'runtime', template_name: '新规格', enabled: true }]);
    assert.equal(filled.draft.addResourceName, '新规格');
    assert.equal(filled.draft.addMatchExpr, draft.addMatchExpr);
  });

  test('a template on a later directory page is available for safe preselection', async () => {
    const calls = [];
    const catalog = await collectPages(async (page, page_size) => {
      calls.push({ page, page_size });
      return { items: [{ template_id: page === 1 ? 'old' : 'new', template_name: page === 1 ? '旧规格' : '刚创建的规格', enabled: true }], total: 2, page_size: 1 };
    });
    assert.equal(calls.length, 2);
    assert.equal(applyRuntimeTemplateChoice(draft, 'new', catalog).error, null);
    await assert.rejects(() => collectPages(async page => {
      if (page === 2) throw new Error('catalog denied');
      return { items: catalog.slice(0, 1), total: 2, page_size: 1 };
    }), /catalog denied/);
  });
} finally {
  await server.close();
}
