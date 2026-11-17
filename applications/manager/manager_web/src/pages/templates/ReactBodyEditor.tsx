import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { JsonField } from '../../components/JsonField';
import { ModelTemplateSelect } from '../../components/ModelTemplateSelect';
import {
  numberOrEmpty,
  parseNumberOrNull,
  parseReactExtraJson,
  reservedReactExtraFields,
  type CompressionRecallFormState,
  type ContextEngineFormState,
  type DialogueCompressorFormState,
  type MessageSummaryOffloaderFormState,
  type ReactFormState,
  type ReactSubagentEntry,
  type RoundCompressorFormState,
  type SessionMemoryCompressorFormState,
} from './reactForm';

type SectionKey = 'basic' | 'subagents' | 'contextEngine' | 'advancedJson';

/** 自定义 JSON 空间校验：非法 JSON 或含结构化保留字段时返回错误文案（对齐 MCP customConfigError）。 */
export function reactExtraJsonError(
  form: ReactFormState,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  const custom = parseReactExtraJson(form.extraJson);
  if (custom === undefined) return t('reactTemplates.advancedJsonInvalid');
  const reserved = reservedReactExtraFields(custom);
  if (reserved.length) {
    return t('reactTemplates.advancedJsonReserved', { fields: reserved.join(', ') });
  }
  // 分组级「其余键」JSON 非法时 parseJsonObject 会静默丢弃，需在保存前拦截。
  const ce = form.contextEngine;
  const groupExtraJson = [
    form.subagentRuntimeExtraJson,
    ce.extraJson,
    ce.compressionRecall.extraJson,
    ce.messageSummaryOffloader.extraJson,
    ce.sessionMemory.memoryExtraJson,
    ce.sessionMemory.extraJson,
    ce.dialogueCompressor.extraJson,
    ce.currentRoundCompressor.extraJson,
    ce.roundLevelCompressor.extraJson,
    ce.modelContextWindowTokensJson,
    ce.tokenizerRegistryJson,
    ce.tokenizerSpecJson,
    ...form.subagents.map((s) => s.extraJson),
  ];
  if (groupExtraJson.some((raw) => parseReactExtraJson(raw) === undefined)) {
    return t('reactTemplates.advancedJsonInvalid');
  }
  return '';
}

interface Props {
  form: ReactFormState;
  onChange: (next: ReactFormState) => void;
}

/** 三态字段：未配置（null，省略键）/ 开启（true）/ 关闭（false）。 */
function TriStateField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean | null;
  onChange: (v: boolean | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <div>
      <label className="label">{label}</label>
      <select
        className="select"
        value={value === null ? '' : String(value)}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === '' ? null : v === 'true');
        }}
      >
        <option value="">{t('reactTemplates.triStateUnset')}</option>
        <option value="true">{t('reactTemplates.triStateOn')}</option>
        <option value="false">{t('reactTemplates.triStateOff')}</option>
      </select>
    </div>
  );
}

/** 数值字段：空 = 省略键。 */
function NumberOrNullField({
  label,
  value,
  onChange,
  placeholder,
  min = 0,
  integer = false,
}: {
  label: string;
  value: number | null;
  onChange: (v: number | null) => void;
  placeholder?: string;
  /** 数值下界（默认 0）：低于下界的输入视为未配置（省略键），不写入 body。 */
  min?: number;
  /** 仅接受整数（计数/大小类字段）；非整数输入视为未配置。 */
  integer?: boolean;
}) {
  // 文本态受控：type="number" 会按数值回写输入框，把 "." / "-" 等中间态吞掉（0.8 会被
  // 输成 8），故用 type="text" + inputMode="decimal" 让键盘仍显示数字，输入文本自行解析。
  const [text, setText] = useState(() => numberOrEmpty(value));
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!editing) setText(numberOrEmpty(value));
  }, [value, editing]);

  const parseValid = (raw: string): number | null => {
    const parsed = parseNumberOrNull(raw);
    if (parsed === null) return null;
    if (parsed < min) return null;
    if (integer && !Number.isInteger(parsed)) return null;
    return parsed;
  };

  return (
    <div>
      <label className="label">{label}</label>
      <input
        className="input mono"
        type="text"
        inputMode="decimal"
        placeholder={placeholder}
        value={text}
        onFocus={() => setEditing(true)}
        onBlur={() => {
          setEditing(false);
          setText(numberOrEmpty(value));
        }}
        onChange={(e) => {
          setText(e.target.value);
          onChange(parseValid(e.target.value));
        }}
      />
    </div>
  );
}

function SectionExtraJson({
  label,
  hint,
  value,
  onChange,
  rows = 6,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
}) {
  return (
    <details className="md:col-span-2">
      <summary className="text-[11px] text-muted cursor-pointer">{label}</summary>
      <div className="mt-2">
        <JsonField label={label} hint={hint} value={value} onChange={onChange} placeholder="{}" rows={rows} />
      </div>
    </details>
  );
}

/** 上下文引擎的压缩器子配置折叠卡。 */
function SubConfigCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="card">
      <summary className="cursor-pointer text-sm font-medium">{title}</summary>
      <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-4">{children}</div>
    </details>
  );
}

function SubagentCard({
  entry,
  onChange,
}: {
  entry: ReactSubagentEntry;
  onChange: (patch: Partial<ReactSubagentEntry>) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium mono">{entry.name}</span>
        <TriStateField
          label={t('reactTemplates.subagentEnabled')}
          value={entry.enabled}
          onChange={(v) => onChange({ enabled: v })}
        />
      </div>
      <NumberOrNullField
        label={t('reactTemplates.subagentMaxIterations')}
        value={entry.maxIterations}
        onChange={(v) => onChange({ maxIterations: v })}
        placeholder={t('reactTemplates.unconfigured')}
        integer
      />
      <details>
        <summary className="text-[11px] text-muted cursor-pointer">
          {t('reactTemplates.extraKeys')}
        </summary>
        <div className="mt-2">
          <JsonField
            label={t('reactTemplates.extraKeys')}
            hint={t('reactTemplates.subagentExtraHint')}
            value={entry.extraJson}
            onChange={(v) => onChange({ extraJson: v })}
            placeholder="{}"
            rows={3}
          />
        </div>
      </details>
    </div>
  );
}

/** React 模板 body 编辑器：基础 / 子Agent / 上下文引擎 / 自定义 JSON 并列 tab。 */
export function ReactBodyEditor({ form, onChange }: Props) {
  const { t } = useTranslation();
  const [section, setSection] = useState<SectionKey>('basic');

  const updateForm = <K extends keyof ReactFormState>(key: K, value: ReactFormState[K]) => {
    onChange({ ...form, [key]: value });
  };
  const updateSubagent = (key: string, patch: Partial<ReactSubagentEntry>) => {
    onChange({
      ...form,
      subagents: form.subagents.map((s) => (s.key === key ? { ...s, ...patch } : s)),
    });
  };
  const updateContextEngine = (patch: Partial<ContextEngineFormState>) => {
    onChange({ ...form, contextEngine: { ...form.contextEngine, ...patch } });
  };
  const updateCompressionRecall = (patch: Partial<CompressionRecallFormState>) => {
    updateContextEngine({
      compressionRecall: { ...form.contextEngine.compressionRecall, ...patch },
    });
  };
  const updateMessageSummaryOffloader = (patch: Partial<MessageSummaryOffloaderFormState>) => {
    updateContextEngine({
      messageSummaryOffloader: { ...form.contextEngine.messageSummaryOffloader, ...patch },
    });
  };
  const updateSessionMemory = (patch: Partial<SessionMemoryCompressorFormState>) => {
    updateContextEngine({
      sessionMemory: { ...form.contextEngine.sessionMemory, ...patch },
    });
  };
  const updateDialogueCompressor = (patch: Partial<DialogueCompressorFormState>) => {
    updateContextEngine({
      dialogueCompressor: { ...form.contextEngine.dialogueCompressor, ...patch },
    });
  };
  const updateCurrentRoundCompressor = (patch: Partial<RoundCompressorFormState>) => {
    updateContextEngine({
      currentRoundCompressor: { ...form.contextEngine.currentRoundCompressor, ...patch },
    });
  };
  const updateRoundLevelCompressor = (patch: Partial<RoundCompressorFormState>) => {
    updateContextEngine({
      roundLevelCompressor: { ...form.contextEngine.roundLevelCompressor, ...patch },
    });
  };

  const multimodalValue = form.enableReadImageMultimodal === null ? '' : String(form.enableReadImageMultimodal);
  const extraJsonError = reactExtraJsonError(form, t);
  const ce = form.contextEngine;

  const sections: { key: SectionKey; label: string }[] = [
    { key: 'basic', label: t('reactTemplates.sections.basic') },
    { key: 'subagents', label: t('reactTemplates.sections.subagents') },
    { key: 'contextEngine', label: t('reactTemplates.sections.contextEngine') },
    { key: 'advancedJson', label: t('reactTemplates.sections.advancedJson') },
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="tabs-bar overflow-x-auto">
        {sections.map((it) => (
          <button
            key={it.key}
            type="button"
            onClick={() => setSection(it.key)}
            className={`tab ${section === it.key ? 'active' : ''}`}
          >
            {it.label}
          </button>
        ))}
      </div>

      {section === 'basic' && (
        <div className="card grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="label">{t('reactTemplates.agentName')}</label>
            <input
              className="input mono"
              value={form.agentName}
              onChange={(e) => updateForm('agentName', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('reactTemplates.skillMode')}</label>
            <input
              className="input mono"
              value={form.skillMode}
              onChange={(e) => updateForm('skillMode', e.target.value)}
            />
          </div>
          <TriStateField
            label={t('reactTemplates.enableTaskLoop')}
            value={form.enableTaskLoop}
            onChange={(v) => updateForm('enableTaskLoop', v)}
          />
          <div>
            <label className="label">{t('reactTemplates.enableReadImageMultimodal')}</label>
            <select
              className="select"
              value={multimodalValue}
              onChange={(e) => {
                const v = e.target.value;
                updateForm('enableReadImageMultimodal', v === '' ? null : v === 'true');
              }}
            >
              <option value="">{t('reactTemplates.multimodalAuto')}</option>
              <option value="false">{t('reactTemplates.multimodalText')}</option>
              <option value="true">{t('reactTemplates.multimodalOn')}</option>
            </select>
            <p className="text-[11px] text-muted mt-1">{t('reactTemplates.multimodalHint')}</p>
          </div>
          <NumberOrNullField
            label={t('reactTemplates.completionTimeout')}
            value={form.completionTimeout}
            onChange={(v) => updateForm('completionTimeout', v)}
            placeholder={t('reactTemplates.unlimited')}
            integer
          />
          <NumberOrNullField
            label={t('reactTemplates.maxIterations')}
            value={form.maxIterations}
            onChange={(v) => updateForm('maxIterations', v)}
            placeholder={t('reactTemplates.unlimited')}
            integer
          />
          <NumberOrNullField
            label={t('reactTemplates.streamChunkThreshold')}
            value={form.streamChunkThreshold}
            integer
            onChange={(v) => updateForm('streamChunkThreshold', v)}
            placeholder={t('reactTemplates.unconfigured')}
          />
          <NumberOrNullField
            label={t('reactTemplates.streamCharacterThreshold')}
            value={form.streamCharacterThreshold}
            integer
            onChange={(v) => updateForm('streamCharacterThreshold', v)}
            placeholder={t('reactTemplates.unconfigured')}
          />
        </div>
      )}

      {section === 'subagents' && (
        <div className="flex flex-col gap-3">
          <div className="card flex flex-col gap-2">
            <TriStateField
              label={t('reactTemplates.subagentRuntimeEnabled')}
              value={form.subagentRuntimeEnabled}
              onChange={(v) => updateForm('subagentRuntimeEnabled', v)}
            />
            <p className="text-[11px] text-muted">{t('reactTemplates.subagentRuntimeHint')}</p>
          </div>

          {form.subagents.map((entry) => (
            <SubagentCard
              key={entry.key}
              entry={entry}
              onChange={(patch) => updateSubagent(entry.key, patch)}
            />
          ))}
        </div>
      )}

      {section === 'contextEngine' && (
        <div className="flex flex-col gap-3">
          <div className="card grid grid-cols-1 md:grid-cols-2 gap-4">
            <TriStateField
              label={t('reactTemplates.contextEngineEnabled')}
              value={ce.enabled}
              onChange={(v) => updateContextEngine({ enabled: v })}
            />
            <TriStateField
              label={t('reactTemplates.contextEngineEnableReload')}
              value={ce.enableReload}
              onChange={(v) => updateContextEngine({ enableReload: v })}
            />
            <TriStateField
              label={t('reactTemplates.contextEngineTiktokenCounter')}
              value={ce.enableTiktokenCounter}
              onChange={(v) => updateContextEngine({ enableTiktokenCounter: v })}
            />
            <TriStateField
              label={t('reactTemplates.contextEngineTokenizerOffline')}
              value={ce.tokenizerOffline}
              onChange={(v) => updateContextEngine({ tokenizerOffline: v })}
            />
            <NumberOrNullField
              label={t('reactTemplates.contextEngineContextWindowTokens')}
              value={ce.contextWindowTokens}
              integer
              onChange={(v) => updateContextEngine({ contextWindowTokens: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <div>
              <label className="label">{t('reactTemplates.contextEngineTokenizerCacheDir')}</label>
              <input
                className="input mono"
                value={ce.tokenizerCacheDir}
                onChange={(e) => updateContextEngine({ tokenizerCacheDir: e.target.value })}
              />
            </div>
            <div className="md:col-span-2">
              <JsonField
                label={t('reactTemplates.contextEngineModelContextWindowTokens')}
                value={ce.modelContextWindowTokensJson}
                onChange={(v) => updateContextEngine({ modelContextWindowTokensJson: v })}
                placeholder="{}"
                rows={3}
              />
            </div>
            <JsonField
              label={t('reactTemplates.contextEngineTokenizerRegistry')}
              value={ce.tokenizerRegistryJson}
              onChange={(v) => updateContextEngine({ tokenizerRegistryJson: v })}
              placeholder="{}"
              rows={3}
            />
            <JsonField
              label={t('reactTemplates.contextEngineTokenizerSpec')}
              value={ce.tokenizerSpecJson}
              onChange={(v) => updateContextEngine({ tokenizerSpecJson: v })}
              placeholder="{}"
              rows={3}
            />
            <SectionExtraJson
              label={t('reactTemplates.extraKeys')}
              hint={t('reactTemplates.contextEngineExtraHint')}
              value={ce.extraJson}
              onChange={(v) => updateContextEngine({ extraJson: v })}
              rows={10}
            />
          </div>

          <SubConfigCard title={t('reactTemplates.compressorCompressionRecall')}>
            <TriStateField
              label={t('reactTemplates.compressorEnabled')}
              value={ce.compressionRecall.enabled}
              onChange={(v) => updateCompressionRecall({ enabled: v })}
            />
            <NumberOrNullField
              label={t('reactTemplates.compressionRecallChunkSize')}
              value={ce.compressionRecall.chunkSizeTokens}
              integer
              onChange={(v) => updateCompressionRecall({ chunkSizeTokens: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <NumberOrNullField
              label={t('reactTemplates.compressionRecallChunkOverlap')}
              value={ce.compressionRecall.chunkOverlapTokens}
              integer
              onChange={(v) => updateCompressionRecall({ chunkOverlapTokens: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <SectionExtraJson
              label={t('reactTemplates.extraKeys')}
              hint={t('reactTemplates.compressionRecallExtraHint')}
              value={ce.compressionRecall.extraJson}
              onChange={(v) => updateCompressionRecall({ extraJson: v })}
              rows={4}
            />
          </SubConfigCard>

          <SubConfigCard title={t('reactTemplates.compressorMessageSummaryOffloader')}>
            <div className="md:col-span-2">
              <label className="label">{t('reactTemplates.msgSummaryProtectedTools')}</label>
              <input
                className="input mono"
                value={ce.messageSummaryOffloader.protectedToolNames}
                onChange={(e) => updateMessageSummaryOffloader({ protectedToolNames: e.target.value })}
              />
            </div>
            <NumberOrNullField
              label={t('reactTemplates.msgSummaryAddThresholdRatio')}
              value={ce.messageSummaryOffloader.addMessageThresholdRatio}
              onChange={(v) => updateMessageSummaryOffloader({ addMessageThresholdRatio: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <NumberOrNullField
              label={t('reactTemplates.msgSummaryTtlSeconds')}
              value={ce.messageSummaryOffloader.ttlSeconds}
              integer
              onChange={(v) => updateMessageSummaryOffloader({ ttlSeconds: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <NumberOrNullField
              label={t('reactTemplates.msgSummaryTtlContextRatio')}
              value={ce.messageSummaryOffloader.ttlContextOccupancyRatio}
              onChange={(v) => updateMessageSummaryOffloader({ ttlContextOccupancyRatio: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <NumberOrNullField
              label={t('reactTemplates.msgSummaryTtlMessageRatio')}
              value={ce.messageSummaryOffloader.ttlMessageThresholdRatio}
              onChange={(v) => updateMessageSummaryOffloader({ ttlMessageThresholdRatio: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <SectionExtraJson
              label={t('reactTemplates.extraKeys')}
              hint={t('reactTemplates.msgSummaryExtraHint')}
              value={ce.messageSummaryOffloader.extraJson}
              onChange={(v) => updateMessageSummaryOffloader({ extraJson: v })}
              rows={4}
            />
          </SubConfigCard>

          <SubConfigCard title={t('reactTemplates.compressorSessionMemory')}>
            <TriStateField
              label={t('reactTemplates.compressorEnabled')}
              value={ce.sessionMemory.enabled}
              onChange={(v) => updateSessionMemory({ enabled: v })}
            />
            <NumberOrNullField
              label={t('reactTemplates.sessionMemoryTriggerRatio')}
              value={ce.sessionMemory.triggerContextRatio}
              onChange={(v) => updateSessionMemory({ triggerContextRatio: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <div>
              <label className="label">{t('reactTemplates.sessionMemoryPath')}</label>
              <input
                className="input mono"
                value={ce.sessionMemory.sessionMemoryPath}
                onChange={(e) => updateSessionMemory({ sessionMemoryPath: e.target.value })}
              />
            </div>
            <NumberOrNullField
              label={t('reactTemplates.sessionMemoryMaxNotesChars')}
              value={ce.sessionMemory.maxNotesChars}
              integer
              onChange={(v) => updateSessionMemory({ maxNotesChars: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <div className="md:col-span-2">
              <details>
                <summary className="text-[11px] text-muted cursor-pointer">
                  {t('reactTemplates.sessionMemoryMemoryTitle')}
                </summary>
                <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-4">
                  <NumberOrNullField
                    label={t('reactTemplates.sessionMemoryUpdateTriggerRatio')}
                    value={ce.sessionMemory.memoryUpdateTriggerContextRatio}
                    onChange={(v) => updateSessionMemory({ memoryUpdateTriggerContextRatio: v })}
                    placeholder={t('reactTemplates.unconfigured')}
                  />
                  <div>
                    <label className="label">{t('reactTemplates.sessionMemoryUpdateMode')}</label>
                    <input
                      className="input mono"
                      value={ce.sessionMemory.memoryUpdateMode}
                      onChange={(e) => updateSessionMemory({ memoryUpdateMode: e.target.value })}
                    />
                  </div>
                  <NumberOrNullField
                    label={t('reactTemplates.sessionMemoryDirectReplaceRetries')}
                    value={ce.sessionMemory.memoryDirectReplaceMaxRetries}
                    integer
                    onChange={(v) => updateSessionMemory({ memoryDirectReplaceMaxRetries: v })}
                    placeholder={t('reactTemplates.unconfigured')}
                  />
                  <SectionExtraJson
                    label={t('reactTemplates.extraKeys')}
                    hint={t('reactTemplates.sessionMemoryMemoryExtraHint')}
                    value={ce.sessionMemory.memoryExtraJson}
                    onChange={(v) => updateSessionMemory({ memoryExtraJson: v })}
                    rows={4}
                  />
                </div>
              </details>
            </div>
            <SectionExtraJson
              label={t('reactTemplates.extraKeys')}
              hint={t('reactTemplates.sessionMemoryExtraHint')}
              value={ce.sessionMemory.extraJson}
              onChange={(v) => updateSessionMemory({ extraJson: v })}
              rows={4}
            />
          </SubConfigCard>

          <SubConfigCard title={t('reactTemplates.compressorDialogue')}>
            <NumberOrNullField
              label={t('reactTemplates.compressorTriggerRatio')}
              value={ce.dialogueCompressor.triggerContextRatio}
              onChange={(v) => updateDialogueCompressor({ triggerContextRatio: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <NumberOrNullField
              label={t('reactTemplates.compressorMinTargetRatio')}
              value={ce.dialogueCompressor.minTargetContextRatio}
              onChange={(v) => updateDialogueCompressor({ minTargetContextRatio: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <ModelTemplateSelect
              label={t('reactTemplates.compressorModel')}
              value={ce.dialogueCompressor.modelTemplateId}
              onChange={(v) => updateDialogueCompressor({ modelTemplateId: v })}
            />
            <SectionExtraJson
              label={t('reactTemplates.extraKeys')}
              hint={t('reactTemplates.dialogueExtraHint')}
              value={ce.dialogueCompressor.extraJson}
              onChange={(v) => updateDialogueCompressor({ extraJson: v })}
              rows={4}
            />
          </SubConfigCard>

          <SubConfigCard title={t('reactTemplates.compressorCurrentRound')}>
            <NumberOrNullField
              label={t('reactTemplates.compressorTriggerRatio')}
              value={ce.currentRoundCompressor.triggerContextRatio}
              onChange={(v) => updateCurrentRoundCompressor({ triggerContextRatio: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <NumberOrNullField
              label={t('reactTemplates.compressorMinTargetRatio')}
              value={ce.currentRoundCompressor.minTargetContextRatio}
              onChange={(v) => updateCurrentRoundCompressor({ minTargetContextRatio: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <NumberOrNullField
              label={t('reactTemplates.compressorKeepRecent')}
              value={ce.currentRoundCompressor.keepRecentMessages}
              integer
              onChange={(v) => updateCurrentRoundCompressor({ keepRecentMessages: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <ModelTemplateSelect
              label={t('reactTemplates.compressorModel')}
              value={ce.currentRoundCompressor.modelTemplateId}
              onChange={(v) => updateCurrentRoundCompressor({ modelTemplateId: v })}
            />
            <SectionExtraJson
              label={t('reactTemplates.extraKeys')}
              hint={t('reactTemplates.currentRoundExtraHint')}
              value={ce.currentRoundCompressor.extraJson}
              onChange={(v) => updateCurrentRoundCompressor({ extraJson: v })}
              rows={4}
            />
          </SubConfigCard>

          <SubConfigCard title={t('reactTemplates.compressorRoundLevel')}>
            <NumberOrNullField
              label={t('reactTemplates.compressorTriggerRatio')}
              value={ce.roundLevelCompressor.triggerContextRatio}
              onChange={(v) => updateRoundLevelCompressor({ triggerContextRatio: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <NumberOrNullField
              label={t('reactTemplates.compressorMinTargetRatio')}
              value={ce.roundLevelCompressor.minTargetContextRatio}
              onChange={(v) => updateRoundLevelCompressor({ minTargetContextRatio: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <NumberOrNullField
              label={t('reactTemplates.compressorKeepRecent')}
              value={ce.roundLevelCompressor.keepRecentMessages}
              integer
              onChange={(v) => updateRoundLevelCompressor({ keepRecentMessages: v })}
              placeholder={t('reactTemplates.unconfigured')}
            />
            <ModelTemplateSelect
              label={t('reactTemplates.compressorModel')}
              value={ce.roundLevelCompressor.modelTemplateId}
              onChange={(v) => updateRoundLevelCompressor({ modelTemplateId: v })}
            />
            <SectionExtraJson
              label={t('reactTemplates.extraKeys')}
              hint={t('reactTemplates.roundLevelExtraHint')}
              value={ce.roundLevelCompressor.extraJson}
              onChange={(v) => updateRoundLevelCompressor({ extraJson: v })}
              rows={4}
            />
          </SubConfigCard>
        </div>
      )}

      {section === 'advancedJson' && (
        <div className="card">
          <JsonField
            label={t('reactTemplates.advancedJson')}
            hint={t('reactTemplates.advancedJsonHint')}
            value={form.extraJson}
            onChange={(v) => updateForm('extraJson', v)}
            placeholder="{}"
            rows={20}
          />
          {extraJsonError && (
            <div className="text-[11px] text-danger mt-1" role="alert">
              {extraJsonError}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
