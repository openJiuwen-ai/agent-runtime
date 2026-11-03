import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { HintTooltip } from './HintTooltip';
import { GuideLink } from './GuideLink';
import './AgentCapabilityCards.css';
import {
  TEMPLATE_REF_EDITOR_SLOTS,
  isMultiValueTemplateRefSlot,
  isTemplateRefEditorSlot,
  parseRefChain,
  type TemplateRefEditorSlot,
  type TemplateRefMap,
} from '../utils/templateRef';

import { loadTemplateOptions, type TemplateOption } from '../services/templateOptions';
export { loadTemplateOptions } from '../services/templateOptions';

/** 槽位 → 对应资源管理页路径（用于「前往 xxx 页添加配置」引导跳转） */
const SLOT_CONFIG_PATH: Record<string, string> = {
  default_model: '/model-templates',
  video_model: '/model-templates',
  audio_model: '/model-templates',
  vision_model: '/model-templates',
  image_gen_model: '/model-templates',
  embedding_model: '/embedding-templates',
  skill_prebuilt: '/skill-prebuilt-templates',
  extension_config: '/extension-config-templates',
  mcp: '/mcp-templates',
  permissions: '/safety-guardrails',
  a2a_access_policy: '/a2a-management?tab=policies',
};

/** 槽位 → 引导页面名称（用于「前往 {页面} 页面 →」链接文案） */
const SLOT_HINT_PAGE_KEY: Record<string, string> = {
  default_model: 'modelTemplate.title',
  video_model: 'modelTemplate.title',
  audio_model: 'modelTemplate.title',
  vision_model: 'modelTemplate.title',
  image_gen_model: 'modelTemplate.title',
  embedding_model: 'embeddingTemplate.title',
  skill_prebuilt: 'skillWhitelistTemplate.title',
  extension_config: 'extensionTemplate.title',
  mcp: 'mcpTemplate.title',
  permissions: 'safetyGuardrails.title',
  a2a_access_policy: 'a2aManagement.title',
};

function SelectChevron({ open = false }: { open?: boolean }) {
  return (
    <svg
      className={`h-4 w-4 shrink-0 text-muted transition-transform ${open ? 'rotate-180' : ''}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      aria-hidden
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
    </svg>
  );
}

interface TemplateRefEditorProps {
  label?: string;
  resourceLinksInNewTab?: boolean;
  collapseOptional?: boolean;
  optionalContent?: ReactNode;
  optionalSelectedCount?: number;
  expandOptional?: boolean;
  hint?: string;
  required?: boolean;
  /** 星号标注在指定槽位标签右侧（如 default_model），而非整体标题 */
  requiredSlots?: readonly string[];
  value: TemplateRefMap;
  onChange: (value: TemplateRefMap) => void;
}


function extractTemplateIds(refs: string[] | undefined): string[] {
  if (!refs?.length) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const ref of refs) {
    for (const segment of parseRefChain(ref).segments) {
      const id = segment.mode === 'template' ? segment.templateId.trim() : '';
      if (!id || seen.has(id)) continue;
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

function editorValueFromMap(value: TemplateRefMap): Record<TemplateRefEditorSlot, string[]> {
  const out = {} as Record<TemplateRefEditorSlot, string[]>;
  for (const slot of TEMPLATE_REF_EDITOR_SLOTS) {
    const ids = extractTemplateIds(value[slot]);
    out[slot] = isMultiValueTemplateRefSlot(slot) ? ids : ids.slice(0, 1);
  }
  return out;
}

function extraSlotsFromMap(value: TemplateRefMap): TemplateRefMap {
  const extra: TemplateRefMap = {};
  for (const [slot, refs] of Object.entries(value)) {
    if (slot === 'service_config') continue;
    if (!isTemplateRefEditorSlot(slot) && refs?.length) extra[slot] = refs;
  }
  return extra;
}

function serializeEditorValue(
  editor: Record<TemplateRefEditorSlot, string[]>,
  extra: TemplateRefMap,
): TemplateRefMap {
  const out: TemplateRefMap = { ...extra };
  for (const slot of TEMPLATE_REF_EDITOR_SLOTS) {
    const ids = editor[slot].map((id) => id.trim()).filter(Boolean);
    if (ids.length) out[slot] = ids;
    else delete out[slot];
  }
  return out;
}

function CheckboxSelect({
  options,
  selected,
  onChange,
  label,
}: {
  label: string;
  options: TemplateOption[];
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const { t } = useTranslation();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [popoverEl, setPopoverEl] = useState<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const optionIds = useMemo(() => new Set(options.map((o) => o.template_id)), [options]);
  const orphanIds = selected.filter((id) => !optionIds.has(id));
  const labelById = useMemo(() => {
    const map = new Map(options.map((o) => [o.template_id, o.label]));
    for (const id of orphanIds) map.set(id, id);
    return map;
  }, [options, orphanIds]);

  const positionPopover = useCallback(() => {
    const trigger = triggerRef.current;
    const el = popoverEl;
    if (!trigger || !el) return;
    const r = trigger.getBoundingClientRect();
    const maxH = 224;
    const spaceBelow = window.innerHeight - r.bottom;
    const openUp = spaceBelow < Math.min(maxH, 160) && r.top > spaceBelow;
    el.style.inset = 'auto';
    el.style.margin = '0';
    el.style.left = `${r.left}px`;
    el.style.width = `${r.width}px`;
    el.style.maxHeight = `${maxH}px`;
    if (openUp) {
      el.style.bottom = `${window.innerHeight - r.top + 4}px`;
      el.style.top = 'auto';
    } else {
      el.style.top = `${r.bottom + 4}px`;
      el.style.bottom = 'auto';
    }
  }, [popoverEl]);

  const close = useCallback(() => {
    const el = popoverEl;
    if (el && 'hidePopover' in el) {
      try {
        (el as HTMLElement).hidePopover();
      } catch {
        /* already closed */
      }
    }
    setOpen(false);
  }, [popoverEl]);

  const toggleOpen = () => {
    const el = popoverEl;
    if (el && 'showPopover' in el) {
      if (open) close();
      else {
        positionPopover();
        (el as HTMLElement).showPopover();
        setOpen(true);
      }
      return;
    }
    setOpen((v) => !v);
  };

  useEffect(() => {
    if (popoverEl && 'showPopover' in popoverEl) {
      popoverEl.setAttribute('popover', 'manual');
    }
  }, [popoverEl]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target) || popoverEl?.contains(target)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', positionPopover);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', positionPopover);
    };
  }, [open, close, positionPopover, popoverEl]);

  const toggle = (id: string) => {
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  };

  const display = selected.length
    ? selected.map((id) => labelById.get(id) ?? id).join(', ')
    : t('policies.templateRef.none');
  const items = [...orphanIds.map((id) => ({ template_id: id, label: id })), ...options];

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        className="select flex h-10 w-full cursor-pointer items-center justify-between gap-2 pr-3 text-left"
        onClick={toggleOpen}
        aria-expanded={open}
        aria-label={label}
      >
        <span className={selected.length ? 'truncate' : 'text-muted truncate'}>{display}</span>
        <SelectChevron open={open} />
      </button>
      <div
        ref={setPopoverEl}
        className="dropdown-menu-surface fixed z-[80] max-h-56 overflow-auto"
      >
        {items.length === 0 ? (
          <div className="px-3 py-2 text-xs text-muted">{t('policies.templateRef.noOptions')}</div>
        ) : (
          items.map((opt) => (
            <label
              key={opt.template_id}
              className="dropdown-menu-option flex items-center gap-2"
            >
              <input
                type="checkbox"
                checked={selected.includes(opt.template_id)}
                onChange={() => toggle(opt.template_id)}
              />
              <span className="truncate">{opt.label}</span>
            </label>
          ))
        )}
      </div>
    </div>
  );
}

export function TemplateRefEditor({
  label,
  resourceLinksInNewTab = false,
  collapseOptional = false,
  optionalContent,
  optionalSelectedCount = 0,
  expandOptional = false,
  hint,
  required,
  requiredSlots,
  value,
  onChange,
}: TemplateRefEditorProps) {
  const { t, i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const [editor, setEditor] = useState(() => editorValueFromMap(value));
  const extraSlotsRef = useRef<TemplateRefMap>(extraSlotsFromMap(value));
  const [templateOptions, setTemplateOptions] = useState<Record<string, TemplateOption[]>>({});
  const [loadingTemplates, setLoadingTemplates] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [optionalOpen, setOptionalOpen] = useState(false);
  useEffect(() => { if (expandOptional) setOptionalOpen(true); }, [expandOptional]);

  const emit = useCallback(
    (next: Record<TemplateRefEditorSlot, string[]>, changedSlot: TemplateRefEditorSlot) => {
      setEditor(next);
      // A selection edits only its own slot. Preserve imported fallback/dynamic
      // references in other slots instead of flattening them as a side effect.
      const serialized = serializeEditorValue(next, extraSlotsRef.current);
      const updated = { ...value };
      if (serialized[changedSlot]?.length) updated[changedSlot] = serialized[changedSlot];
      else delete updated[changedSlot];
      onChange(updated);
    },
    [onChange, value],
  );

  useEffect(() => {
    extraSlotsRef.current = extraSlotsFromMap(value);
    setEditor((current) => {
      const next = editorValueFromMap(value);
      return JSON.stringify(current) === JSON.stringify(next) ? current : next;
    });
  }, [value]);

  useEffect(() => {
    let cancelled = false;
    setLoadingTemplates(true);
    setLoadError(null);
    void loadTemplateOptions()
      .then((opts) => {
        if (!cancelled) setTemplateOptions(opts);
      })
      .catch((error: Error) => { if (!cancelled) setLoadError(error.message); })
      .finally(() => {
        if (!cancelled) setLoadingTemplates(false);
      });
    return () => {
      cancelled = true;
    };
  }, [revision]);

  const setSlotIds = (slot: TemplateRefEditorSlot, ids: string[]) => {
    emit({ ...editor, [slot]: ids }, slot);
  };

  const slotDescription: Record<TemplateRefEditorSlot, string> = {
    default_model: tr('选择 Agent 对话和推理使用的默认模型。', 'Choose the default model for Agent conversations and reasoning.'),
    video_model: tr('为视频理解任务选择模型。', 'Choose a model for video understanding.'),
    audio_model: tr('为音频理解任务选择模型。', 'Choose a model for audio understanding.'),
    vision_model: tr('为图像理解任务选择模型。', 'Choose a model for image understanding.'),
    image_gen_model: tr('为图片生成任务选择模型。', 'Choose a model for image generation.'),
    embedding_model: tr('选择文本向量化使用的模型。', 'Choose a model for text embeddings.'),
    skill_prebuilt: tr('选择 Agent 可使用的预置技能源，可选择多个。', 'Select one or more prebuilt skill sources for this Agent.'),
    extension_config: tr('组合可复用的扩展配置，可选择多个。', 'Combine one or more reusable extension configurations.'),
    mcp: tr('连接 Agent 可调用的 MCP 工具服务，可选择多个。', 'Connect one or more MCP tool services for this Agent.'),
    permissions: tr('设置 Agent 调用工具时遵循的安全策略。', 'Set the safety policy for Agent tool calls.'),
    a2a_access_policy: tr('限制此 Agent 可访问的外部 Agent 范围。', 'Limit the external Agents this Agent can access.'),
  };
  const renderSlot = (slot: TemplateRefEditorSlot) => {
    const options = templateOptions[slot] ?? [];
    const selected = editor[slot] ?? [];
    const multi = isMultiValueTemplateRefSlot(slot);
    const configPath = SLOT_CONFIG_PATH[slot];
    const hintPageKey = SLOT_HINT_PAGE_KEY[slot];
    const title = t(`policies.templateRef.slots.${slot}`, { defaultValue: slot });
    const isRequired = requiredSlots?.includes(slot);
    return <section key={slot} className="agent-capability-card" aria-label={title}>
      <div className="agent-capability-card__header">
        <div className="agent-capability-card__heading"><strong>{title}{isRequired ? <span className="text-danger ml-1" aria-hidden="true">*</span> : ` · ${tr('可选', 'Optional')}`}</strong><p>{slotDescription[slot]}</p></div>
        {configPath && hintPageKey && (resourceLinksInNewTab
          ? <a href={`/manager${configPath}`} target="_blank" rel="noopener noreferrer" className="btn sm agent-capability-card__link">{t('guide.gotoPageLink', { page: t(hintPageKey) })} ↗</a>
          : <HintTooltip text={t('guide.gotoPageLink', { page: t(hintPageKey) })}><GuideLink page={t(hintPageKey)} to={configPath} className="btn sm agent-capability-card__link" /></HintTooltip>)}
      </div>
      {multi ? <CheckboxSelect label={title} options={options} selected={selected} onChange={(ids) => setSlotIds(slot, ids)} /> : <select
        className="select h-10 w-full" aria-label={title} value={selected[0] ?? ''}
        onChange={(e) => setSlotIds(slot, e.target.value ? [e.target.value] : [])}>
        <option value="">{t('policies.templateRef.none')}</option>
        {selected[0] && !options.some((option) => option.template_id === selected[0]) && <option value={selected[0]}>{selected[0]}</option>}
        {options.map(option => <option key={option.template_id} value={option.template_id}>{option.label}</option>)}
      </select>}
    </section>;
  };
  const optionalCount = TEMPLATE_REF_EDITOR_SLOTS.filter(slot => !requiredSlots?.includes(slot) && value[slot]?.length).length + optionalSelectedCount;

  return (
    <div className="agent-capabilities">
      <div className="agent-capabilities__heading">
        <div><strong className="text-sm">{label ?? t('policies.templateRef.title')}{required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}</strong>{hint && <p className="text-xs text-muted mt-1">{hint}</p>}</div>
        <button type="button" className="btn sm" disabled={loadingTemplates} onClick={() => setRevision(v => v + 1)}>{t('common.refresh')}</button>
      </div>
      {resourceLinksInNewTab && <p className="text-xs text-muted">{t('agentTemplate.resourceTabHint')}</p>}
      {loadError && <div role="alert" className="text-danger text-sm">{t('errors.loadFailed', { detail: loadError })}</div>}
      {loadingTemplates && <div className="text-xs text-muted">{t('policies.templateRef.loadingTemplates')}</div>}
      {(collapseOptional ? TEMPLATE_REF_EDITOR_SLOTS.filter(slot => requiredSlots?.includes(slot)) : TEMPLATE_REF_EDITOR_SLOTS).map(renderSlot)}
      {collapseOptional ? <>
        <button type="button" className="agent-capabilities__toggle" aria-expanded={optionalOpen} onClick={() => setOptionalOpen(value => !value)}>
          <span><strong>{t('agentTemplate.optionalResources')}</strong><small>{tr('按需添加模型、技能、工具与扩展能力。', 'Add models, skills, tools and extensions as needed.')}</small></span>
          <span className="agent-capabilities__toggle-state">{optionalCount > 0 && <span className="tag">{tr(`已配置 ${optionalCount} 项`, `${optionalCount} configured`)}</span>}<SelectChevron open={optionalOpen} /></span>
        </button>
        <div className="agent-capabilities__optional" hidden={!optionalOpen}>
          {TEMPLATE_REF_EDITOR_SLOTS.filter(slot => !requiredSlots?.includes(slot)).map(renderSlot)}
          {optionalContent}
        </div>
      </> : optionalContent}
    </div>
  );
}
