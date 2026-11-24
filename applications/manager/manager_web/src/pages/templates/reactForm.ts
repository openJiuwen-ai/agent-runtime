import { safeStringify } from '../../utils/format';
import { jsonContentForValidation } from '../../utils/jsonExample';

/**
 * React 模板 body 的结构化表单状态（前端维护的 schema）。
 *
 * 分组：基础 / 子Agent / 上下文引擎 / 自定义 JSON。
 * 每组结构化其已知的键，其余键落入该组的 extraJson（或顶层 extraJson），
 * 保底不丢数据。新建模板的模型配置由模型槽位（default_model）统一掌管；
 * 已存模板 body 中的历史模型三键（model_name 等）经顶层 extraJson 原样透传，
 * 编辑保存时不静默丢弃。
 *
 * 参数集按企业版整理裁剪：仅保留《react配置参数总结》E 列为空的字段；
 * evolution / ttse / a2x_registry 整组不结构化，已存模板中的旧键经顶层
 * extraJson 透传。新增字段默认留空 = 省略键，由运行时自身默认值兜底。
 */

export interface ReactSubagentEntry {
  key: string;
  name: string;
  /** 三态：null = 未配置（省略键），true = 开启，false = 关闭 */
  enabled: boolean | null;
  maxIterations: number | null;
  /** 该 subagent 的其余键 */
  extraJson: string;
}

export interface CompressionRecallFormState {
  /** 三态：null = 未配置（省略键），true = 开启，false = 关闭 */
  enabled: boolean | null;
  chunkSizeTokens: number | null;
  chunkOverlapTokens: number | null;
  extraJson: string;
}

export interface MessageSummaryOffloaderFormState {
  /** list[string] 的逗号分隔文本；空 = 省略 */
  protectedToolNames: string;
  addMessageThresholdRatio: number | null;
  ttlSeconds: number | null;
  ttlContextOccupancyRatio: number | null;
  ttlMessageThresholdRatio: number | null;
  extraJson: string;
}

export interface SessionMemoryCompressorFormState {
  /** 三态：null = 未配置（省略键），true = 开启，false = 关闭 */
  enabled: boolean | null;
  triggerContextRatio: number | null;
  sessionMemoryPath: string;
  maxNotesChars: number | null;
  memoryUpdateTriggerContextRatio: number | null;
  memoryUpdateMode: string;
  memoryDirectReplaceMaxRetries: number | null;
  /** memory 子对象的其余键 */
  memoryExtraJson: string;
  /** 本组其余键 */
  extraJson: string;
}

export interface DialogueCompressorFormState {
  triggerContextRatio: number | null;
  minTargetContextRatio: number | null;
  /** 引用模型模板的 template_id；空 = 继承主 Agent 模型 */
  modelTemplateId: string;
  extraJson: string;
}

export interface RoundCompressorFormState {
  triggerContextRatio: number | null;
  minTargetContextRatio: number | null;
  keepRecentMessages: number | null;
  /** 引用模型模板的 template_id；空 = 继承主 Agent 模型 */
  modelTemplateId: string;
  extraJson: string;
}

export interface ContextEngineFormState {
  /** 三态：null = 未配置（省略键），true = 开启，false = 关闭 */
  enabled: boolean | null;
  /** 上下文窗口上限；null = 省略 */
  contextWindowTokens: number | null;
  /** 模型级上下文窗口映射（kv JSON 文本），空 = 省略 */
  modelContextWindowTokensJson: string;
  enableReload: boolean | null;
  enableTiktokenCounter: boolean | null;
  /** 模型词表映射（kv JSON 文本），空 = 省略 */
  tokenizerRegistryJson: string;
  /** 上下文词表映射（kv JSON 文本），空 = 省略 */
  tokenizerSpecJson: string;
  tokenizerCacheDir: string;
  tokenizerOffline: boolean | null;
  compressionRecall: CompressionRecallFormState;
  messageSummaryOffloader: MessageSummaryOffloaderFormState;
  sessionMemory: SessionMemoryCompressorFormState;
  dialogueCompressor: DialogueCompressorFormState;
  currentRoundCompressor: RoundCompressorFormState;
  roundLevelCompressor: RoundCompressorFormState;
  /** 上述键之外的其余键（如调试落盘、OpenRouter 窗口开关等） */
  extraJson: string;
}

export interface ReactFormState {
  // 基础
  agentName: string;
  skillMode: string;
  /** 三态：null = 未配置（省略键），true = 开启，false = 关闭 */
  enableTaskLoop: boolean | null;
  /** read_file 图片输入策略：null=未配置/自动探测，false=纯文本，true=多模态 */
  enableReadImageMultimodal: boolean | null;
  /** 单轮任务循环等待上限（秒）；null 表示未配置 */
  completionTimeout: number | null;
  /** 主 Agent 内层 ReAct 轮次上限；null 表示未配置 */
  maxIterations: number | null;
  streamChunkThreshold: number | null;
  streamCharacterThreshold: number | null;
  // 子Agent
  /** 常驻子Agent运行时开关：null = 未配置（省略键），true = 开启，false = 关闭 */
  subagentRuntimeEnabled: boolean | null;
  /** subagent_runtime 中 enabled 之外的其余键（保底透传，不丢数据） */
  subagentRuntimeExtraJson: string;
  subagents: ReactSubagentEntry[];
  // 上下文引擎
  contextEngine: ContextEngineFormState;
  /** 自定义 JSON 空间：除以上分组外的全部 react 键 */
  extraJson: string;
}

/** 「基础」分组显式建模的顶层键。 */
const STRUCTURED_KEYS = new Set([
  'agent_name',
  'skill_mode',
  'enable_task_loop',
  'enable_read_image_multimodal',
  'completion_timeout',
  'max_iterations',
  'stream_chunk_threshold',
  'stream_character_threshold',
]);

/** 其中数值形态的键：非数字形态的原值需保留透传，避免保存时静默丢弃。 */
const NUMBER_STRUCTURED_KEYS = new Set([
  'completion_timeout',
  'max_iterations',
  'stream_chunk_threshold',
  'stream_character_threshold',
]);

/** 其中布尔形态的键：非布尔形态的原值需保留透传，避免保存时静默丢弃。 */
const BOOL_STRUCTURED_KEYS = new Set(['enable_task_loop', 'enable_read_image_multimodal']);

const SUBAGENT_KEYS = new Set(['subagents', 'subagent_runtime']);
const CONTEXT_ENGINE_KEY = 'context_engine_config';

/** 结构化表单拥有的顶层键，不应出现在自定义 JSON 空间（否则会被表单静默覆盖）。 */
const RESERVED_EXTRA_KEYS = new Set([
  ...STRUCTURED_KEYS,
  ...SUBAGENT_KEYS,
  CONTEXT_ENGINE_KEY,
]);

// ---------------------------------------------------------------------------
// 通用小工具
// ---------------------------------------------------------------------------

let _rowKey = 0;
function nextKey(prefix: string): string {
  _rowKey += 1;
  return `${prefix}-${_rowKey}`;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((x) => typeof x === 'string');
}

function asRecord(value: unknown): Record<string, unknown> {
  return isPlainRecord(value) ? value : {};
}

function asString(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asTriBool(value: unknown): boolean | null {
  // 严格解析：仅接受布尔与 "true"/"false" 字符串；其它形态按未配置处理，
  // 避免 Boolean("false") 把字符串形态判成 true 固化进 body。
  if (value === true || value === 'true') return true;
  if (value === false || value === 'false') return false;
  return null;
}

function splitKnown(
  record: Record<string, unknown>,
  knownKeys: Set<string>,
): { known: Record<string, unknown>; rest: Record<string, unknown> } {
  const known: Record<string, unknown> = {};
  const rest: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(record)) {
    if (knownKeys.has(key)) known[key] = value;
    else rest[key] = value;
  }
  return { known, rest };
}

function stringifyOrEmpty(record: Record<string, unknown>): string {
  return Object.keys(record).length ? safeStringify(record, 2) : '';
}

/** number|null → 输入框文本（空串表示未配置）。 */
export function numberOrEmpty(value: number | null): string {
  return value === null ? '' : String(value);
}

/** 输入框文本 → number|null（空串/非法输入为 null）。 */
export function parseNumberOrNull(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/** 解析一段 JSON 文本为对象；空/非法/非对象一律返回 {}。 */
function parseJsonObject(raw: string): Record<string, unknown> {
  const content = jsonContentForValidation(raw);
  if (!content) return {};
  try {
    const value: unknown = JSON.parse(content);
    if (!isPlainRecord(value)) return {};
    return value;
  } catch {
    return {};
  }
}

/** 解析自定义 JSON 空间内容；空→{}，非法或非对象→undefined（对齐 MCP parseMcpCustomConfig）。 */
export function parseReactExtraJson(raw: string): Record<string, unknown> | undefined {
  const content = jsonContentForValidation(raw);
  if (!content) return {};
  try {
    const value: unknown = JSON.parse(content);
    if (!isPlainRecord(value)) return undefined;
    return value;
  } catch {
    return undefined;
  }
}

/** 结构化表单拥有的顶层键，不应出现在自定义 JSON 空间。 */
export function reservedReactExtraFields(custom: Record<string, unknown>): string[] {
  return Object.keys(custom).filter((key) => {
    if (!RESERVED_EXTRA_KEYS.has(key)) return false;
    // 分组键仅在值为 object 时与结构化表单冲突；非 object 形态无法结构化编辑，原样透传。
    if (SUBAGENT_KEYS.has(key) || key === CONTEXT_ENGINE_KEY) {
      return isPlainRecord(custom[key]);
    }
    // 已知数值/布尔标量键：非数字/非布尔形态无法被表单接管，原样透传不算冲突
    // （否则保留到 extraJson 的非标准形态值会导致模板无法保存）。
    if (NUMBER_STRUCTURED_KEYS.has(key)) {
      return asNumberOrNull(custom[key]) !== null;
    }
    if (BOOL_STRUCTURED_KEYS.has(key)) {
      return asTriBool(custom[key]) !== null;
    }
    return true;
  });
}

// ---------------------------------------------------------------------------
// 默认值
// ---------------------------------------------------------------------------

const DEFAULT_SUBAGENTS: Array<Omit<ReactSubagentEntry, 'key'>> = [
  { name: 'general_agent', enabled: null, maxIterations: null, extraJson: '' },
  { name: 'code_agent', enabled: null, maxIterations: null, extraJson: '' },
  { name: 'research_agent', enabled: null, maxIterations: null, extraJson: '' },
];

function defaultContextEngine(): ContextEngineFormState {
  return {
    // 默认全部「未配置」（null/空），保存时省略键，由运行时自身默认值兜底；
    // 避免把 3000/500/0.8 等示例值固化进 body 覆盖运行时默认。
    enabled: null,
    contextWindowTokens: null,
    modelContextWindowTokensJson: '',
    enableReload: null,
    enableTiktokenCounter: null,
    tokenizerRegistryJson: '',
    tokenizerSpecJson: '',
    tokenizerCacheDir: '',
    tokenizerOffline: null,
    compressionRecall: {
      enabled: null,
      chunkSizeTokens: null,
      chunkOverlapTokens: null,
      extraJson: '',
    },
    messageSummaryOffloader: {
      protectedToolNames: '',
      addMessageThresholdRatio: null,
      ttlSeconds: null,
      ttlContextOccupancyRatio: null,
      ttlMessageThresholdRatio: null,
      extraJson: '',
    },
    sessionMemory: {
      enabled: null,
      triggerContextRatio: null,
      sessionMemoryPath: '',
      maxNotesChars: null,
      memoryUpdateTriggerContextRatio: null,
      memoryUpdateMode: '',
      memoryDirectReplaceMaxRetries: null,
      memoryExtraJson: '',
      extraJson: '',
    },
    dialogueCompressor: {
      triggerContextRatio: null,
      minTargetContextRatio: null,
      modelTemplateId: '',
      extraJson: '',
    },
    currentRoundCompressor: {
      triggerContextRatio: null,
      minTargetContextRatio: null,
      keepRecentMessages: null,
      modelTemplateId: '',
      extraJson: '',
    },
    roundLevelCompressor: {
      triggerContextRatio: null,
      minTargetContextRatio: null,
      keepRecentMessages: null,
      modelTemplateId: '',
      extraJson: '',
    },
    extraJson: '',
  };
}

export function createDefaultReactFormState(): ReactFormState {
  return {
    agentName: 'main_agent',
    skillMode: 'all',
    enableTaskLoop: null,
    enableReadImageMultimodal: null,
    completionTimeout: null,
    maxIterations: null,
    streamChunkThreshold: null,
    streamCharacterThreshold: null,
    subagentRuntimeEnabled: null,
    subagentRuntimeExtraJson: '',
    subagents: DEFAULT_SUBAGENTS.map((s) => ({ ...s, key: nextKey('subagent') })),
    contextEngine: defaultContextEngine(),
    extraJson: '',
  };
}

// ---------------------------------------------------------------------------
// 上下文引擎：压缩器子组 body ↔ form 转换
// ---------------------------------------------------------------------------

/** 子组所有字段均未配置时返回 null（整组省略），否则返回 body 对象。 */
function nonEmptyOrNull(record: Record<string, unknown>): Record<string, unknown> | null {
  return Object.keys(record).length ? record : null;
}

/** 已知键的值不符合预期形态时原样透传到 rest，避免保存时静默丢弃。 */
function preserveNonConforming(
  rest: Record<string, unknown>,
  known: Record<string, unknown>,
  key: string,
  conforms: (v: unknown) => boolean,
): void {
  const raw = known[key];
  if (raw !== undefined && !conforms(raw)) rest[key] = raw;
}

const COMPRESSION_RECALL_FLAT_KEYS = new Set(['enabled', 'chunk_size_tokens', 'chunk_overlap_tokens']);

function compressionRecallBodyToForm(value: unknown): CompressionRecallFormState {
  const v = asRecord(value);
  const { known, rest } = splitKnown(v, COMPRESSION_RECALL_FLAT_KEYS);
  preserveNonConforming(rest, known, 'enabled', (x) => asTriBool(x) !== null);
  preserveNonConforming(rest, known, 'chunk_size_tokens', (x) => asNumberOrNull(x) !== null);
  preserveNonConforming(rest, known, 'chunk_overlap_tokens', (x) => asNumberOrNull(x) !== null);
  return {
    enabled: asTriBool(known.enabled),
    chunkSizeTokens: asNumberOrNull(known.chunk_size_tokens),
    chunkOverlapTokens: asNumberOrNull(known.chunk_overlap_tokens),
    extraJson: stringifyOrEmpty(rest),
  };
}

function compressionRecallFormToBody(form: CompressionRecallFormState): Record<string, unknown> | null {
  const out: Record<string, unknown> = { ...parseJsonObject(form.extraJson) };
  if (form.chunkSizeTokens !== null) out.chunk_size_tokens = form.chunkSizeTokens;
  if (form.chunkOverlapTokens !== null) out.chunk_overlap_tokens = form.chunkOverlapTokens;
  // enabled 三态：null=未配置（省略），true/false 显式写入，避免把未配置的子组强行注入 body。
  if (form.enabled !== null) out.enabled = form.enabled;
  return nonEmptyOrNull(out);
}

const MESSAGE_SUMMARY_FLAT_KEYS = new Set([
  'protected_tool_names',
  'add_message_threshold_ratio',
  'ttl_seconds',
  'ttl_context_occupancy_ratio',
  'ttl_message_threshold_ratio',
]);

function messageSummaryOffloaderBodyToForm(value: unknown): MessageSummaryOffloaderFormState {
  const v = asRecord(value);
  const { known, rest } = splitKnown(v, MESSAGE_SUMMARY_FLAT_KEYS);
  // protected_tool_names 仅接受 string 数组；其它形态留在 extraJson 原样透传
  const restAll: Record<string, unknown> = { ...rest };
  if (!isStringArray(known.protected_tool_names) && 'protected_tool_names' in known) {
    restAll.protected_tool_names = known.protected_tool_names;
  }
  preserveNonConforming(restAll, known, 'add_message_threshold_ratio', (x) => asNumberOrNull(x) !== null);
  preserveNonConforming(restAll, known, 'ttl_seconds', (x) => asNumberOrNull(x) !== null);
  preserveNonConforming(
    restAll,
    known,
    'ttl_context_occupancy_ratio',
    (x) => asNumberOrNull(x) !== null,
  );
  preserveNonConforming(
    restAll,
    known,
    'ttl_message_threshold_ratio',
    (x) => asNumberOrNull(x) !== null,
  );
  return {
    protectedToolNames: isStringArray(known.protected_tool_names) ? known.protected_tool_names.join(', ') : '',
    addMessageThresholdRatio: asNumberOrNull(known.add_message_threshold_ratio),
    ttlSeconds: asNumberOrNull(known.ttl_seconds),
    ttlContextOccupancyRatio: asNumberOrNull(known.ttl_context_occupancy_ratio),
    ttlMessageThresholdRatio: asNumberOrNull(known.ttl_message_threshold_ratio),
    extraJson: stringifyOrEmpty(restAll),
  };
}

function messageSummaryOffloaderFormToBody(form: MessageSummaryOffloaderFormState): Record<string, unknown> | null {
  const out: Record<string, unknown> = { ...parseJsonObject(form.extraJson) };
  const names = form.protectedToolNames
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (names.length) out.protected_tool_names = names;
  if (form.addMessageThresholdRatio !== null) out.add_message_threshold_ratio = form.addMessageThresholdRatio;
  if (form.ttlSeconds !== null) out.ttl_seconds = form.ttlSeconds;
  if (form.ttlContextOccupancyRatio !== null) out.ttl_context_occupancy_ratio = form.ttlContextOccupancyRatio;
  if (form.ttlMessageThresholdRatio !== null) out.ttl_message_threshold_ratio = form.ttlMessageThresholdRatio;
  return nonEmptyOrNull(out);
}

const SESSION_MEMORY_FLAT_KEYS = new Set([
  'enabled',
  'trigger_context_ratio',
  'session_memory_path',
  'max_notes_chars',
  'memory',
]);

const MEMORY_FLAT_KEYS = new Set(['update_trigger_context_ratio', 'update_mode', 'direct_replace_max_retries']);

function sessionMemoryBodyToForm(value: unknown): SessionMemoryCompressorFormState {
  const v = asRecord(value);
  const { known, rest } = splitKnown(v, SESSION_MEMORY_FLAT_KEYS);
  let memoryUpdateTriggerContextRatio: number | null = null;
  let memoryUpdateMode = '';
  let memoryDirectReplaceMaxRetries: number | null = null;
  let memoryExtraJson = '';
  const restAll: Record<string, unknown> = { ...rest };
  preserveNonConforming(restAll, known, 'enabled', (x) => asTriBool(x) !== null);
  preserveNonConforming(restAll, known, 'trigger_context_ratio', (x) => asNumberOrNull(x) !== null);
  preserveNonConforming(restAll, known, 'max_notes_chars', (x) => asNumberOrNull(x) !== null);
  if (isPlainRecord(known.memory)) {
    const memory = splitKnown(known.memory, MEMORY_FLAT_KEYS);
    preserveNonConforming(
      memory.rest,
      memory.known,
      'update_trigger_context_ratio',
      (x) => asNumberOrNull(x) !== null,
    );
    preserveNonConforming(
      memory.rest,
      memory.known,
      'update_mode',
      (x) => typeof x === 'string',
    );
    preserveNonConforming(
      memory.rest,
      memory.known,
      'direct_replace_max_retries',
      (x) => asNumberOrNull(x) !== null,
    );
    memoryUpdateTriggerContextRatio = asNumberOrNull(memory.known.update_trigger_context_ratio);
    memoryUpdateMode = typeof memory.known.update_mode === 'string' ? memory.known.update_mode : '';
    memoryDirectReplaceMaxRetries = asNumberOrNull(memory.known.direct_replace_max_retries);
    memoryExtraJson = stringifyOrEmpty(memory.rest);
  } else if ('memory' in known) {
    // 非 record 的 memory 值无法结构化编辑，原样透传
    restAll.memory = known.memory;
  }
  return {
    enabled: asTriBool(known.enabled),
    triggerContextRatio: asNumberOrNull(known.trigger_context_ratio),
    sessionMemoryPath: typeof known.session_memory_path === 'string' ? known.session_memory_path : '',
    maxNotesChars: asNumberOrNull(known.max_notes_chars),
    memoryUpdateTriggerContextRatio,
    memoryUpdateMode,
    memoryDirectReplaceMaxRetries,
    memoryExtraJson,
    extraJson: stringifyOrEmpty(restAll),
  };
}

function sessionMemoryFormToBody(form: SessionMemoryCompressorFormState): Record<string, unknown> | null {
  const out: Record<string, unknown> = { ...parseJsonObject(form.extraJson) };
  if (form.triggerContextRatio !== null) out.trigger_context_ratio = form.triggerContextRatio;
  if (form.sessionMemoryPath.trim()) out.session_memory_path = form.sessionMemoryPath.trim();
  if (form.maxNotesChars !== null) out.max_notes_chars = form.maxNotesChars;
  const memory: Record<string, unknown> = { ...parseJsonObject(form.memoryExtraJson) };
  if (form.memoryUpdateTriggerContextRatio !== null) {
    memory.update_trigger_context_ratio = form.memoryUpdateTriggerContextRatio;
  }
  if (form.memoryUpdateMode.trim()) memory.update_mode = form.memoryUpdateMode.trim();
  if (form.memoryDirectReplaceMaxRetries !== null) {
    memory.direct_replace_max_retries = form.memoryDirectReplaceMaxRetries;
  }
  if (Object.keys(memory).length) out.memory = memory;
  // enabled 三态：null=未配置（省略），true/false 显式写入，避免把未配置的子组强行注入 body。
  if (form.enabled !== null) out.enabled = form.enabled;
  return nonEmptyOrNull(out);
}

const DIALOGUE_FLAT_KEYS = new Set([
  'trigger_context_ratio',
  'min_target_context_ratio',
  'model_template_id',
]);

function dialogueCompressorBodyToForm(value: unknown): DialogueCompressorFormState {
  const v = asRecord(value);
  const { known, rest } = splitKnown(v, DIALOGUE_FLAT_KEYS);
  preserveNonConforming(rest, known, 'trigger_context_ratio', (x) => asNumberOrNull(x) !== null);
  preserveNonConforming(rest, known, 'min_target_context_ratio', (x) => asNumberOrNull(x) !== null);
  preserveNonConforming(rest, known, 'model_template_id', (x) => typeof x === 'string');
  return {
    triggerContextRatio: asNumberOrNull(known.trigger_context_ratio),
    minTargetContextRatio: asNumberOrNull(known.min_target_context_ratio),
    modelTemplateId: asString(known.model_template_id, ''),
    extraJson: stringifyOrEmpty(rest),
  };
}

function dialogueCompressorFormToBody(form: DialogueCompressorFormState): Record<string, unknown> | null {
  const out: Record<string, unknown> = { ...parseJsonObject(form.extraJson) };
  if (form.triggerContextRatio !== null) out.trigger_context_ratio = form.triggerContextRatio;
  if (form.minTargetContextRatio !== null) out.min_target_context_ratio = form.minTargetContextRatio;
  if (form.modelTemplateId.trim()) out.model_template_id = form.modelTemplateId.trim();
  return nonEmptyOrNull(out);
}

const ROUND_FLAT_KEYS = new Set([
  'trigger_context_ratio',
  'min_target_context_ratio',
  'keep_recent_messages',
  'model_template_id',
]);

function roundCompressorBodyToForm(value: unknown): RoundCompressorFormState {
  const v = asRecord(value);
  const { known, rest } = splitKnown(v, ROUND_FLAT_KEYS);
  preserveNonConforming(rest, known, 'trigger_context_ratio', (x) => asNumberOrNull(x) !== null);
  preserveNonConforming(rest, known, 'min_target_context_ratio', (x) => asNumberOrNull(x) !== null);
  preserveNonConforming(rest, known, 'keep_recent_messages', (x) => asNumberOrNull(x) !== null);
  preserveNonConforming(rest, known, 'model_template_id', (x) => typeof x === 'string');
  return {
    triggerContextRatio: asNumberOrNull(known.trigger_context_ratio),
    minTargetContextRatio: asNumberOrNull(known.min_target_context_ratio),
    keepRecentMessages: asNumberOrNull(known.keep_recent_messages),
    modelTemplateId: asString(known.model_template_id, ''),
    extraJson: stringifyOrEmpty(rest),
  };
}

function roundCompressorFormToBody(form: RoundCompressorFormState): Record<string, unknown> | null {
  const out: Record<string, unknown> = { ...parseJsonObject(form.extraJson) };
  if (form.triggerContextRatio !== null) out.trigger_context_ratio = form.triggerContextRatio;
  if (form.minTargetContextRatio !== null) out.min_target_context_ratio = form.minTargetContextRatio;
  if (form.keepRecentMessages !== null) out.keep_recent_messages = form.keepRecentMessages;
  if (form.modelTemplateId.trim()) out.model_template_id = form.modelTemplateId.trim();
  return nonEmptyOrNull(out);
}

// ---------------------------------------------------------------------------
// 上下文引擎 body ↔ form 转换
// ---------------------------------------------------------------------------

const CONTEXT_ENGINE_FLAT_KEYS = new Set([
  'enabled',
  'context_window_tokens',
  'model_context_window_tokens',
  'enable_reload',
  'enable_tiktoken_counter',
  'tokenizer_registry',
  'tokenizer_spec',
  'tokenizer_cache_dir',
  'tokenizer_offline',
]);

const CONTEXT_ENGINE_GROUP_KEYS = new Set([
  'compression_recall_config',
  'message_summary_offloader_config',
  'session_memory_compressor_config',
  'dialogue_compressor_config',
  'current_round_compressor_config',
  'round_level_compressor_config',
]);

function contextEngineBodyToForm(value: unknown): ContextEngineFormState {
  const v = asRecord(value);
  const flat = splitKnown(v, CONTEXT_ENGINE_FLAT_KEYS);
  const groups = splitKnown(flat.rest, CONTEXT_ENGINE_GROUP_KEYS);
  // 已知键的值不符合预期形态时原样透传到 extraJson（与 protected_tool_names / memory 一致），
  // 避免历史非标准类型值（数组/字符串/标量错位等）在保存时被静默丢弃。
  const restAll: Record<string, unknown> = { ...groups.rest };
  const preserve = (key: string, raw: unknown, conforms: (v: unknown) => boolean) => {
    if (raw !== undefined && !conforms(raw)) restAll[key] = raw;
  };
  preserve('model_context_window_tokens', flat.known.model_context_window_tokens, isPlainRecord);
  preserve('tokenizer_registry', flat.known.tokenizer_registry, isPlainRecord);
  preserve('tokenizer_spec', flat.known.tokenizer_spec, isPlainRecord);
  preserve('enabled', flat.known.enabled, (v) => asTriBool(v) !== null);
  preserve('enable_reload', flat.known.enable_reload, (v) => asTriBool(v) !== null);
  preserve(
    'enable_tiktoken_counter',
    flat.known.enable_tiktoken_counter,
    (v) => asTriBool(v) !== null,
  );
  preserve('tokenizer_offline', flat.known.tokenizer_offline, (v) => asTriBool(v) !== null);
  preserve(
    'context_window_tokens',
    flat.known.context_window_tokens,
    (v) => asNumberOrNull(v) !== null,
  );
  preserve('tokenizer_cache_dir', flat.known.tokenizer_cache_dir, (v) => typeof v === 'string');
  for (const key of CONTEXT_ENGINE_GROUP_KEYS) {
    preserve(key, groups.known[key], isPlainRecord);
  }
  return {
    enabled: asTriBool(flat.known.enabled),
    contextWindowTokens: asNumberOrNull(flat.known.context_window_tokens),
    modelContextWindowTokensJson: isPlainRecord(flat.known.model_context_window_tokens)
      ? stringifyOrEmpty(flat.known.model_context_window_tokens)
      : '',
    enableReload: asTriBool(flat.known.enable_reload),
    enableTiktokenCounter: asTriBool(flat.known.enable_tiktoken_counter),
    tokenizerRegistryJson: isPlainRecord(flat.known.tokenizer_registry)
      ? stringifyOrEmpty(flat.known.tokenizer_registry)
      : '',
    tokenizerSpecJson: isPlainRecord(flat.known.tokenizer_spec)
      ? stringifyOrEmpty(flat.known.tokenizer_spec)
      : '',
    tokenizerCacheDir: asString(flat.known.tokenizer_cache_dir, ''),
    tokenizerOffline: asTriBool(flat.known.tokenizer_offline),
    compressionRecall: compressionRecallBodyToForm(groups.known.compression_recall_config),
    messageSummaryOffloader: messageSummaryOffloaderBodyToForm(groups.known.message_summary_offloader_config),
    sessionMemory: sessionMemoryBodyToForm(groups.known.session_memory_compressor_config),
    dialogueCompressor: dialogueCompressorBodyToForm(groups.known.dialogue_compressor_config),
    currentRoundCompressor: roundCompressorBodyToForm(groups.known.current_round_compressor_config),
    roundLevelCompressor: roundCompressorBodyToForm(groups.known.round_level_compressor_config),
    extraJson: stringifyOrEmpty(restAll),
  };
}

function contextEngineFormToBody(form: ContextEngineFormState): Record<string, unknown> {
  const out: Record<string, unknown> = { ...parseJsonObject(form.extraJson) };
  // 布尔键三态：null=未配置（省略），true/false 显式写入，避免未配置的 context_engine_config 被强行注入。
  if (form.enabled !== null) out.enabled = form.enabled;
  if (form.enableReload !== null) out.enable_reload = form.enableReload;
  if (form.enableTiktokenCounter !== null) out.enable_tiktoken_counter = form.enableTiktokenCounter;
  if (form.tokenizerCacheDir.trim()) out.tokenizer_cache_dir = form.tokenizerCacheDir.trim();
  if (form.tokenizerOffline !== null) out.tokenizer_offline = form.tokenizerOffline;
  if (form.contextWindowTokens !== null) out.context_window_tokens = form.contextWindowTokens;
  const modelWindow = parseJsonObject(form.modelContextWindowTokensJson);
  if (Object.keys(modelWindow).length) out.model_context_window_tokens = modelWindow;
  const registry = parseJsonObject(form.tokenizerRegistryJson);
  if (Object.keys(registry).length) out.tokenizer_registry = registry;
  const spec = parseJsonObject(form.tokenizerSpecJson);
  if (Object.keys(spec).length) out.tokenizer_spec = spec;
  // 压缩器子组：所有字段留空时整组省略（运行时用自身默认值兜底）
  const recall = compressionRecallFormToBody(form.compressionRecall);
  if (recall) out.compression_recall_config = recall;
  const offloader = messageSummaryOffloaderFormToBody(form.messageSummaryOffloader);
  if (offloader) out.message_summary_offloader_config = offloader;
  const sessionMemory = sessionMemoryFormToBody(form.sessionMemory);
  if (sessionMemory) out.session_memory_compressor_config = sessionMemory;
  const dialogue = dialogueCompressorFormToBody(form.dialogueCompressor);
  if (dialogue) out.dialogue_compressor_config = dialogue;
  const currentRound = roundCompressorFormToBody(form.currentRoundCompressor);
  if (currentRound) out.current_round_compressor_config = currentRound;
  const roundLevel = roundCompressorFormToBody(form.roundLevelCompressor);
  if (roundLevel) out.round_level_compressor_config = roundLevel;
  return out;
}

// ---------------------------------------------------------------------------
// 子Agent 转换
// ---------------------------------------------------------------------------

function subagentEntryFromBody(
  def: Omit<ReactSubagentEntry, 'key'>,
  value: unknown,
): ReactSubagentEntry {
  const v = asRecord(value);
  const rest: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(v)) {
    if (key === 'enabled' || key === 'max_iterations') continue;
    rest[key] = val;
  }
  return {
    key: nextKey('subagent'),
    name: def.name,
    enabled: asTriBool(v.enabled),
    maxIterations: asNumberOrNull(v.max_iterations),
    extraJson: stringifyOrEmpty(rest),
  };
}

// ---------------------------------------------------------------------------
// 顶层 body ↔ form 转换
// ---------------------------------------------------------------------------

export function reactBodyToFormState(body: Record<string, unknown>): ReactFormState {
  const b = asRecord(body);
  const rest: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(b)) {
    // 模型三键等历史遗留键一并落入 rest（自定义 JSON）原样透传，
    // 编辑已有模板时不再静默丢弃；新建模板由模型槽位机制天然不会产生这些键。
    if (RESERVED_EXTRA_KEYS.has(key)) {
      // 分组键（subagents/subagent_runtime/context_engine_config）非 object 形态时保留到
      // rest 原样透传，避免保存时静默丢弃；标量结构化键仍由表单字段接管。
      if ((SUBAGENT_KEYS.has(key) || key === CONTEXT_ENGINE_KEY) && !isPlainRecord(value)) {
        rest[key] = value;
      }
      // 已知数值/布尔标量键的非数字/非布尔形态同样保留到 rest，避免保存时静默丢弃；
      // 用户在表单显式设置该字段时，结构化值覆盖原值。
      if (
        value !== undefined &&
        value !== null &&
        ((NUMBER_STRUCTURED_KEYS.has(key) && asNumberOrNull(value) === null) ||
          (BOOL_STRUCTURED_KEYS.has(key) && asTriBool(value) === null))
      ) {
        rest[key] = value;
      }
      continue;
    }
    rest[key] = value;
  }

  const subagentRuntime = asRecord(b.subagent_runtime);
  const subagentRuntimeRest: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(subagentRuntime)) {
    if (key === 'enabled') continue;
    subagentRuntimeRest[key] = val;
  }

  const subagentsRaw = asRecord(b.subagents);
  const knownNames = new Set(DEFAULT_SUBAGENTS.map((s) => s.name));
  const subagents = DEFAULT_SUBAGENTS.map((def) => subagentEntryFromBody(def, subagentsRaw[def.name]));
  // 保留 body 里非默认的子 Agent 名称，避免编辑保存后丢失。
  for (const [name, val] of Object.entries(subagentsRaw)) {
    if (knownNames.has(name)) continue;
    subagents.push(
      subagentEntryFromBody({ name, enabled: null, maxIterations: null, extraJson: '' }, val),
    );
  }

  return {
    agentName: asString(b.agent_name, ''),
    skillMode: asString(b.skill_mode, ''),
    enableTaskLoop: asTriBool(b.enable_task_loop),
    enableReadImageMultimodal: asTriBool(b.enable_read_image_multimodal),
    completionTimeout: asNumberOrNull(b.completion_timeout),
    maxIterations: asNumberOrNull(b.max_iterations),
    streamChunkThreshold: asNumberOrNull(b.stream_chunk_threshold),
    streamCharacterThreshold: asNumberOrNull(b.stream_character_threshold),
    subagentRuntimeEnabled: asTriBool(subagentRuntime.enabled),
    subagentRuntimeExtraJson: stringifyOrEmpty(subagentRuntimeRest),
    subagents,
    contextEngine: contextEngineBodyToForm(b.context_engine_config),
    extraJson: stringifyOrEmpty(rest),
  };
}

export function reactFormStateToBody(form: ReactFormState): Record<string, unknown> {
  const rest = parseReactExtraJson(form.extraJson) ?? {};

  const body: Record<string, unknown> = { ...rest };

  if (form.agentName.trim()) body.agent_name = form.agentName.trim();
  if (form.skillMode.trim()) body.skill_mode = form.skillMode.trim();
  if (form.enableTaskLoop !== null) body.enable_task_loop = form.enableTaskLoop;
  if (form.enableReadImageMultimodal !== null) {
    body.enable_read_image_multimodal = form.enableReadImageMultimodal;
  }
  if (form.completionTimeout !== null) body.completion_timeout = form.completionTimeout;
  if (form.maxIterations !== null) body.max_iterations = form.maxIterations;
  if (form.streamChunkThreshold !== null) body.stream_chunk_threshold = form.streamChunkThreshold;
  if (form.streamCharacterThreshold !== null) body.stream_character_threshold = form.streamCharacterThreshold;

  const subagentRuntimeBody: Record<string, unknown> = {
    ...parseJsonObject(form.subagentRuntimeExtraJson),
  };
  if (form.subagentRuntimeEnabled !== null) {
    subagentRuntimeBody.enabled = form.subagentRuntimeEnabled;
  }
  if (Object.keys(subagentRuntimeBody).length) body.subagent_runtime = subagentRuntimeBody;

  const subagents: Record<string, unknown> = {};
  for (const entry of form.subagents) {
    const name = entry.name.trim();
    if (!name) continue;
    const sub: Record<string, unknown> = { ...parseJsonObject(entry.extraJson) };
    if (entry.enabled !== null) sub.enabled = entry.enabled;
    if (entry.maxIterations !== null) sub.max_iterations = entry.maxIterations;
    // 未配置任何键的空子Agent整组省略，避免把默认空对象注入 body。
    if (Object.keys(sub).length) subagents[name] = sub;
  }
  if (Object.keys(subagents).length) body.subagents = subagents;

  const contextEngineBody = contextEngineFormToBody(form.contextEngine);
  if (Object.keys(contextEngineBody).length) {
    body.context_engine_config = contextEngineBody;
  }

  return body;
}
