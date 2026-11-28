import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const cacheDir = await mkdtemp(join(tmpdir(), 'wizard-resource-test-'));
const server = await createServer({ cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { activeWizardAgentContexts, activeRuntimeTemplateIds, initialWizardResource, runtimePriorityConflict, matchingAttemptResource, ensureWizardAgentDefinition } = await server.ssrLoadModule('/src/utils/wizardResources.ts');
  const row = { resource_id: 'binding-1', resource_name: 'My runtime', ref_template_id: 'runtime-a', match_expr: '', priority: 3, enabled: true, expires_at: null };
  await test('an explicit template handoff never selects an unrelated existing binding', () => {
    assert.equal(initialWizardResource([row], 'runtime-new'), undefined);
    assert.equal(initialWizardResource([row], 'runtime-a'), row);
    const expired = {...row, resource_id: 'expired', expires_at: '2000-01-01T00:00:00Z'};
    assert.equal(initialWizardResource([expired, row]), row);
  });
  await test('runtime choices exclude expired, disabled and missing templates and de-duplicate bindings', () => {
    assert.deepEqual(activeRuntimeTemplateIds([row, { ...row, resource_id: 'duplicate' }, { ...row, ref_template_id: 'disabled', enabled: false }, { ...row, ref_template_id: 'expired', expires_at: '2000-01-01T00:00:00Z' }, { ...row, ref_template_id: 'missing' }], ['runtime-a', 'disabled', 'expired']), ['runtime-a']);
  });
  await test('enabled runtime priorities are unique across scopes, and editing excludes own binding', () => {
    assert.equal(runtimePriorityConflict([row], 3), true);
    assert.equal(runtimePriorityConflict([row], 3, 'binding-1'), false);
    assert.equal(runtimePriorityConflict([{ ...row, enabled: false }], 3), false);
  });
  await test('a partial binding retry only reuses its exact resource and preserves server timestamp formatting', () => {
    const attempt = { templateId: 'runtime-a', name: 'My runtime', scope: '', priority: 3, enabled: true, expiresAt: null };
    assert.equal(matchingAttemptResource([row], attempt), row);
    assert.equal(matchingAttemptResource([row], { ...attempt, name: 'Another binding' }), undefined);
    assert.equal(matchingAttemptResource([row], { ...attempt, scope: "user_id in ('bob')" }), undefined);
    assert.equal(matchingAttemptResource([row], { ...attempt, priority: 4 }), undefined);
    const timed = { ...row, expires_at: '2030-01-01T00:00:00+00:00' };
    assert.equal(matchingAttemptResource([timed], { ...attempt, expiresAt: '2030-01-01T00:00:00.000Z' }), timed);
  });
  const definition = { agent_id: 'agent-1', agent_config_template_id: 'config-a', runtime_template_id: 'runtime-a', agent_name: 'Existing Agent' };
  await test('account readiness requires its own active binding and exact template pair, even when another pair is valid', () => {
    const agent = { ...row, resource_id: 'agent-binding', ref_template_id: 'config-a' };
    const context = { bot_id: 'agent-binding', jiuwenclaw_id: 'cluster-a', agent_template_id: 'config-a', runtime_template_id: 'runtime-a' };
    const filter = (contexts, agents = [agent], runtimes = [row], definitions = [definition]) => activeWizardAgentContexts(contexts, 'cluster-a', agents, runtimes, definitions);
    assert.deepEqual(filter([context]), [context]);
    assert.deepEqual(filter([{ ...context, jiuwenclaw_id: 'another-cluster' }]), []);
    assert.deepEqual(filter([{ ...context, bot_id: 'disabled-binding' }]), []);
    assert.deepEqual(filter([{ ...context, agent_template_id: 'disabled-template' }]), []);
    assert.deepEqual(filter([{ ...context, runtime_template_id: 'missing-runtime' }]), []);
    assert.deepEqual(filter([context], [{ ...agent, enabled: false }]), []);
    assert.deepEqual(filter([context], [agent], [{ ...row, expires_at: '2000-01-01T00:00:00Z' }]), []);
    assert.deepEqual(filter([context], [agent], [row], [{ ...definition, runtime_template_id: 'another-runtime' }]), []);
  });
  await test('an exact Agent definition pair is reused without creating another global Agent', async () => {
    let creates = 0;
    const saved = await ensureWizardAgentDefinition({ list: async () => [definition], create: async () => { creates++; return definition; } }, 'config-a', 'runtime-a');
    assert.equal(saved, definition); assert.equal(creates, 0);
  });
  await test('different runtime pair creates an independent Agent definition', async () => {
    let creates = 0;
    await ensureWizardAgentDefinition({ list: async () => [definition], create: async () => { creates++; return { ...definition, runtime_template_id: 'runtime-b' }; } }, 'config-a', 'runtime-b');
    assert.equal(creates, 1);
  });
  await test('a concurrent save or lost create response recovers the exact pair on retry', async () => {
    let lists = 0;
    assert.equal(await ensureWizardAgentDefinition({ list: async () => ++lists === 1 ? [] : [definition], create: async () => { throw new Error('lost response'); } }, 'config-a', 'runtime-a'), definition);
    assert.equal(lists, 2);
  });
  await test('definition failure is propagated when no exact pair was saved', async () => {
    await assert.rejects(() => ensureWizardAgentDefinition({ list: async () => [], create: async () => { throw new Error('permission denied'); } }, 'config-a', 'runtime-a'), /permission denied/);
  });
} finally { await server.close(); await rm(cacheDir, { recursive: true, force: true }); }
