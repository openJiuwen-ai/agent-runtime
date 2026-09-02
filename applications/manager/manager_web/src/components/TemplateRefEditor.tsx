import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  EmbeddingTemplateApi,
  ExtensionTemplateApi,
  ModelTemplateApi,
  PermissionsTemplateApi,
  SkillWhitelistTemplateApi,
} from '../services/api';
import {
  TEMPLATE_REF_EDITOR_SLOTS,
  isMultiValueTemplateRefSlot,
  isTemplateRefEditorSlot,
  parseRefChain,
  type TemplateRefEditorSlot,
  type TemplateRefMap,
} from '../utils/templateRef';

export interface TemplateOption {
  template_id: string;
  label: string;
}

interface TemplateRefEditorProps {
  label?: string;
  hint?: string;
  required?: boolean;
  value: TemplateRefMap;
  onChange: (value: TemplateRefMap) => void;
}

export async function loadTemplateOptions(): Promise<Record<string, TemplateOption[]>> {
  const pageSize = 200;
  const [models, embeddings, skills, extensions, permissions] = await Promise.all([
    ModelTemplateApi.list({ page: 1, page_size: pageSize, enabled: true }),
    EmbeddingTemplateApi.list({ page: 1, page_size: pageSize, enabled: true }),
    SkillWhitelistTemplateApi.list({ page: 1, page_size: pageSize, enabled: true }),
    ExtensionTemplateApi.list({ page: 1, page_size: pageSize, enabled: true }),
    PermissionsTemplateApi.list({ page: 1, page_size: pageSize, enabled: true }),
  ]);

  const toOpt = (id: string, name: string): TemplateOption => ({
    template_id: id,
    label: name ? `${name} (${id})` : id,
  });

  const modelOptions = (models.items ?? []).map((m) => toOpt(m.template_id, m.template_name));
  const bySlot: Record<string, TemplateOption[]> = {};
  for (const slot of ['default_model', 'video_model', 'audio_model', 'vision_model'] as const) {
    bySlot[slot] = modelOptions;
  }

  bySlot.embedding_model = (embeddings.items ?? []).map((t) =>
    toOpt(t.template_id, t.template_name),
  );
  bySlot.skill_whitelist = (skills.items ?? []).map((t) => toOpt(t.template_id, t.template_name));
  bySlot.extension_config = (extensions.items ?? []).map((t) => toOpt(t.template_id, t.template_name));
  bySlot.permissions = (permissions.items ?? []).map((t) => toOpt(t.template_id, t.template_name));
  return bySlot;
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
        className="select w-full text-left flex items-center justify-between gap-2"
        onClick={toggleOpen}
      >
        <span className={selected.length ? 'truncate' : 'text-muted truncate'}>{display}</span>
        <span className="text-muted text-xs shrink-0">{open ? '▲' : '▼'}</span>
      </button>
      <div
        ref={setPopoverEl}
        className="fixed z-[80] max-h-56 overflow-auto rounded-md border border-border bg-bg shadow-lg py-1"
      >
        {items.length === 0 ? (
          <div className="px-3 py-2 text-xs text-muted">{t('policies.templateRef.noOptions')}</div>
        ) : (
          items.map((opt) => (
            <label
              key={opt.template_id}
              className="flex items-center gap-2 px-3 py-2 hover:bg-bg-hover cursor-pointer text-sm"
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
  hint,
  required,
  value,
  onChange,
}: TemplateRefEditorProps) {
  const { t } = useTranslation();
  const [editor, setEditor] = useState(() => editorValueFromMap(value));
  const extraSlotsRef = useRef<TemplateRefMap>(extraSlotsFromMap(value));
  const [templateOptions, setTemplateOptions] = useState<Record<string, TemplateOption[]>>({});
  const [loadingTemplates, setLoadingTemplates] = useState(false);

  const emit = useCallback(
    (next: Record<TemplateRefEditorSlot, string[]>) => {
      setEditor(next);
      onChange(serializeEditorValue(next, extraSlotsRef.current));
    },
    [onChange],
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
    void loadTemplateOptions()
      .then((opts) => {
        if (!cancelled) setTemplateOptions(opts);
      })
      .finally(() => {
        if (!cancelled) setLoadingTemplates(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setSlotIds = (slot: TemplateRefEditorSlot, ids: string[]) => {
    emit({ ...editor, [slot]: ids });
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
      {loadingTemplates && (
        <div className="text-[11px] text-muted mb-2">{t('policies.templateRef.loadingTemplates')}</div>
      )}

      <div className="flex flex-col gap-2">
        {TEMPLATE_REF_EDITOR_SLOTS.map((slot) => {
          const options = templateOptions[slot] ?? [];
          const selected = editor[slot] ?? [];
          const multi = isMultiValueTemplateRefSlot(slot);
          return (
            <div key={slot} className="flex items-start gap-2">
              <div className="w-32 shrink-0 pt-2 text-xs font-medium text-muted whitespace-nowrap">
                {t(`policies.templateRef.slots.${slot}`, { defaultValue: slot })}
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
                    className="select w-full"
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
            </div>
          );
        })}
      </div>
    </div>
  );
}
