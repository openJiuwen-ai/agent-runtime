import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { activeAdmissionRules, deriveClusterSetupProgress, isActiveConfiguration } = await server.ssrLoadModule('/src/utils/clusterSetupProgress.ts');
  const now = Date.parse('2026-11-02T12:00:00Z');
  const instance = { jiuwenclaw_id: 'one', gateway_status: 'pending', runtime_status: 'pending', user_web_status: 'pending' };
  const allow = { enabled: true, expires_at: null, subject_type: 'user', subject_id: 'user-one', login_policy: 'allow' };
  const binding = { enabled: true, expires_at: null };
  const input = { instance, users: [], orgs: [], agents: [], runtimes: [] };

  test('re-entry selects the first missing saved configuration in dependency order', () => {
    assert.equal(deriveClusterSetupProgress(input, now).nextSection, 'access');
    const admitted = { ...input, users: [allow] };
    assert.equal(deriveClusterSetupProgress(admitted, now).nextSection, 'service-resources');
    const runtime = { ...admitted, runtimes: [binding] };
    assert.equal(deriveClusterSetupProgress(runtime, now).nextSection, 'agent-resources');
    const complete = deriveClusterSetupProgress({ ...runtime, agents: [binding] }, now);
    assert.equal(complete.nextSection, 'status');
    assert.equal(complete.completedConfigurations, 3);
    assert.equal(complete.configurationComplete, true);
    assert.equal(complete.connected, false, 'saved offline configuration must not imply live connectivity');
  });

  test('denied, expired or disabled rules cannot complete admission', () => {
    assert.equal(activeAdmissionRules([{ ...allow, enabled: false }], now).length, 0);
    assert.equal(activeAdmissionRules([{ ...allow, login_policy: 'deny' }], now).length, 0);
    assert.equal(activeAdmissionRules([{ ...allow, expires_at: '2026-11-02T12:00:00Z' }], now).length, 0);
    assert.equal(activeAdmissionRules([allow, { ...allow, login_policy: 'deny' }], now).length, 0);
    assert.equal(activeAdmissionRules([allow, { ...allow, login_policy: 'deny', enabled: false }], now).length, 1);
    const progress = deriveClusterSetupProgress({ ...input, orgs: [{ ...allow, subject_type: 'org' }] }, now);
    assert.equal(progress.admission.total, 1);
    assert.equal(progress.nextSection, 'service-resources');
  });

  test('resource expiry checks UTC timestamps and ignore disabled bindings', () => {
    assert.equal(isActiveConfiguration({ ...binding, expires_at: '2026-11-02T12:00:00' }, now), false);
    assert.equal(isActiveConfiguration({ ...binding, expires_at: '2026-11-02T13:00:00' }, now), true);
    assert.equal(isActiveConfiguration({ ...binding, expires_at: 'invalid' }, now), false);
    const progress = deriveClusterSetupProgress({ ...input, users: [allow], agents: [binding], runtimes: [{ ...binding, enabled: false }] }, now);
    assert.equal(progress.nextSection, 'service-resources');
    assert.equal(progress.configurationComplete, false);
  });
} finally {
  await server.close();
}
