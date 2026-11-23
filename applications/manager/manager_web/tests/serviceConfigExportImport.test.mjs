import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';

const server = await createServer({
  appType: 'custom',
  configFile: false,
  server: { middlewareMode: true, ws: false },
});
after(() => server.close());

const {
  parseConfigSyncImport,
  remapContainerId,
  remapCreateBodyContainerIds,
} = await server.ssrLoadModule('/src/utils/serviceConfigExport.ts');

test('remapContainerId appends suffix and respects max length', () => {
  assert.equal(remapContainerId('c-agentserver', 'abcd1234'), 'c-agentserver-abcd1234');
  const long = `c-${'x'.repeat(120)}`;
  const out = remapContainerId(long, 'abcd1234');
  assert.ok(out.length <= 100);
  assert.ok(out.endsWith('-abcd1234'));
});

test('parseConfigSyncImport remaps fixed container ids so upsert will create new rows', () => {
  const { body, templateCount } = parseConfigSyncImport({
    type: 'config_sync',
    rawdata: {
      containers: [
        {
          container_id: 'c-agentserver',
          name: 'agentserver',
          image: 'example/agentserver:dev',
          volumeMounts: [{ name: 'swarm-code', mountPath: '/app' }],
        },
        {
          container_id: 'c-jiuwenbox',
          name: 'jiuwenbox',
          image: 'example/box:dev',
        },
      ],
      templates: [
        {
          template_id: 'default',
          template_name: 'default',
          main_container_id: 'c-agentserver',
          sidecar_container_ids: ['c-jiuwenbox'],
          volumes: [{ name: 'swarm-code', hostPath: { path: '/code', type: 'Directory' } }],
        },
      ],
      scopes: [],
    },
  });

  assert.equal(templateCount, 1);
  assert.notEqual(body.main_container_id, 'c-agentserver');
  assert.match(body.main_container_id, /^c-agentserver-[0-9a-f]{12}$/);
  assert.equal(body.sidecar_container_ids?.length, 1);
  assert.match(body.sidecar_container_ids[0], /^c-jiuwenbox-[0-9a-f]{12}$/);

  const containers = body.data?.config_sync?.containers ?? [];
  assert.equal(containers.length, 2);
  const ids = containers.map((c) => c.container_id);
  assert.deepEqual(ids.sort(), [body.main_container_id, body.sidecar_container_ids[0]].sort());
  // 同一批导入共用短后缀
  assert.equal(
    body.main_container_id.slice(-8),
    body.sidecar_container_ids[0].slice(-8),
  );
  assert.equal(body.volumes?.length, 1);
});

test('remapCreateBodyContainerIds is a no-op when no inline containers', () => {
  const body = {
    template_name: 'x',
    main_container_id: 'c-existing',
    data: { config_sync: { source_template_id: 'default' } },
  };
  assert.deepEqual(remapCreateBodyContainerIds(body), body);
});
