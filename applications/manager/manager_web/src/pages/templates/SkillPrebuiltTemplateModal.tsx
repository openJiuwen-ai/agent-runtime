import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { ConfigRefreshCostHint } from '../../components/ConfigRefreshCostHint';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { useFormDirty } from '../../hooks/useFormDirty';
import {
  ExtensionTemplateApi,
  SkillPrebuiltTemplateApi,
  ApiError,
} from '../../services/api';
import { toast } from '../../stores/uiStore';
import { findUnsafeTextField } from '../../utils/safeText';
import { isValidHttpUrl } from '../../utils/url';
import type {
  SkillPrebuiltTemplate,
  SkillPrebuiltTemplateUpdateBody,
} from '../../types';
import { LimitedTextInput } from '../../components/LimitedTextInput';

interface Props {
  open: boolean;
  template: SkillPrebuiltTemplate | null;
  copyFrom?: SkillPrebuiltTemplate;
  onClose: () => void;
  /** created=true 表示新建成功，便于父级展示「去 Agent 模板引用」引导 */
  onSaved: (saved: SkillPrebuiltTemplate & { created: boolean }) => void;
}

/** 与 skill_prebuilt_template 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
  skill_id: 512,
  package_url: 2048,
  source_id: 64,
  version_id: 128,
} as const;

const BUILTIN_SOURCE_ID = 'swarmskillhub';

type InstallMode = 'spi' | 'url';
type SourceKind = 'builtin' | 'extension' | 'used';

type SourceOption = {
  id: string;
  kind: SourceKind;
};

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div className="md:col-span-2 text-xs font-semibold text-muted tracking-wide pt-1">
      {children}
    </div>
  );
}

interface FormState {
  installMode: InstallMode;
  template_name: string;
  description: string;
  skill_id: string;
  package_url: string;
  source_id: string;
  version_id: string;
  sha256: string;
}

const empty: FormState = {
  installMode: 'spi',
  template_name: '',
  description: '',
  skill_id: '',
  package_url: '',
  source_id: '',
  version_id: '',
  sha256: '',
};

function resolveInstallMode(template: SkillPrebuiltTemplate | null): InstallMode {
  if (!template) return 'spi';
  return (template.source_id ?? '').trim() ? 'spi' : 'url';
}

function readDataString(data: Record<string, unknown> | null | undefined, key: string): string {
  if (!data || typeof data !== 'object') return '';
  const value = data[key];
  return typeof value === 'string' ? value : '';
}

function buildTemplateData(
  existing: Record<string, unknown> | null | undefined,
  sha256: string,
  provider: boolean,
  /** 未切换安装方式时保留另一侧历史 sha，避免静默删掉 */
  preserveInactiveSide = false,
): Record<string, unknown> | undefined {
  // 保留已有 data（含历史 author）；界面不再编辑 author
  const next: Record<string, unknown> = {
    ...(existing && typeof existing === 'object' ? { ...existing } : {}),
  };
  const sha = sha256.trim().toLowerCase();
  if (sha && !provider) {
    next.sha256 = sha;
  } else if (!(provider && preserveInactiveSide)) {
    delete next.sha256;
  }
  return Object.keys(next).length ? next : undefined;
}

function collectSourceIdsFromCustomConfig(custom: Record<string, unknown> | null | undefined): string[] {
  if (!custom || typeof custom !== 'object') return [];
  const raw = custom.skill_sources;
  if (!Array.isArray(raw)) return [];
  const ids: string[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue;
    const sid = (entry as { source_id?: unknown }).source_id;
    if (typeof sid === 'string' && sid.trim()) ids.push(sid.trim());
  }
  return ids;
}

function preferSourceKind(current: SourceKind | undefined, next: SourceKind): SourceKind {
  const rank: Record<SourceKind, number> = { builtin: 3, extension: 2, used: 1 };
  if (!current) return next;
  return rank[next] > rank[current] ? next : current;
}

async function loadSkillSourceOptions(): Promise<SourceOption[]> {
  const kinds = new Map<string, SourceKind>([[BUILTIN_SOURCE_ID, 'builtin']]);
  try {
    const [skills, extensions] = await Promise.all([
      SkillPrebuiltTemplateApi.list({ page: 1, page_size: 200 }),
      ExtensionTemplateApi.list({
        page: 1,
        page_size: 100,
        enabled: true,
        component: 'agent_server',
      }),
    ]);
    for (const ext of extensions.items ?? []) {
      for (const sid of collectSourceIdsFromCustomConfig(ext.custom_config ?? undefined)) {
        if (sid === BUILTIN_SOURCE_ID) continue;
        kinds.set(sid, preferSourceKind(kinds.get(sid), 'extension'));
      }
    }
    for (const row of skills.items ?? []) {
      const sid = (row.source_id || '').trim();
      if (!sid || sid === BUILTIN_SOURCE_ID) continue;
      kinds.set(sid, preferSourceKind(kinds.get(sid), 'used'));
    }
  } catch {
    // 下拉仅作辅助；失败时仍保留内置源
  }
  return [...kinds.entries()]
    .map(([id, kind]) => ({ id, kind }))
    .sort((a, b) => {
      if (a.kind !== b.kind) {
        const rank: Record<SourceKind, number> = { builtin: 0, extension: 1, used: 2 };
        return rank[a.kind] - rank[b.kind];
      }
      return a.id.localeCompare(b.id);
    });
}

function sourceOptionLabel(
  option: SourceOption,
  t: (key: string, opts?: Record<string, string>) => string,
): string {
  if (option.kind === 'builtin') {
    return t('skillWhitelistTemplate.sourceIdBuiltin', { id: option.id });
  }
  if (option.kind === 'extension') {
    return t('skillWhitelistTemplate.sourceIdFromExtension', { id: option.id });
  }
  return t('skillWhitelistTemplate.sourceIdFromUsed', { id: option.id });
}

/** 可输入也可点选；列表经 portal 挂到 body，避免被 modal overflow 裁切 */
function SourceCombobox({
  value,
  options,
  maxLength,
  placeholder,
  ariaLabel,
  onChange,
}: {
  value: string;
  options: SourceOption[];
  maxLength: number;
  placeholder: string;
  ariaLabel: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({});
  const hostRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const q = value.trim().toLowerCase();
    if (!q) return options;
    // 已精确选中某项时展示全量，便于切换到其他源
    if (options.some((opt) => opt.id.toLowerCase() === q)) return options;
    return options.filter((opt) => {
      const label = sourceOptionLabel(opt, t).toLowerCase();
      return opt.id.toLowerCase().includes(q) || label.includes(q);
    });
  }, [options, value, t]);

  useLayoutEffect(() => {
    if (!open || !hostRef.current) return;
    const sync = () => {
      const rect = hostRef.current?.getBoundingClientRect();
      if (!rect) return;
      const maxH = 192;
      const spaceBelow = window.innerHeight - rect.bottom - 8;
      const openUp = spaceBelow < Math.min(maxH, 120) && rect.top > spaceBelow;
      setMenuStyle({
        position: 'fixed',
        left: rect.left,
        width: Math.max(rect.width, 220),
        zIndex: 1100,
        maxHeight: Math.min(maxH, openUp ? rect.top - 8 : spaceBelow),
        ...(openUp
          ? { bottom: window.innerHeight - rect.top + 4 }
          : { top: rect.bottom + 4 }),
      });
    };
    sync();
    window.addEventListener('resize', sync);
    window.addEventListener('scroll', sync, true);
    return () => {
      window.removeEventListener('resize', sync);
      window.removeEventListener('scroll', sync, true);
    };
  }, [open, filtered.length]);

  // 点击菜单外（含 modal 其它区域）关闭；host 内由 useDismissWhenPointerLeaves 处理不够时兜底
  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      const path = event.composedPath();
      if (hostRef.current && path.includes(hostRef.current)) return;
      if (menuRef.current && path.includes(menuRef.current)) return;
      setOpen(false);
    };
    window.addEventListener('pointerdown', onDown, true);
    return () => window.removeEventListener('pointerdown', onDown, true);
  }, [open]);

  const menu =
    open && filtered.length > 0
      ? createPortal(
          <div
            ref={menuRef}
            className="dropdown-menu-surface overflow-y-auto"
            style={menuStyle}
            role="listbox"
            aria-label={ariaLabel}
          >
            {filtered.map((opt) => {
              const selected = opt.id === value.trim();
              return (
                <button
                  key={opt.id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  className={`dropdown-menu-option text-left${selected ? ' selected' : ''}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    onChange(opt.id);
                    setOpen(false);
                  }}
                >
                  {sourceOptionLabel(opt, t)}
                </button>
              );
            })}
          </div>,
          document.body,
        )
      : null;

  return (
    <div ref={hostRef}>
      <div className="skill-source-combobox">
        <input
          className="input skill-source-combobox__input"
          aria-label={ariaLabel}
          aria-expanded={open}
          aria-autocomplete="list"
          role="combobox"
          value={value}
          maxLength={maxLength}
          placeholder={placeholder}
          autoComplete="off"
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            onChange(e.target.value.slice(0, maxLength));
            setOpen(true);
          }}
        />
        <button
          type="button"
          className="skill-source-combobox__toggle"
          aria-label={t('skillWhitelistTemplate.sourceIdShowOptions')}
          aria-expanded={open}
          tabIndex={-1}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => setOpen((prev) => !prev)}
        >
          <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path
              fillRule="evenodd"
              d="M5.22 7.22a.75.75 0 0 1 1.06 0L10 10.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 8.28a.75.75 0 0 1 0-1.06Z"
              clipRule="evenodd"
            />
          </svg>
        </button>
        <span
          className={`skill-source-combobox__count text-[11px] tabular-nums shrink-0 ${
            value.length >= maxLength ? 'text-danger' : 'text-muted'
          }`}
          aria-live="polite"
        >
          {value.length}/{maxLength}
        </span>
      </div>
      {menu}
      <p className="text-xs text-muted mt-1">{t('skillWhitelistTemplate.sourceIdComboboxHint')}</p>
    </div>
  );
}

export function SkillPrebuiltTemplateModal({ open, template, copyFrom, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);
  const [sourceOptions, setSourceOptions] = useState<SourceOption[]>([
    { id: BUILTIN_SOURCE_ID, kind: 'builtin' },
  ]);
  /** 打开弹窗时按后端值推导的安装方式；仅当用户主动切换后才清空另一侧字段 */
  const initialInstallModeRef = useRef<InstallMode>('spi');

  useEffect(() => {
    if (!open) return;
    const sourceTemplate = template ?? copyFrom ?? null;
    const packageUrl = sourceTemplate?.package_url || '';
    const next: FormState = sourceTemplate
      ? {
          installMode: resolveInstallMode(sourceTemplate),
          template_name: clipField(sourceTemplate.template_name, FIELD_MAX_LENGTH.template_name),
          description: clipField(sourceTemplate.description ?? '', FIELD_MAX_LENGTH.description),
          skill_id: clipField(sourceTemplate.skill_id, FIELD_MAX_LENGTH.skill_id),
          package_url: clipField(packageUrl, FIELD_MAX_LENGTH.package_url),
          source_id: clipField(sourceTemplate.source_id ?? '', FIELD_MAX_LENGTH.source_id),
          version_id: clipField(sourceTemplate.version_id ?? '', FIELD_MAX_LENGTH.version_id),
          sha256: clipField(readDataString(sourceTemplate.data ?? undefined, 'sha256'), 64),
        }
      : {
          ...empty,
          source_id: BUILTIN_SOURCE_ID,
        };
    if (!template && copyFrom) next.template_name = `${next.template_name.slice(0, 117)} - copy`;
    initialInstallModeRef.current = next.installMode;
    setForm(next);
    markClean(next);

    let cancelled = false;
    void loadSkillSourceOptions().then((options) => {
      if (cancelled) return;
      // 只刷新选项；勿在 markClean 后再改 form，否则编辑 URL 模板会误判 dirty，
      // 也可能覆盖用户在请求返回前已输入的技能源 ID。新建分支的 next 已含内置源。
      setSourceOptions(options);
    });
    return () => {
      cancelled = true;
    };
  }, [open, template, copyFrom, markClean]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const setInstallMode = (mode: InstallMode) => {
    setForm((s) => {
      if (mode !== 'spi' || s.source_id.trim()) {
        return { ...s, installMode: mode };
      }
      const fallback = sourceOptions[0]?.id ?? BUILTIN_SOURCE_ID;
      return { ...s, installMode: mode, source_id: fallback };
    });
  };

  const submit = async () => {
    if (!form.template_name.trim()) {
      toast('warn', t('skillWhitelistTemplate.fieldRequired', { field: t('skillWhitelistTemplate.templateName') }));
      return;
    }
    if (!form.skill_id.trim()) {
      toast('warn', t('skillWhitelistTemplate.fieldRequired', { field: t('skillWhitelistTemplate.skillId') }));
      return;
    }

    const provider = form.installMode === 'spi';
    const modeSwitched = Boolean(template ?? copyFrom) && form.installMode !== initialInstallModeRef.current;
    const sourceId = form.source_id.trim();
    const versionId = form.version_id.trim();
    const packageUrl = form.package_url.trim();

    if (provider) {
      if (!sourceId) {
        toast('warn', t('skillWhitelistTemplate.fieldRequired', { field: t('skillWhitelistTemplate.sourceId') }));
        return;
      }
      if (!versionId) {
        toast('warn', t('skillWhitelistTemplate.fieldRequired', { field: t('skillWhitelistTemplate.versionId') }));
        return;
      }
    } else {
      if (!packageUrl) {
        toast('warn', t('skillWhitelistTemplate.fieldRequired', { field: t('skillWhitelistTemplate.packageUrl') }));
        return;
      }
      if (!isValidHttpUrl(packageUrl)) {
        toast('warn', t('skillWhitelistTemplate.skillSourceInvalid'));
        return;
      }
    }

    const unsafeChecks = [
      { label: t('skillWhitelistTemplate.templateName'), value: form.template_name },
      { label: t('skillWhitelistTemplate.templateDescription'), value: form.description },
      { label: t('skillWhitelistTemplate.skillId'), value: form.skill_id },
    ];
    if (provider) {
      unsafeChecks.push(
        { label: t('skillWhitelistTemplate.sourceId'), value: form.source_id },
        { label: t('skillWhitelistTemplate.versionId'), value: form.version_id },
      );
    }
    const unsafeField = findUnsafeTextField(unsafeChecks);
    if (unsafeField) {
      toast('warn', t('skillWhitelistTemplate.unsafeText', { field: unsafeField }));
      return;
    }

    const sha = provider ? '' : form.sha256.trim().toLowerCase();
    if (sha && !/^[0-9a-f]{64}$/.test(sha)) {
      toast('warn', t('skillWhitelistTemplate.sha256Invalid'));
      return;
    }

    const description = form.description.trim() || null;
    // 未切换安装方式时保留另一侧历史字段（含 data.sha256），避免双字段旧数据被静默清掉
    const data = buildTemplateData(
      (template ?? copyFrom)?.data ?? undefined,
      sha,
      provider,
      Boolean(template ?? copyFrom) && !modeSwitched,
    );
    const created = !template;

    setSaving(true);
    try {
      let saved: SkillPrebuiltTemplate;
      if (template) {
        const patch: SkillPrebuiltTemplateUpdateBody = {
          template_name: form.template_name.trim(),
          description,
          skill_id: form.skill_id.trim(),
          data: data ?? null,
        };
        if (provider) {
          patch.source_id = sourceId;
          patch.version_id = versionId;
          // 仅用户主动切到技能源时才清 URL；未切换则省略，保留历史 package_url
          if (modeSwitched && template.package_url) {
            patch.package_url = null;
          }
        } else {
          patch.package_url = packageUrl;
          // 仅用户主动切到技能包时才清 SPI 字段
          if (modeSwitched) {
            patch.source_id = '';
            patch.version_id = '';
          }
        }
        saved = await SkillPrebuiltTemplateApi.update(template.template_id, patch);
        toast('success', t('success.saved'));
      } else {
        saved = await SkillPrebuiltTemplateApi.create({
          template_name: form.template_name.trim(),
          description,
          skill_id: form.skill_id.trim(),
          package_url: provider ? !modeSwitched ? copyFrom?.package_url ?? undefined : undefined : packageUrl || undefined,
          source_id: provider ? sourceId || undefined : !modeSwitched ? copyFrom?.source_id ?? undefined : undefined,
          version_id: provider ? versionId || undefined : !modeSwitched ? copyFrom?.version_id ?? undefined : undefined,
          enabled: true,
          data,
        });
        toast('success', t('skillWhitelistTemplate.savedNeedBind'));
      }
      onSaved({ ...saved, created });
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  const isSpi = form.installMode === 'spi';

  return (
    <Modal
      open={open}
      title={template ? t('skillWhitelistTemplate.edit') : t('skillWhitelistTemplate.new')}
      onClose={onClose}
      dirty={isDirty(form)}
      size="lg"
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" onClick={submit} disabled={saving}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <SectionTitle>{t('skillWhitelistTemplate.sectionBasic')}</SectionTitle>
        <div className="md:col-span-2">
          <FieldLabel required>{t('skillWhitelistTemplate.templateName')}</FieldLabel>
          <LimitedTextInput
            value={form.template_name}
            maxLength={FIELD_MAX_LENGTH.template_name}
            onChange={(v) => update('template_name', v)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('skillWhitelistTemplate.templateDescription')}</FieldLabel>
          <LimitedTextInput
            value={form.description}
            maxLength={FIELD_MAX_LENGTH.description}
            onChange={(v) => update('description', v)}
          />
        </div>

        <SectionTitle>{t('skillWhitelistTemplate.sectionInstall')}</SectionTitle>
        <div className="md:col-span-2">
          <FieldLabel required>{t('skillWhitelistTemplate.installMode')}</FieldLabel>
          <div className="flex gap-2" role="radiogroup" aria-label={t('skillWhitelistTemplate.installMode')}>
            <button
              type="button"
              role="radio"
              aria-checked={isSpi}
              className={`btn sm ${isSpi ? 'primary' : 'ghost'}`}
              onClick={() => setInstallMode('spi')}
            >
              {t('skillWhitelistTemplate.modeSpiLabel')}
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={!isSpi}
              className={`btn sm ${!isSpi ? 'primary' : 'ghost'}`}
              onClick={() => setInstallMode('url')}
            >
              {t('skillWhitelistTemplate.modeUrlLabel')}
            </button>
          </div>
          <p className="text-xs text-muted mt-1">
            {isSpi ? t('skillWhitelistTemplate.modeSpiHint') : t('skillWhitelistTemplate.modeUrlHint')}
          </p>
        </div>

        <div className="md:col-span-2">
          <FieldLabel required>{t('skillWhitelistTemplate.skillId')}</FieldLabel>
          <LimitedTextInput
            value={form.skill_id}
            maxLength={FIELD_MAX_LENGTH.skill_id}
            onChange={(v) => update('skill_id', v)}
            placeholder={
              isSpi
                ? t('skillWhitelistTemplate.skillIdHintSpi')
                : t('skillWhitelistTemplate.skillIdHintUrl')
            }
          />
        </div>

        {isSpi ? (
          <>
            <div>
              <FieldLabel required>{t('skillWhitelistTemplate.sourceId')}</FieldLabel>
              <SourceCombobox
                ariaLabel={t('skillWhitelistTemplate.sourceId')}
                value={form.source_id}
                options={sourceOptions}
                maxLength={FIELD_MAX_LENGTH.source_id}
                placeholder={t('skillWhitelistTemplate.sourceIdHint')}
                onChange={(v) => update('source_id', v)}
              />
            </div>
            <div>
              <FieldLabel required>{t('skillWhitelistTemplate.versionId')}</FieldLabel>
              <LimitedTextInput
                value={form.version_id}
                maxLength={FIELD_MAX_LENGTH.version_id}
                onChange={(v) => update('version_id', v)}
                placeholder={t('skillWhitelistTemplate.versionIdHint')}
              />
            </div>
          </>
        ) : (
          <>
            <div className="md:col-span-2">
              <FieldLabel required>{t('skillWhitelistTemplate.packageUrl')}</FieldLabel>
              <LimitedTextInput
                value={form.package_url}
                maxLength={FIELD_MAX_LENGTH.package_url}
                onChange={(v) => update('package_url', v)}
                placeholder={t('skillWhitelistTemplate.packageUrlHint')}
              />
            </div>
            <div className="md:col-span-2">
              <FieldLabel>{t('skillWhitelistTemplate.sha256')}</FieldLabel>
              <LimitedTextInput
                value={form.sha256}
                maxLength={64}
                onChange={(v) => update('sha256', v)}
                placeholder={t('skillWhitelistTemplate.sha256Hint')}
              />
            </div>
          </>
        )}
      </div>
      {template && <ConfigRefreshCostHint scope="template" />}
    </Modal>
  );
}
