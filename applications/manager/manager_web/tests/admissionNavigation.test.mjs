import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { admissionCatalogPath, admissionOrgPath, admissionClusterReturnPath, admissionPreparationCluster, admissionDraftKey, readAdmissionDraft, writeAdmissionDraft, clearAdmissionDraft } = await server.ssrLoadModule('/src/utils/admissionNavigation.ts');
  const queryOf = (path) => new URLSearchParams(path.split('?')[1]);

  test('user preparation roundtrip preserves cluster, subject tab and pending setup context', () => {
    const input = new URLSearchParams({ setup: '1', template: 'agent-a', returnTo: '/agent-templates?search=hello&create=1', admissionResume: '1', create: '1', page: '8' });
    const catalog = admissionCatalogPath('users', input, 'cluster-a');
    assert.equal(catalog.split('?')[0], '/users');
    const query = queryOf(catalog);
    assert.equal(query.get('admissionCluster'), 'cluster-a');
    assert.equal(query.get('accessTab'), 'users');
    assert.equal(query.get('template'), 'agent-a');
    assert.equal(query.get('returnTo'), '/agent-templates?search=hello');
    assert.equal(query.get('setup'), '1');
    assert.equal(query.has('page'), false);
    assert.equal(query.has('create'), false);
    const returned = admissionClusterReturnPath(query);
    assert.equal(returned.split('?')[0], '/instances/cluster-a/access');
    assert.equal(queryOf(returned).get('accessTab'), 'users');
    assert.equal(queryOf(returned).get('admissionResume'), '1');
    assert.equal(queryOf(returned).has('admissionCluster'), false);
  });

  test('organization create/edit/list hops retain the same admission return', () => {
    const catalog = admissionCatalogPath('orgs', new URLSearchParams({ setup: '1', admissionResume: '1' }), 'cluster-b');
    const create = admissionOrgPath('new', queryOf(catalog));
    const saved = admissionOrgPath('org-b', queryOf(create));
    const listed = admissionCatalogPath('orgs', queryOf(saved));
    assert.equal(listed, catalog);
    assert.equal(admissionClusterReturnPath(queryOf(saved)), '/instances/cluster-b/access?setup=1&accessTab=orgs&admissionResume=1');
    assert.equal(admissionOrgPath('ordinary-org'), '/orgs/ordinary-org');
  });

  test('no preparation context creates no return button; unsafe IDs and return targets stay local', () => {
    assert.equal(admissionClusterReturnPath(new URLSearchParams()), null);
    for (const value of ['https://evil.test', '//evil.test', '../cluster', 'cluster?x=1', 'cluster/a', 'cluster\\a', 'c\n']) {
      const query = new URLSearchParams({ admissionCluster: value });
      assert.equal(admissionPreparationCluster(query), null);
      assert.equal(admissionClusterReturnPath(query), null);
      assert.equal(admissionCatalogPath('users', query), '/users');
    }
    const returned = admissionClusterReturnPath(new URLSearchParams({ admissionCluster: 'safe', accessTab: 'bad', returnTo: 'https://evil.test' }));
    assert.equal(returned, '/instances/safe/access?returnTo=%2Fagent-templates&accessTab=orgs');
  });

  test('unsaved admission selection is isolated by user, cluster and subject kind', () => {
    const storage = new Map();
    const local = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
    const key = admissionDraftKey('admin', 'cluster-a', 'users');
    const draft = { ids: ['alice', 'bob'], expiresAt: '2027-01-02T09:00', loginPolicy: 'deny' };
    writeAdmissionDraft(local, key, draft);
    assert.deepEqual(readAdmissionDraft(local, key), draft);
    for (const other of [admissionDraftKey('user', 'cluster-a', 'users'), admissionDraftKey('admin', 'cluster-b', 'users'), admissionDraftKey('admin', 'cluster-a', 'orgs')]) {
      assert.deepEqual(readAdmissionDraft(local, other), { ids: [], expiresAt: '', loginPolicy: 'allow' });
    }
    clearAdmissionDraft(local, key);
    assert.deepEqual(readAdmissionDraft(local, key), { ids: [], expiresAt: '', loginPolicy: 'allow' });
  });

  test('corrupt or unavailable handoff storage cannot break preparation', () => {
    const bad = { getItem: () => '{', setItem: () => { throw Error('blocked'); }, removeItem: () => { throw Error('blocked'); } };
    assert.deepEqual(readAdmissionDraft(bad, 'x'), { ids: [], expiresAt: '', loginPolicy: 'allow' });
    writeAdmissionDraft(bad, 'x', { ids: [], expiresAt: '', loginPolicy: 'allow' });
    clearAdmissionDraft(bad, 'x');
    const invalid = { getItem: () => JSON.stringify({ ids: [7], expiresAt: '', loginPolicy: 'allow' }) };
    assert.deepEqual(readAdmissionDraft(invalid, 'x'), { ids: [], expiresAt: '', loginPolicy: 'allow' });
  });
} finally {
  await server.close();
}
