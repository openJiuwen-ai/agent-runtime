import assert from 'node:assert/strict';
import { createServer } from 'vite';

const server = await createServer({
  appType: 'custom',
  configFile: false,
  server: { middlewareMode: true },
});

try {
  const {
    createDefaultExecutionGuardFormState,
    executionGuardBodyToFormState,
    executionGuardFormStateToBody,
    validateExecutionGuardForm,
  } = await server.ssrLoadModule('/src/pages/templates/executionGuardForm.ts');

  const defaults = createDefaultExecutionGuardFormState();
  const body = executionGuardFormStateToBody(defaults);

  assert.equal(body.model_anomaly_detection_rail.transient_max_retries, 3);
  assert.equal(body.model_anomaly_detection_rail.enabled, true);
  assert.equal(body.circuit_breaker.enabled, false);
  assert.equal(body.circuit_breaker.critical_threshold, 20);
  assert.equal(validateExecutionGuardForm(defaults), null);
  assert.deepEqual(executionGuardBodyToFormState(body), defaults);
  assert.equal(Object.hasOwn(body, 'backoff_seconds'), false);

  const windowError = validateExecutionGuardForm({ ...defaults, repeatWindowChars: 10 });
  assert.equal(windowError.code, 'window');
  assert.equal(windowError.minimum, 384);
  assert.equal(validateExecutionGuardForm({ ...defaults, criticalThreshold: 5 }).code, 'critical');
  assert.equal(validateExecutionGuardForm({ ...defaults, transientMaxRetries: 1.5 }).code, 'notInteger');
} finally {
  await server.close();
}
