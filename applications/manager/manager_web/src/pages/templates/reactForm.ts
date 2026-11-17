import { safeStringify } from '../../utils/format';
import { jsonContentForValidation } from '../../utils/jsonExample';

/**
 * React 模板 body 的结构化表单状态（前端维护的 schema）。
 *
 * 分组：基础 / 子Agent / 上下文引擎 / 自定义 JSON。
 * 每组结构化其已知的键，其余键落入该组的 extraJson（或顶层 extraJson），
 * 保底不丢数据。模型三键（model_name / model_client_config / model_config_obj）
 * 由模型槽位（default_model）统一掌管，绝不出现在 react body 中。
 *
 * 参数集按企业版整理裁剪：仅保留《react配置参数总结》E 列为空的字段；
 * evolution / ttse / a2x_registry 整组不结构化，已存模板中的旧键经顶层
 * extraJson 透传。新增字段默认留空 = 省略键，由运行时自身默认值兜底。
 */

export interface ReactSubagentEntry {
  key: string;
  name: string;
  /** 开关：true = 开启，false = 关闭 */
  enabled: boolean;
  maxIterations: number | null;
  /** 该 subagent 的其余键 */
  extraJson: string;
}

export interface CompressionRecallFormState {
  /** 开关：true = 开启，false = 关闭 */
  enabled: boolean;
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
  enabled: boolean;
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
  /** 开关：true = 开启，false = 关闭 */
  enabled: boolean;
  /** 上下文窗口上限；null = 省略 */
  contextWindowTokens: number | null;
  /** 模型级上下文窗口映射（kv JSON 文本），空 = 省略 */
  modelContextWindowTokensJson: string;
  enableReload: boolean;
  enableTiktokenCounter: boolean;
  /** 模型词表映射（kv JSON 文本），空 = 省略 */
  tokenizerRegistryJson: string;
  /** 上下文词表映射（kv JSON 文本），空 = 省略 */
  tokenizerSpecJson: string;
  tokenizerCacheDir: string;
  tokenizerOffline: boolean;
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
  /** 开关：true = 开启，false = 关闭 */
  enableTaskLoop: boolean;
  /** read_file 图片输入策略：null=未配置/自动探测，false=纯文本，true=多模态 */
  enableReadImageMultimodal: boolean | null;
  /** 单轮任务循环等待上限（秒）；null 表示未配置 */
  completionTimeout: number | null;
  /** 主 Agent 内层 ReAct 轮次上限；null 表示未配置 */
  maxIterations: number | null;
  streamChunkThreshold: number | null;
  streamCharacterThreshold: number | null;
  // 子Agent
  /** 常驻子Agent运行时开关 */
  subagentRuntimeEnabled: boolean;
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

const SUBAGENT_KEYS = new Set(['subagents', 'subagent_runtime']);
const CONTEXT_ENGINE_KEY = 'context_engine_config';

/** 模型槽位专属键，绝不允许出现在 react body 中。 */
const MODEL_SLOT_KEYS = new Set(['model_name', 'model_client_config', 'model_config_obj']);

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
  if (value === null || value === undefined) return null;
  return Boolean(value);
}

function asBool(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
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
  return Object.keys(custom).filter((key) => RESERVED_EXTRA_KEYS.has(key));
}

// ---------------------------------------------------------------------------
// 默认值
// ---------------------------------------------------------------------------

const DEFAULT_SUBAGENTS: Array<Omit<ReactSubagentEntry, 'key'>> = [
  { name: 'general_agent', enabled: true, maxIterations: null, extraJson: '' },
  { name: 'code_agent', enabled: false, maxIterations: null, extraJson: '' },
  { name: 'research_agent', enabled: false, maxIterations: null, extraJson: '' },
];

function defaultContextEngine(): ContextEngineFormState {
  return {
    enabled: true,
    contextWindowTokens: null,
    modelContextWindowTokensJson: '',
    enableReload: true,
    enableTiktokenCounter: false,
    tokenizerRegistryJson: '',
    tokenizerSpecJson: '',
    tokenizerCacheDir: 'tokenizers',
    tokenizerOffline: false,
    compressionRecall: {
      enabled: false,
      chunkSizeTokens: 3000,
      chunkOverlapTokens: 500,
      extraJson: '',
    },
    messageSummaryOffloader: {
      protectedToolNames: 'read_file, read_pdf',
      addMessageThresholdRatio: 0.1,
      ttlSeconds: 300,
      ttlContextOccupancyRatio: 0.5,
      ttlMessageThresholdRatio: 0.05,
      extraJson: '',
    },
    sessionMemory: {
      enabled: false,
      triggerContextRatio: 0.8,
      sessionMemoryPath: '',
      maxNotesChars: null,
      memoryUpdateTriggerContextRatio: 0.1,
      memoryUpdateMode: '',
      memoryDirectReplaceMaxRetries: null,
      memoryExtraJson: '',
      extraJson: '',
    },
    dialogueCompressor: {
      triggerContextRatio: 0.8,
      minTargetContextRatio: 0.1,
      modelTemplateId: '',
      extraJson: '',
    },
    currentRoundCompressor: {
      triggerContextRatio: 0.8,
      minTargetContextRatio: 0.1,
      keepRecentMessages: 4,
      modelTemplateId: '',
      extraJson: '',
    },
    roundLevelCompressor: {
      triggerContextRatio: 0.8,
      minTargetContextRatio: 0.1,
      keepRecentMessages: 0,
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
    enableTaskLoop: true,
    enableReadImageMultimodal: null,
    completionTimeout: null,
    maxIterations: null,
    streamChunkThreshold: 50,
    streamCharacterThreshold: 2000,
    subagentRuntimeEnabled: true,
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

const COMPRESSION_RECALL_FLAT_KEYS = new Set(['enabled', 'chunk_size_tokens', 'chunk_overlap_tokens']);

function compressionRecallBodyToForm(value: unknown): CompressionRecallFormState {
  const v = asRecord(value);
  const { known, rest } = splitKnown(v, COMPRESSION_RECALL_FLAT_KEYS);
  return {
    enabled: asBool(known.enabled, false),
    chunkSizeTokens: asNumberOrNull(known.chunk_size_tokens),
    chunkOverlapTokens: asNumberOrNull(known.chunk_overlap_tokens),
    extraJson: stringifyOrEmpty(rest),
  };
}

function compressionRecallFormToBody(form: CompressionRecallFormState): Record<string, unknown> | null {
  const out: Record<string, unknown> = { ...parseJsonObject(form.extraJson) };
  out.enabled = form.enabled;
  if (form.chunkSizeTokens !== null) out.chunk_size_tokens = form.chunkSizeTokens;
  if (form.chunkOverlapTokens !== null) out.chunk_overlap_tokens = form.chunkOverlapTokens;
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
  if (isPlainRecord(known.memory)) {
    const memory = splitKnown(known.memory, MEMORY_FLAT_KEYS);
    memoryUpdateTriggerContextRatio = asNumberOrNull(memory.known.update_trigger_context_ratio);
    memoryUpdateMode = typeof memory.known.update_mode === 'string' ? memory.known.update_mode : '';
    memoryDirectReplaceMaxRetries = asNumberOrNull(memory.known.direct_replace_max_retries);
    memoryExtraJson = stringifyOrEmpty(memory.rest);
  } else if ('memory' in known) {
    // 非 record 的 memory 值无法结构化编辑，原样透传
    restAll.memory = known.memory;
  }
  return {
    enabled: asBool(known.enabled, false),
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
  out.enabled = form.enabled;
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
  return {
    enabled: asBool(flat.known.enabled, true),
    contextWindowTokens: asNumberOrNull(flat.known.context_window_tokens),
    modelContextWindowTokensJson: isPlainRecord(flat.known.model_context_window_tokens)
      ? stringifyOrEmpty(flat.known.model_context_window_tokens)
      : '',
    enableReload: asBool(flat.known.enable_reload, true),
    enableTiktokenCounter: asBool(flat.known.enable_tiktoken_counter, false),
    tokenizerRegistryJson: isPlainRecord(flat.known.tokenizer_registry)
      ? stringifyOrEmpty(flat.known.tokenizer_registry)
      : '',
    tokenizerSpecJson: isPlainRecord(flat.known.tokenizer_spec)
      ? stringifyOrEmpty(flat.known.tokenizer_spec)
      : '',
    tokenizerCacheDir: asString(flat.known.tokenizer_cache_dir, ''),
    tokenizerOffline: asBool(flat.known.tokenizer_offline, false),
    compressionRecall: compressionRecallBodyToForm(groups.known.compression_recall_config),
    messageSummaryOffloader: messageSummaryOffloaderBodyToForm(groups.known.message_summary_offloader_config),
    sessionMemory: sessionMemoryBodyToForm(groups.known.session_memory_compressor_config),
    dialogueCompressor: dialogueCompressorBodyToForm(groups.known.dialogue_compressor_config),
    currentRoundCompressor: roundCompressorBodyToForm(groups.known.current_round_compressor_config),
    roundLevelCompressor: roundCompressorBodyToForm(groups.known.round_level_compressor_config),
    extraJson: stringifyOrEmpty(groups.rest),
  };
}

function contextEngineFormToBody(form: ContextEngineFormState): Record<string, unknown> {
  const out: Record<string, unknown> = { ...parseJsonObject(form.extraJson) };
  out.enabled = form.enabled;
  out.enable_reload = form.enableReload;
  out.enable_tiktoken_counter = form.enableTiktokenCounter;
  if (form.tokenizerCacheDir.trim()) out.tokenizer_cache_dir = form.tokenizerCacheDir.trim();
  out.tokenizer_offline = form.tokenizerOffline;
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
    enabled: typeof v.enabled === 'boolean' ? v.enabled : def.enabled,
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
    if (RESERVED_EXTRA_KEYS.has(key) || MODEL_SLOT_KEYS.has(key)) continue;
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
      subagentEntryFromBody({ name, enabled: false, maxIterations: null, extraJson: '' }, val),
    );
  }

  return {
    agentName: asString(b.agent_name, ''),
    skillMode: asString(b.skill_mode, ''),
    enableTaskLoop: b.enable_task_loop !== false,
    enableReadImageMultimodal: asTriBool(b.enable_read_image_multimodal),
    completionTimeout: asNumberOrNull(b.completion_timeout),
    maxIterations: asNumberOrNull(b.max_iterations),
    streamChunkThreshold: asNumberOrNull(b.stream_chunk_threshold),
    streamCharacterThreshold: asNumberOrNull(b.stream_character_threshold),
    subagentRuntimeEnabled: subagentRuntime.enabled !== false,
    subagentRuntimeExtraJson: stringifyOrEmpty(subagentRuntimeRest),
    subagents,
    contextEngine: contextEngineBodyToForm(b.context_engine_config),
    extraJson: stringifyOrEmpty(rest),
  };
}

export function reactFormStateToBody(form: ReactFormState): Record<string, unknown> {
  const rest = parseReactExtraJson(form.extraJson) ?? {};
  for (const key of MODEL_SLOT_KEYS) {
    delete rest[key];
  }

  const body: Record<string, unknown> = { ...rest };

  if (form.agentName.trim()) body.agent_name = form.agentName.trim();
  if (form.skillMode.trim()) body.skill_mode = form.skillMode.trim();
  body.enable_task_loop = form.enableTaskLoop;
  if (form.enableReadImageMultimodal !== null) {
    body.enable_read_image_multimodal = form.enableReadImageMultimodal;
  }
  if (form.completionTimeout !== null) body.completion_timeout = form.completionTimeout;
  if (form.maxIterations !== null) body.max_iterations = form.maxIterations;
  if (form.streamChunkThreshold !== null) body.stream_chunk_threshold = form.streamChunkThreshold;
  if (form.streamCharacterThreshold !== null) body.stream_character_threshold = form.streamCharacterThreshold;

  body.subagent_runtime = {
    ...parseJsonObject(form.subagentRuntimeExtraJson),
    enabled: form.subagentRuntimeEnabled,
  };

  const subagents: Record<string, unknown> = {};
  for (const entry of form.subagents) {
    const name = entry.name.trim();
    if (!name) continue;
    const sub: Record<string, unknown> = { ...parseJsonObject(entry.extraJson) };
    sub.enabled = entry.enabled;
    if (entry.maxIterations !== null) sub.max_iterations = entry.maxIterations;
    subagents[name] = sub;
  }
  body.subagents = subagents;

  const contextEngineBody = contextEngineFormToBody(form.contextEngine);
  if (Object.keys(contextEngineBody).length) {
    body.context_engine_config = contextEngineBody;
  }

  return body;
}
