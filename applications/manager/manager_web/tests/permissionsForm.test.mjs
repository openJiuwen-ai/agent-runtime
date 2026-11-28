import assert from 'node:assert/strict';
import { createServer } from 'vite';

globalThis.document = { documentElement: { lang: '' } };

const server = await createServer({
  appType: 'custom',
  configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false },
});

try {
  const {
    createDefaultPermissionsFormState,
    permissionsBodyToFormState,
    permissionsFormStateToBody,
  } = await server.ssrLoadModule(
    '/src/pages/instance/instanceConfigPanel/permissionsForm.ts',
  );

  const loaded = permissionsBodyToFormState({
    enabled: true,
    skill_authorization: { enabled: true },
  });
  assert.equal(
    loaded.skillAuthorizationEnabled,
    true,
    'managed permissions body should restore dynamic skill authorization',
  );

  const form = createDefaultPermissionsFormState();
  form.skillAuthorizationEnabled = true;
  assert.deepEqual(
    permissionsFormStateToBody(form).skill_authorization,
    { enabled: true },
    'managed permissions body should persist dynamic skill authorization',
  );
} finally {
  await server.close();
}
