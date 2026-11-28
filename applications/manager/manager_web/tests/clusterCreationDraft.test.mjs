import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';
const cacheDir = await mkdtemp(join(tmpdir(), 'cluster-creation-test-'));
const server = await createServer({ configFile: false, cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { clusterCreationDraftKey, emptyClusterCreationDraft, readClusterCreationDraft, saveClusterCreationDraft, clearClusterCreationDraft, clusterCreationHosts, normalizeClusterCreationDraft, resolveClusterCreationHosts, updateClusterCreationNamespace, updateClusterCreationEndpoint } = await server.ssrLoadModule('/src/utils/clusterCreationDraft.ts');
  test('cluster creation drafts retain every field after leaving and re-entering', () => {
    const map = new Map();
    const storage = { getItem: key => map.get(key) || null, setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) };
    const key = clusterCreationDraftKey('admin', 'agent-a', '/agent-templates?search=客服');
    const draft = { name: '测试集群', description: 'draft', namespace: 'test', hostMode: 'manual', gateway: 'http://gateway:1', runtime: 'http://runtime:2', web: 'http://web:3', gatewayHttp: 'http://gateway:4', gatewayWs: '' };
    saveClusterCreationDraft(storage, key, draft);
    assert.deepEqual(readClusterCreationDraft(storage, key), draft);
    clearClusterCreationDraft(storage, key);
    assert.deepEqual(readClusterCreationDraft(storage, key), emptyClusterCreationDraft);
  });
  test('creation drafts isolate users, selected templates and return contexts', () => {
    const key = clusterCreationDraftKey('admin', 'agent-a', '/agent-templates?search=a');
    assert.notEqual(key, clusterCreationDraftKey('user', 'agent-a', '/agent-templates?search=a'));
    assert.notEqual(key, clusterCreationDraftKey('admin', 'agent-b', '/agent-templates?search=a'));
    assert.notEqual(key, clusterCreationDraftKey('admin', 'agent-a', '/agent-templates?search=b'));
    assert.notEqual(clusterCreationDraftKey('a:b', 'c'), clusterCreationDraftKey('a', 'b:c'));
  });
  test('damaged or denied storage cannot stop cluster creation', () => {
    for (const raw of ['null', '{bad', '[]', 'true', '{"hostMode":"other","name":42}']) {
      assert.deepEqual(readClusterCreationDraft({ getItem: () => raw }, 'key'), emptyClusterCreationDraft);
    }
    const denied = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); }, removeItem: () => { throw new Error('denied'); } };
    assert.deepEqual(readClusterCreationDraft(denied, 'key'), emptyClusterCreationDraft);
    assert.doesNotThrow(() => saveClusterCreationDraft(denied, 'key', emptyClusterCreationDraft));
    assert.doesNotThrow(() => clearClusterCreationDraft(denied, 'key'));
    assert.deepEqual(readClusterCreationDraft({ getItem: () => '{"name":"draft","unknown":"ignore"}' }, 'key'), { ...emptyClusterCreationDraft, name: 'draft' });
  });
  test('untouched legacy drafts do not restore an assumed default namespace', () => {
    const oldBlank = { ...emptyClusterCreationDraft, namespace: 'default' };
    assert.deepEqual(readClusterCreationDraft({ getItem: () => JSON.stringify(oldBlank) }, 'key'), emptyClusterCreationDraft);
    const named = { ...oldBlank, name: 'Saved cluster' };
    assert.deepEqual(readClusterCreationDraft({ getItem: () => JSON.stringify(named) }, 'key'), named);
  });
  test('Gateway edits follow standard user endpoints while preserving custom or cleared ones', () => {
    const first = updateClusterCreationNamespace(emptyClusterCreationDraft, 'zyq');
    const changed = updateClusterCreationEndpoint(first, 'gateway', 'https://gateway.example.test');
    assert.equal(changed.gatewayHttp, changed.gateway);
    assert.equal(changed.gatewayWs, changed.gateway);
    assert.equal(changed.runtime, first.runtime);
    const custom = updateClusterCreationEndpoint({ ...changed, gatewayHttp: 'https://http.example.test', gatewayWs: '' }, 'gateway', 'https://next.example.test');
    assert.equal(custom.gatewayHttp, 'https://http.example.test');
    assert.equal(custom.gatewayWs, '');
  });
  test('automatic endpoints consistently use the current namespace', () => {
    const hosts = clusterCreationHosts(' preview ');
    assert.equal(hosts.gateway, 'http://jiuwenswarm-gateway.preview:19000');
    assert.equal(hosts.runtime, 'http://jiuwenswarm-agent-runtime.preview:8091');
    assert.equal(hosts.web, 'http://jiuwenswarm-web.preview:5173');
    assert.equal(hosts.gatewayWs, hosts.gatewayHttp);
    assert.deepEqual(clusterCreationHosts(' '), { gateway: '', runtime: '', web: '', gatewayHttp: '', gatewayWs: '' });
    assert.equal(emptyClusterCreationDraft.namespace, '');
  });
  test('legacy automatic drafts migrate using the addresses that would actually have been submitted', () => {
    const legacy = { ...emptyClusterCreationDraft, name: 'Saved cluster', namespace: 'zyq', gateway: 'http://old-custom:19000', hostMode: 'auto' };
    const restored = readClusterCreationDraft({ getItem: () => JSON.stringify(legacy) }, 'key');
    const migrated = normalizeClusterCreationDraft(restored);
    assert.equal(migrated.name, 'Saved cluster');
    assert.equal(migrated.hostMode, 'manual');
    assert.deepEqual(resolveClusterCreationHosts(migrated), clusterCreationHosts('zyq'));
    assert.equal(migrated.gateway.includes('old-custom'), false);
    const manual = { ...legacy, hostMode: 'manual', gatewayWs: '' };
    assert.deepEqual(normalizeClusterCreationDraft(manual), manual);
  });
  test('the first namespace fills endpoint suggestions and later changes preserve custom addresses', () => {
    const first = updateClusterCreationNamespace(normalizeClusterCreationDraft(emptyClusterCreationDraft), 'zyq');
    assert.deepEqual(resolveClusterCreationHosts(first), clusterCreationHosts('zyq'));
    const changed = updateClusterCreationNamespace({ ...first, web: 'https://user.example.test', gatewayWs: '' }, 'staging');
    assert.equal(changed.gateway, 'http://jiuwenswarm-gateway.staging:19000');
    assert.equal(changed.runtime, 'http://jiuwenswarm-agent-runtime.staging:8091');
    assert.equal(changed.gatewayHttp, 'http://jiuwenswarm-gateway.staging:19000');
    assert.equal(changed.web, 'https://user.example.test');
    assert.equal(changed.gatewayWs, '');
    assert.equal(changed.namespace, 'staging');
    assert.equal(first.namespace, 'zyq');
  });
  test('clearing a namespace clears only generated suggestions and never guesses default', () => {
    const first = updateClusterCreationNamespace(emptyClusterCreationDraft, 'zyq');
    const cleared = updateClusterCreationNamespace({ ...first, gateway: 'https://external.example.test' }, '');
    assert.equal(cleared.gateway, 'https://external.example.test');
    assert.equal(cleared.runtime, '');
    assert.equal(cleared.web, '');
    assert.equal(cleared.namespace, '');
    assert.deepEqual(resolveClusterCreationHosts(normalizeClusterCreationDraft(emptyClusterCreationDraft)), clusterCreationHosts(''));
  });
} finally { await server.close(); await rm(cacheDir, { recursive: true, force: true }); }
