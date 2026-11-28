import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const cacheDir = await mkdtemp(join(tmpdir(), 'cluster-wizard-test-'));
const server = await createServer({ cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { clusterWizardStep, clusterWizardPath, clusterSetupPath, legacyClusterWizardPath, carryClusterWizard, guidedClusterWizardPath, guidedClusterStage, guidedClusterResumeStage } = await server.ssrLoadModule('/src/utils/clusterWizard.ts');
  test('continue setup resumes the correct stage of the five-step wizard', () => {
    const sections = ['access', 'service-resources', 'agent-resources', 'status'];
    sections.forEach((section, index) => {
      const query = new URLSearchParams(guidedClusterWizardPath('saved-cluster', guidedClusterResumeStage(section)).split('?')[1]);
      assert.equal(query.get('instance'), 'saved-cluster');
      assert.equal(query.get('stage'), String(index + 2));
      assert.equal(legacyClusterWizardPath(query), null);
    });
  });
  test('guided flow resumes the saved instance without legacy redirects or unsafe return targets', () => {
    const path = guidedClusterWizardPath('cluster/one', 4, new URLSearchParams({ templateId: 'agent-one', returnTo: 'https://external.invalid' }));
    const query = new URLSearchParams(path.split('?')[1]);
    assert.equal(query.get('instance'), 'cluster/one');
    assert.equal(query.get('stage'), '4');
    assert.equal(query.get('template'), 'agent-one');
    assert.equal(query.get('returnTo'), '/agent-templates');
    assert.equal(legacyClusterWizardPath(query), null);
    for (const stage of ['1', '2', '3', '4', '5']) assert.equal(guidedClusterStage(stage), Number(stage));
    for (const stage of [null, '', '0', '6', '2.5', 'NaN', '01']) assert.equal(guidedClusterStage(stage), null);
    assert.equal(legacyClusterWizardPath(new URLSearchParams({ instance: 'c', stage: 'unknown' })), '/instances/c/cluster-config?setup=1');
  });
  test('created clusters enter actual admission without losing the template handoff', () => {
    const path = clusterSetupPath('cluster/one', 'access', new URLSearchParams({ templateId: 'agent-one', returnTo: '/agent-templates?search=客服&create=1' }));
    assert.equal(path.split('?')[0], '/instances/cluster%2Fone/access');
    const query = new URLSearchParams(path.split('?')[1]);
    assert.equal(query.get('setup'), '1');
    assert.equal(query.get('template'), 'agent-one');
    assert.equal(query.get('returnTo'), '/agent-templates?search=%E5%AE%A2%E6%9C%8D');
    assert.equal(query.has('instance'), false);
  });
  test('old wizard bookmarks route to actual saved cluster sections', () => {
    assert.equal(legacyClusterWizardPath(new URLSearchParams()), null);
    const expected = ['status', 'access', 'service-resources', 'status'];
    expected.forEach((section, step) => {
      const path = legacyClusterWizardPath(new URLSearchParams({ instance: 'one', step: String(step), template: 'agent' }));
      assert.equal(path, '/instances/one/' + section + '?setup=1&template=agent');
    });
    assert.equal(legacyClusterWizardPath(new URLSearchParams({ instance: 'one' })), '/instances/one/cluster-config?setup=1');
    assert.equal(clusterWizardPath('one', 1), '/instances/one/access?setup=1');
    assert.equal(clusterWizardStep('3', false), 0);
    assert.equal(clusterWizardStep(null, true), 1);
    for (const invalid of ['-1', '4', 'NaN', '2.5', '']) assert.equal(clusterWizardStep(invalid, true), 1);
  });
  test('setup context stays within the same cluster across navigation', () => {
    const context = new URLSearchParams({ setup: '1', template: 'a', returnTo: '/agent-templates?search=a' });
    const path = carryClusterWizard('/instances/c/agent-resources?open=add', 'c', context);
    const query = new URLSearchParams(path.split('?')[1]);
    assert.equal(query.get('open'), 'add');
    assert.equal(query.get('setup'), '1');
    assert.equal(query.get('template'), 'a');
    assert.equal(query.get('returnTo'), '/agent-templates?search=a');
    assert.equal(carryClusterWizard('/instances/other/access', 'c', context), '/instances/other/access');
    assert.equal(carryClusterWizard('/instances', 'c', context), '/instances');
    assert.equal(carryClusterWizard('/instances/c/access', 'c', new URLSearchParams()), '/instances/c/access');
  });
  test('legacy workflow context becomes setup mode and return targets stay local', () => {
    const path = carryClusterWizard('/instances/c/status', 'c', new URLSearchParams({ wizardStep: '2', templateId: 'a', returnTo: 'https://other.test' }));
    const query = new URLSearchParams(path.split('?')[1]);
    assert.equal(query.get('setup'), '1');
    assert.equal(query.has('wizardStep'), false);
    assert.equal(query.get('returnTo'), '/agent-templates');
    assert.equal(query.get('template'), 'a');
  });
} finally { await server.close(); await rm(cacheDir, { recursive: true, force: true }); }
