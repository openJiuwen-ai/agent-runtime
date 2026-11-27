import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

globalThis.window = { location: { protocol: 'http:', origin: 'http://localhost:5273' } };
globalThis.document = { cookie: '', documentElement: {} };
globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    async text() { return JSON.stringify(body); },
    async json() { return body; },
  };
}

const calls = [];
let failReclaim = false;

globalThis.fetch = async (url, init = {}) => {
  const request = { url: String(url), method: init.method ?? 'GET' };
  calls.push(request);
  if (request.url.includes('/reclaim-teams')) {
    return failReclaim
      ? jsonResponse(500, { detail: 'team reclaim failed' })
      : jsonResponse(200, { code: 200, message: 'success', data: {} });
  }
  return jsonResponse(200, { deleted: true, removed: true, group_ids: [] });
};

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const server = await createServer({
  configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false },
  appType: 'custom',
});

try {
  const { OrgApi, UserApi } = await server.ssrLoadModule('/src/services/api.ts');

  const reset = () => {
    calls.length = 0;
    failReclaim = false;
  };

  await test('deleting an org reclaims that org teams after the IAM call succeeds', async () => {
    reset();
    const result = await OrgApi.remove('org/a b');
    await flush();
    assert.equal(result.deleted, true);
    assert.deepEqual(calls.map(({ url, method }) => [url, method]), [
      ['http://localhost:5273/idp/v1/orgs/org%2Fa%20b', 'DELETE'],
      ['http://localhost:5273/api/v1/orgs/org%2Fa%20b/reclaim-teams', 'POST'],
    ]);
  });

  await test('removing an org member reclaims the member teams by current orgs', async () => {
    reset();
    await OrgApi.removeMember('org-1', 'user/1');
    await flush();
    assert.deepEqual(calls.map(({ url, method }) => [url, method]), [
      ['http://localhost:5273/idp/v1/orgs/org-1/members/user%2F1', 'DELETE'],
      ['http://localhost:5273/api/v1/users/user%2F1/reclaim-teams', 'POST'],
    ]);
  });

  await test('deleting a user reclaims all of that user teams', async () => {
    reset();
    await UserApi.remove('user-2');
    await flush();
    assert.deepEqual(calls.map(({ url, method }) => [url, method]), [
      ['http://localhost:5273/idp/v1/users/user-2', 'DELETE'],
      ['http://localhost:5273/api/v1/users/user-2/reclaim-teams', 'POST'],
    ]);
  });

  await test('setting user orgs reclaims teams that no longer belong to the kept orgs', async () => {
    reset();
    await UserApi.setOrgs('user-3', ['org-a']);
    await flush();
    assert.deepEqual(calls.map(({ url, method }) => [url, method]), [
      ['http://localhost:5273/idp/v1/users/user-3/orgs', 'PUT'],
      ['http://localhost:5273/api/v1/users/user-3/reclaim-teams', 'POST'],
    ]);
  });

  await test('a failed reclaim does not break the completed IAM operation', async () => {
    reset();
    failReclaim = true;
    const originalWarn = console.warn;
    const warnings = [];
    console.warn = (...args) => warnings.push(args);
    try {
      assert.equal((await OrgApi.remove('org-2')).deleted, true);
      await flush();
    } finally {
      console.warn = originalWarn;
    }
    assert.equal(calls.at(-1).url, 'http://localhost:5273/api/v1/orgs/org-2/reclaim-teams');
    assert.equal(warnings.length, 1);
  });
} finally {
  await server.close();
}
