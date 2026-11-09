import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';
const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { containerTemplateForm, containerTemplateBody } = await server.ssrLoadModule('/src/utils/containerTemplateForm.ts');
  const source = () => ({
    template_name: 'Deployment container', container_id: 'restore-container', name: 'agent', description: 'default',
    image: 'example/agent:1', image_pull_policy: 'IfNotPresent', enabled: false,
    ports: [{ name: 'health', containerPort: 8080, protocol: 'TCP' }],
    env: [{ name: 'TOKEN', valueFrom: { secretKeyRef: { name: 'deploy', key: 'token' } } }, { name: 'MULTILINE', value: 'a\nb' }],
    env_from: [{ prefix: 'SERVICE_', configMapRef: { name: 'config' } }],
    command: ['/bin/sh', '-c'], args: ['exec "$SERVER" --flag'],
    volume_mounts: [{ name: 'data', mountPath: '/data', readOnly: true, subPath: 'tenant' }],
    resources: { requests: { cpu: '250m', memory: '512Mi', 'ephemeral-storage': '2Gi' }, limits: { cpu: '1', memory: '1Gi', 'nvidia.com/gpu': '1' } },
    security_context: { runAsUser: 0, runAsGroup: 0, privileged: false, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true,
      capabilities: { add: ['NET_BIND_SERVICE'], drop: ['ALL'] }, seccompProfile: { type: 'Localhost', localhostProfile: 'profiles/agent.json' }, appArmorProfile: { type: 'Localhost', localhostProfile: 'agent' } },
    readiness_probe: { httpGet: { port: 'health', path: '/ready', scheme: 'HTTPS', httpHeaders: [{ name: 'X-Health', value: 'check' }] },
      failureThreshold: 10, successThreshold: 2, initialDelaySeconds: 0, periodSeconds: 3, timeoutSeconds: 4 },
    data: { deployment_default: { fingerprint: 'source' }, custom: { keep: true } },
  });
  test('container round-trip preserves deployment env references, commands, nested security, resources and named probe ports', () => {
    const row = source(); const before = structuredClone(row); const form = containerTemplateForm(row);
    assert.equal(form.probe_port, 'health'); assert.equal(form.run_as_user, '0'); assert.equal(form.env_text.startsWith('['), true);
    assert.deepEqual(containerTemplateBody(form, row), row); assert.deepEqual(row, before);
  });
  test('editing common fields retains nested extension fields', () => {
    const row = source(); const form = containerTemplateForm(row);
    Object.assign(form, { cpu_request: '500m', run_as_user: '1000', probe_port: '8443', probe_path: '/healthz', capabilities_add: '' });
    const result = containerTemplateBody(form, row);
    assert.deepEqual(result.resources.requests, { cpu: '500m', memory: '512Mi', 'ephemeral-storage': '2Gi' });
    assert.equal(result.security_context.runAsUser, 1000); assert.equal(result.security_context.allowPrivilegeEscalation, false);
    assert.deepEqual(result.security_context.capabilities, { drop: ['ALL'] });
    assert.deepEqual(result.readiness_probe.httpGet, { ...row.readiness_probe.httpGet, port: 8443, path: '/healthz' }); assert.equal(result.readiness_probe.failureThreshold, 10);
  });
  test('explicit clears remain empty rather than restoring deployment values', () => {
    const row = source(); const form = containerTemplateForm(row);
    for (const key of ['description', 'ports_json', 'env_text', 'env_from_json', 'command_json', 'args_json', 'volume_mounts_json', 'resource_extra_json', 'security_extra_json']) form[key] = '';
    form.probe_enabled = false; const result = containerTemplateBody(form, row);
    for (const key of ['ports', 'env', 'env_from', 'command', 'args', 'volume_mounts']) assert.deepEqual(result[key], []);
    assert.deepEqual(result.resources, {}); assert.deepEqual(result.security_context, {}); assert.equal(result.description, ''); assert.equal(result.readiness_probe, null);
  });
  test('absent and custom probes round-trip without adding an HTTP health check', () => {
    for (const probe of [null, { exec: { command: ['test', '-f', '/ready'] }, failureThreshold: 5 }, { grpc: { port: 9090, service: 'ready' } }]) {
      const row = { ...source(), readiness_probe: probe }; const form = containerTemplateForm(row);
      assert.equal(form.probe_enabled, probe !== null); assert.equal(form.probe_type, probe ? 'custom' : 'httpGet');
      assert.deepEqual(containerTemplateBody(form, row).readiness_probe, probe);
    }
    const form = containerTemplateForm(); assert.equal(form.probe_enabled, false); assert.equal(containerTemplateBody(form).readiness_probe, null);
  });
  test('advanced JSON changes persist and changed common controls take precedence', () => {
    const row = source(); const form = containerTemplateForm(row);
    form.resource_extra_json = JSON.stringify({ requests: { cpu: '2', 'hugepages-2Mi': '80Mi' } }); form.cpu_request = '750m';
    form.security_extra_json = JSON.stringify({ runAsUser: 0, seLinuxOptions: { level: 's0' } });
    const result = containerTemplateBody(form, row);
    assert.deepEqual(result.resources, { requests: { cpu: '750m', 'hugepages-2Mi': '80Mi' } }); assert.deepEqual(result.security_context, { runAsUser: 0, seLinuxOptions: { level: 's0' } });
  });
  test('changing a Localhost security profile clears the now-inapplicable local profile path', () => {
    const row = source(); const form = containerTemplateForm(row);
    form.seccomp_type = 'RuntimeDefault'; form.apparmor_type = 'Unconfined';
    const result = containerTemplateBody(form, row);
    assert.deepEqual(result.security_context.seccompProfile, { type: 'RuntimeDefault' });
    assert.deepEqual(result.security_context.appArmorProfile, { type: 'Unconfined' });
  });
  test('environment values preserve equals and whitespace; malformed edits fail', () => {
    const row = source(); const form = containerTemplateForm(row); form.env_text = 'KEY=  value=x  \nOTHER=\n# note';
    assert.deepEqual(containerTemplateBody(form, row).env, [{ name: 'KEY', value: '  value=x  ' }, { name: 'OTHER', value: '' }]);
    for (const [key, value] of [['ports_json', '{}'], ['args_json', '[1]'], ['env_text', 'missing_equals'], ['run_as_user', '-1'], ['cpu_limit', '2banana'], ['memory_request', '123'], ['probe_port', '99999'], ['security_extra_json', '[]']]) {
      assert.throws(() => containerTemplateBody({ ...containerTemplateForm(row), [key]: value }, row), Error);
    }
  });
  test('metadata survives without mutating source or sending legacy container upserts', () => {
    const row = source(); row.data.config_sync = { source_template_id: 'source', containers: [{ container_id: 'old' }] };
    const result = containerTemplateBody(containerTemplateForm(row), row);
    assert.deepEqual(result.data.config_sync, { source_template_id: 'source' }); result.data.custom.keep = false;
    assert.equal(row.data.custom.keep, true); assert.equal(row.data.config_sync.containers.length, 1);
  });
} finally { await server.close(); }
