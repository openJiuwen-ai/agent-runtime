import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

const server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { admissionKey, admissionTimestamp, localAdmissionTime, writeAdmissionChanges, sameAdmission, hasAdmissionChanges, hasActiveAdmission, writeOrganizationChanges } = await server.ssrLoadModule('/src/utils/wizardAdmission.ts');
  const row = (kind, subjectId, patch = {}) => ({ kind, subjectId, login_policy: 'allow', enabled: true, expires_at: null, ...patch });

  test('identity keys separate organization and user IDs and unchanged grants generate no writes', async () => {
    assert.notEqual(admissionKey(row('user', 'same')), admissionKey(row('org', 'same')));
    const original = [row('user', 'same'), row('org', 'same')];
    const calls = [];
    const result = await writeAdmissionChanges(original, original, { bind: async () => calls.push('bind'), update: async () => calls.push('update'), remove: async () => calls.push('remove') });
    assert.deepEqual(calls, []);
    assert.deepEqual(result.saved, original);
    assert.deepEqual(result.errors, []);
  });

  test('an existing binding is patched rather than replaced and an expiry can be cleared', async () => {
    const previous = row('org', 'org-a', { expires_at: '2027-01-01T00:00:00Z' });
    const next = row('org', 'org-a', { login_policy: 'deny', enabled: false });
    const calls = [];
    const result = await writeAdmissionChanges([previous], [next], { bind: async () => assert.fail('unexpected bind'), remove: async () => assert.fail('unexpected revoke'), update: async (...args) => calls.push(args) });
    assert.deepEqual(calls, [['org', 'org-a', { login_policy: 'deny', enabled: false, expires_at: null, clear_expires_at: true }]]);
    assert.deepEqual(result.saved, [next]);
    assert.ok(sameAdmission(previous, { ...previous, expires_at: '2027-01-01T08:00:00+08:00' }));
  });

  test('partial failures retain completed writes, defer revocations and only retry pending operations', async () => {
    const baseline = [row('user', 'old')];
    const wanted = [row('user', 'new'), row('org', 'org-a')];
    const calls = [];
    let fail = true;
    const writer = {
      bind: async (kind, id) => { calls.push(['bind', kind, id]); if (id === 'org-a' && fail) throw new Error('not available'); },
      update: async (kind, id) => calls.push(['update', kind, id]),
      remove: async (kind, id) => calls.push(['remove', kind, id]),
    };
    const first = await writeAdmissionChanges(baseline, wanted, writer);
    assert.equal(first.errors.length, 1);
    assert.deepEqual(first.saved.map(item => item.subjectId), ['old', 'new']);
    assert.ok(!calls.some(call => call[0] === 'remove'));
    fail = false; calls.length = 0;
    const retried = await writeAdmissionChanges(first.saved, wanted, writer);
    assert.deepEqual(calls, [['bind', 'org', 'org-a'], ['update', 'org', 'org-a'], ['remove', 'user', 'old']]);
    assert.deepEqual(retried.errors, []);
    assert.deepEqual(retried.saved, wanted);
  });

  test('a failed new-grant patch is retried even when the server already created the grant', async () => {
    const wanted = [row('user', 'new', { login_policy: 'deny' })];
    let patches = 0;
    let binds = 0;
    const writer = { bind: async () => { binds++; }, update: async () => { patches++; if (patches === 1) throw new Error('network'); }, remove: async () => assert.fail('unexpected removal') };
    const first = await writeAdmissionChanges([], wanted, writer);
    assert.equal(first.errors.length, 1);
    const retried = await writeAdmissionChanges(first.saved, wanted, writer);
    assert.equal(binds, 2);
    assert.equal(patches, 2);
    assert.deepEqual(retried.saved, wanted);
  });

  test('removing a grant touches only cluster admission, leaving identity resources and unrelated grants alone', async () => {
    const calls = [];
    const result = await writeAdmissionChanges([row('org', 'removed')], [], { bind: async () => assert.fail('bind'), update: async () => assert.fail('update'), remove: async (...args) => calls.push(args) });
    assert.deepEqual(calls, [['org', 'removed']]);
    assert.deepEqual(result.saved, []);
  });

  test('leaving the wizard detects admission additions, removals and settings, while order and timezone representation are immaterial', () => {
    const previous = [row('user', 'user-a'), row('org', 'org-a', { expires_at: '2027-01-01T00:00:00Z' })];
    assert.equal(hasAdmissionChanges(previous, [...previous].reverse()), false);
    assert.equal(hasAdmissionChanges(previous, [previous[0], { ...previous[1], expires_at: '2027-01-01T08:00:00+08:00' }]), false);
    assert.equal(hasAdmissionChanges(previous, previous.slice(0, 1)), true);
    assert.equal(hasAdmissionChanges(previous, [...previous, row('user', 'new')]), true);
    assert.equal(hasAdmissionChanges(previous, [row('user', 'user-a', { login_policy: 'deny' }), previous[1]]), true);
    assert.equal(hasAdmissionChanges(previous, [row('user', 'user-a', { enabled: false }), previous[1]]), true);
  });

  test('empty, denied, disabled, expired and inactive identities cannot complete admission as an allow rule', () => {
    const activeUsers = new Set(['user-a']);
    const activeOrgs = new Set(['org-a']);
    const now = Date.parse('2026-11-26T00:00:00Z');
    for (const draft of [[], [row('user', 'user-a', { login_policy: 'deny' })], [row('user', 'user-a', { enabled: false })], [row('user', 'user-a', { expires_at: '2026-11-25T00:00:00Z' })], [row('user', 'disabled')], [row('org', 'missing')]]) {
      assert.equal(hasActiveAdmission(draft, activeUsers, activeOrgs, now), false);
    }
    assert.equal(hasActiveAdmission([row('user', 'user-a')], activeUsers, activeOrgs, now), true);
    assert.equal(hasActiveAdmission([row('org', 'org-a', { expires_at: '2027-01-01T00:00:00Z' })], activeUsers, activeOrgs, now), true);
  });

  test('offset-free backend expiries are UTC and display at the equivalent local wall time', () => {
    assert.equal(admissionTimestamp('2027-01-01T00:00:00'), Date.parse('2027-01-01T00:00:00Z'));
    assert.equal(localAdmissionTime('2027-01-01T00:00:00'), localAdmissionTime('2027-01-01T00:00:00Z'));
    assert.equal(sameAdmission(row('user', 'a', { expires_at: '2027-01-01T00:00:00' }), row('user', 'a', { expires_at: '2027-01-01T08:00:00+08:00' })), true);
  });

  test('a nested user survives organization membership failure and retry does not create the parent or repeat successful member writes', async () => {
    const organization = { group_id: 'new-org', display_name: '团队', status: 'active', created_at: null, updated_at: null };
    const calls = [];
    let fail = true;
    const writer = {
      create: async body => { calls.push(['create', body]); return organization; },
      update: async () => assert.fail('unchanged parent must not update'),
      addMember: async (id, userId) => { calls.push(['add', id, userId]); if (userId === 'nested-user' && fail) throw new Error('temporary member service failure'); },
      removeMember: async () => assert.fail('no members to remove'),
    };
    // nested-user was already saved globally by the inline child user editor.
    const draftMembers = ['existing-user', 'nested-user'];
    const first = await writeOrganizationChanges(undefined, { display_name: '团队' }, [], draftMembers, writer);
    assert.equal(first.organization, organization);
    assert.deepEqual(first.memberIds, ['existing-user']);
    assert.match(first.error.message, /temporary/);
    fail = false; calls.length = 0;
    const retried = await writeOrganizationChanges(first.organization, { display_name: '团队' }, first.memberIds, draftMembers, writer);
    assert.deepEqual(calls, [['add', 'new-org', 'nested-user']]);
    assert.equal(retried.error, null);
    assert.deepEqual(retried.memberIds, draftMembers);
  });

  test('an organization rename and successful membership removals become the retry baseline', async () => {
    const original = { group_id: 'org', display_name: '旧名', status: 'active', created_at: null, updated_at: null };
    let fail = true;
    const calls = [];
    const writer = {
      create: async () => assert.fail('existing organization must not be recreated'),
      update: async (id, body) => { calls.push(['rename', id]); return { ...original, ...body }; },
      addMember: async () => assert.fail('no additions'),
      removeMember: async (id, userId) => { calls.push(['remove', id, userId]); if (userId === 'second' && fail) throw new Error('failure'); },
    };
    const first = await writeOrganizationChanges(original, { display_name: '新名' }, ['first', 'second'], [], writer);
    assert.equal(first.organization.display_name, '新名');
    assert.deepEqual(first.memberIds, ['second']);
    fail = false; calls.length = 0;
    const retried = await writeOrganizationChanges(first.organization, { display_name: '新名' }, first.memberIds, [], writer);
    assert.deepEqual(calls, [['remove', 'org', 'second']]);
    assert.equal(retried.error, null);
    assert.deepEqual(retried.memberIds, []);
  });
} finally { await server.close(); }
