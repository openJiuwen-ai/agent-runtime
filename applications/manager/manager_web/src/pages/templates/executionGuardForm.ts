/** 执行护栏表单与 body 互转。默认值写在前端，不读取 config.yaml。 */

export interface ExecutionGuardFormState {
  anomalyEnabled: boolean;
  maxRetries: number;
  transientMaxRetries: number;
  repeatMinPatternChars: number;
  repeatMaxPatternChars: number;
  repeatMinCount: number;
  repeatMinTotalChars: number;
  repeatWindowChars: number;
  singleCharRepeatCount: number;
  compactEnabled: boolean;
  consecutiveThreshold: number;
  bailoutThreshold: number;
  breakerEnabled: boolean;
  warningThreshold: number;
  criticalThreshold: number;
  globalBreakerThreshold: number;
  unknownToolThreshold: number;
}

export type ExecutionGuardFormError =
  | { code: 'notInteger' | 'belowMinimum'; field: keyof ExecutionGuardFormState; minimum?: number }
  | { code: 'repeatMax' }
  | { code: 'window'; minimum: number }
  | { code: 'critical' };

export function createDefaultExecutionGuardFormState(): ExecutionGuardFormState {
  return {
    anomalyEnabled: true,
    maxRetries: 2,
    transientMaxRetries: 3,
    repeatMinPatternChars: 2,
    repeatMaxPatternChars: 64,
    repeatMinCount: 6,
    repeatMinTotalChars: 160,
    repeatWindowChars: 1024,
    singleCharRepeatCount: 90,
    compactEnabled: true,
    consecutiveThreshold: 4,
    bailoutThreshold: 3,
    breakerEnabled: false,
    warningThreshold: 10,
    criticalThreshold: 20,
    globalBreakerThreshold: 30,
    unknownToolThreshold: 10,
  };
}

function readBool(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function readInt(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isInteger(value) ? value : fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function executionGuardBodyToFormState(body: Record<string, unknown>): ExecutionGuardFormState {
  const defaults = createDefaultExecutionGuardFormState();
  const rail = asRecord(body.model_anomaly_detection_rail);
  const compact = asRecord(rail.tool_loop_compact);
  const breaker = asRecord(body.circuit_breaker);
  return {
    anomalyEnabled: readBool(rail.enabled, defaults.anomalyEnabled),
    maxRetries: readInt(rail.max_retries, defaults.maxRetries),
    transientMaxRetries: readInt(rail.transient_max_retries, defaults.transientMaxRetries),
    repeatMinPatternChars: readInt(rail.repeat_min_pattern_chars, defaults.repeatMinPatternChars),
    repeatMaxPatternChars: readInt(rail.repeat_max_pattern_chars, defaults.repeatMaxPatternChars),
    repeatMinCount: readInt(rail.repeat_min_count, defaults.repeatMinCount),
    repeatMinTotalChars: readInt(rail.repeat_min_total_chars, defaults.repeatMinTotalChars),
    repeatWindowChars: readInt(rail.repeat_window_chars, defaults.repeatWindowChars),
    singleCharRepeatCount: readInt(rail.single_char_repeat_count, defaults.singleCharRepeatCount),
    compactEnabled: readBool(compact.enabled, defaults.compactEnabled),
    consecutiveThreshold: readInt(compact.consecutive_threshold, defaults.consecutiveThreshold),
    bailoutThreshold: readInt(compact.bailout_threshold, defaults.bailoutThreshold),
    breakerEnabled: readBool(breaker.enabled, defaults.breakerEnabled),
    warningThreshold: readInt(breaker.warning_threshold, defaults.warningThreshold),
    criticalThreshold: readInt(breaker.critical_threshold, defaults.criticalThreshold),
    globalBreakerThreshold: readInt(breaker.global_breaker_threshold, defaults.globalBreakerThreshold),
    unknownToolThreshold: readInt(breaker.unknown_tool_threshold, defaults.unknownToolThreshold),
  };
}

export function executionGuardFormStateToBody(form: ExecutionGuardFormState): Record<string, unknown> {
  return {
    model_anomaly_detection_rail: {
      enabled: form.anomalyEnabled,
      max_retries: form.maxRetries,
      transient_max_retries: form.transientMaxRetries,
      repeat_min_pattern_chars: form.repeatMinPatternChars,
      repeat_max_pattern_chars: form.repeatMaxPatternChars,
      repeat_min_count: form.repeatMinCount,
      repeat_min_total_chars: form.repeatMinTotalChars,
      repeat_window_chars: form.repeatWindowChars,
      single_char_repeat_count: form.singleCharRepeatCount,
      tool_loop_compact: {
        enabled: form.compactEnabled,
        consecutive_threshold: form.consecutiveThreshold,
        bailout_threshold: form.bailoutThreshold,
      },
    },
    circuit_breaker: {
      enabled: form.breakerEnabled,
      warning_threshold: form.warningThreshold,
      critical_threshold: form.criticalThreshold,
      global_breaker_threshold: form.globalBreakerThreshold,
      unknown_tool_threshold: form.unknownToolThreshold,
    },
  };
}

const INTEGER_FIELDS: Array<[keyof ExecutionGuardFormState, number]> = [
  ['maxRetries', 0],
  ['transientMaxRetries', 0],
  ['repeatMinPatternChars', 1],
  ['repeatMaxPatternChars', 1],
  ['repeatMinCount', 2],
  ['repeatMinTotalChars', 1],
  ['repeatWindowChars', 1],
  ['singleCharRepeatCount', 2],
  ['consecutiveThreshold', 2],
  ['bailoutThreshold', 0],
  ['warningThreshold', 1],
  ['criticalThreshold', 1],
  ['globalBreakerThreshold', 1],
  ['unknownToolThreshold', 1],
];

export function validateExecutionGuardForm(form: ExecutionGuardFormState): ExecutionGuardFormError | null {
  for (const [field, minimum] of INTEGER_FIELDS) {
    const value = form[field];
    if (typeof value !== 'number' || !Number.isInteger(value)) {
      return { code: 'notInteger', field };
    }
    if (value < minimum) return { code: 'belowMinimum', field, minimum };
  }
  if (form.repeatMaxPatternChars < form.repeatMinPatternChars) return { code: 'repeatMax' };
  const minWindow = Math.max(
    form.repeatMaxPatternChars * form.repeatMinCount,
    form.repeatMinTotalChars,
    form.singleCharRepeatCount,
  );
  if (form.repeatWindowChars < minWindow) return { code: 'window', minimum: minWindow };
  if (form.criticalThreshold < form.warningThreshold) return { code: 'critical' };
  return null;
}
