import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';
const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { clusterCreationDraftKey, emptyClusterCreationDraft, readClusterCreationDraft, saveClusterCreationDraft, clearClusterCreationDraft, clusterCreationHosts } = await server.ssrLoadModule('/src/utils/clusterCreationDraft.ts');
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
  test('automatic endpoints consistently use the current namespace', () => {
    const hosts = clusterCreationHosts(' preview ');
    assert.equal(hosts.gateway, 'http://jiuwenswarm-gateway.preview:19000');
    assert.equal(hosts.runtime, 'http://jiuwenswarm-agent-runtime.preview:8091');
    assert.equal(hosts.web, 'http://jiuwenswarm-web.preview:5173');
    assert.equal(hosts.gatewayWs, hosts.gatewayHttp);
    assert.equal(clusterCreationHosts('').gateway, 'http://jiuwenswarm-gateway.default:19000');
  });
} finally { await server.close(); }
