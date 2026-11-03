import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { HintTooltip } from './HintTooltip';
import { GuideLink } from './GuideLink';
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
  default_model: 'nav.modelTemplates',
  video_model: 'nav.modelTemplates',
  audio_model: 'nav.modelTemplates',
  vision_model: 'nav.modelTemplates',
  image_gen_model: 'nav.modelTemplates',
  embedding_model: 'nav.embeddingTemplates',
  skill_prebuilt: 'nav.skillWhitelistTemplates',
  extension_config: 'nav.extensionTemplates',
  mcp: 'nav.mcpTemplates',
  permissions: 'nav.safetyGuardrails',
  a2a_access_policy: 'nav.a2aManagement',
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
}: {
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
  hint,
  required,
  requiredSlots,
  value,
  onChange,
}: TemplateRefEditorProps) {
  const { t } = useTranslation();
  const [editor, setEditor] = useState(() => editorValueFromMap(value));
  const extraSlotsRef = useRef<TemplateRefMap>(extraSlotsFromMap(value));
  const [templateOptions, setTemplateOptions] = useState<Record<string, TemplateOption[]>>({});
  const [loadingTemplates, setLoadingTemplates] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [optionalOpen, setOptionalOpen] = useState(false);

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

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="label !mb-0">
          {label ?? t('policies.templateRef.title')}
          {required ? <span className="text-danger ml-0.5" aria-hidden="true">*</span> : null}
        </label>
        {hint && <span className="text-[11px] text-muted">{hint}</span>}
      </div>
      <div className="flex flex-wrap items-center gap-2 mb-2 text-xs text-muted">
        {resourceLinksInNewTab && <span>{t('agentTemplate.resourceTabHint')}</span>}
        <button type="button" className="btn ghost sm" disabled={loadingTemplates} onClick={() => setRevision(v => v + 1)}>{t('common.refresh')}</button>
      </div>
      {loadError && <div role="alert" className="text-danger text-sm mb-2">{t('errors.loadFailed', { detail: loadError })}</div>}
      {loadingTemplates && (
        <div className="text-[11px] text-muted mb-2">{t('policies.templateRef.loadingTemplates')}</div>
      )}

      <div className="flex flex-col gap-2">
        {TEMPLATE_REF_EDITOR_SLOTS.filter(slot => !collapseOptional || optionalOpen || requiredSlots?.includes(slot)).map((slot) => {
          const options = templateOptions[slot] ?? [];
          const selected = editor[slot] ?? [];
          const multi = isMultiValueTemplateRefSlot(slot);
          const configPath = SLOT_CONFIG_PATH[slot];
          const hintPageKey = SLOT_HINT_PAGE_KEY[slot];
          const showHint = !!configPath && !!hintPageKey;
          return (
            <div key={slot} className="flex items-start gap-2">
              <div className="w-32 shrink-0 pt-2 text-xs font-medium text-muted whitespace-nowrap">
                {t(`policies.templateRef.slots.${slot}`, { defaultValue: slot })}
                {requiredSlots?.includes(slot) && (
                  <span className="text-danger ml-0.5" aria-hidden="true">*</span>
                )}
              </div>
              <div className="min-w-0 flex-1">
                {multi ? (
                  <CheckboxSelect
                    options={options}
                    selected={selected}
                    onChange={(ids) => setSlotIds(slot, ids)}
                  />
                ) : (
                  <select
                    className="select h-10 w-full"
                    aria-label={t(`policies.templateRef.slots.${slot}`, { defaultValue: slot })}
                    value={selected[0] ?? ''}
                    onChange={(e) => setSlotIds(slot, e.target.value ? [e.target.value] : [])}
                  >
                    <option value="">{t('policies.templateRef.none')}</option>
                    {selected[0] && !options.some((o) => o.template_id === selected[0]) ? (
                      <option value={selected[0]}>{selected[0]}</option>
                    ) : null}
                    {options.map((opt) => (
                      <option key={opt.template_id} value={opt.template_id}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              {showHint && (resourceLinksInNewTab
                ? <a href={`/manager${configPath}`} target="_blank" rel="noopener noreferrer" className="text-xs text-accent hover:underline self-center max-w-28">{t('guide.gotoPageLink', { page: t(hintPageKey) })} ↗</a>
                : <HintTooltip className="ml-1.5 self-center" text={t('guide.gotoPageLink', { page: t(hintPageKey) })}>
                  <GuideLink page={t(hintPageKey)} to={configPath!} className="text-[11px] leading-snug text-accent hover:text-accent-hover hover:underline" />
                </HintTooltip>)}
            </div>
          );
        })}
      </div>
      {collapseOptional && <button type="button" className="btn ghost sm mt-3" aria-expanded={optionalOpen} onClick={() => setOptionalOpen(value => !value)}>{optionalOpen ? '−' : '+'} {t('agentTemplate.optionalResources')} · {TEMPLATE_REF_EDITOR_SLOTS.filter(slot => !requiredSlots?.includes(slot) && value[slot]?.length).length}</button>}
    </div>
  );
}
