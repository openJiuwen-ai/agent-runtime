import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { Switch } from '../../components/Switch';
import { ExpertPackageTemplateApi, PackageCatalogApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type { ExpertPackageKind, ExpertPackageTemplate, PackageCatalogItem, SwarmflowConfig } from '../../types';

interface Props {
  open: boolean;
  template: ExpertPackageTemplate | null;
  onClose: () => void;
  onSaved: () => void;
  /** 409 冲突：关弹窗并刷新列表，语义与 onSaved 分离。 */
  onConflict: () => void;
}

interface Selection {
  kind: ExpertPackageKind;
  id: string;
  displayName: string;
  version: string;
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

function itemKey(item: { kind: string; id: string }) {
  return `${item.kind}:${item.id}`;
}

const DEFAULT_SWARMFLOW: SwarmflowConfig = {
  allowed: true,
  default_enabled: false,
  max_workflows: 16,
  agents_per_run: null,
  max_agents_total: 64,
  swarmflow_budget: null,
};

/** 数字输入的草稿态：允许暂空（null），保存时统一校验，避免 onChange 回填默认值顶掉正在编辑的内容。 */
type SwarmflowDraft = Omit<
  SwarmflowConfig,
  'max_workflows' | 'agents_per_run' | 'max_agents_total' | 'swarmflow_budget'
> & {
  max_workflows: number | null;
  agents_per_run?: number | null;
  max_agents_total: number | null;
  swarmflow_budget?: number | null;
};

function parsePositiveInt(value: string): number | null {
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 ? n : null;
}

export function ExpertPackageTemplateModal({ open, template, onClose, onSaved, onConflict }: Props) {
  const { t } = useTranslation();
  const editing = template !== null;
  const [catalog, setCatalog] = useState<PackageCatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [selected, setSelected] = useState<Selection[]>([]);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [swarmflow, setSwarmflow] = useState<SwarmflowDraft>(DEFAULT_SWARMFLOW);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (template) {
      setName(template.template_name);
      setDescription(template.description || '');
      setSwarmflow({ ...DEFAULT_SWARMFLOW, ...(template.swarmflow ?? {}) });
      setSelected((template.packages ?? []).map((item) => ({
        kind: item.kind,
        id: item.asset_id,
        displayName: item.asset_id,
        version: item.version || '',
      })));
    } else {
      setName('');
      setDescription('');
      setSwarmflow(DEFAULT_SWARMFLOW);
      setSelected([]);
    }
    setCatalogLoading(true);
    setCatalogError('');
    PackageCatalogApi.catalog()
      .then((page) => {
        const items = page.items ?? [];
        setCatalog(items);
        setCatalogError(page.error || '');
        setSelected((current) => current.map((item) => {
          const found = items.find((row) => row.kind === item.kind && row.id === item.id);
          if (!found) return item;
          return {
            ...item,
            displayName: found.display_name || item.id,
            version: item.version || found.version || '',
          };
        }));
      })
      .catch((error: unknown) => {
        setCatalog([]);
        setCatalogError(error instanceof ApiError ? error.detail : (error as Error).message);
      })
      .finally(() => setCatalogLoading(false));
  }, [open, template]);

  const toggle = (item: PackageCatalogItem) => {
    const key = itemKey(item);
    setSelected((current) => {
      if (current.some((row) => itemKey(row) === key)) {
        return current.filter((row) => itemKey(row) !== key);
      }
      return [
        ...current,
        {
          kind: item.kind,
          id: item.id,
          displayName: item.display_name || item.id,
          version: item.version || '',
        },
      ];
    });
  };

  const save = async () => {
    const templateName = name.trim();
    if (!templateName) {
      toast('danger', t('expertPackageTemplate.fieldRequired', { field: t('expertPackageTemplate.templateName') }));
      return;
    }
    if (selected.length === 0) {
      toast('danger', t('expertPackageTemplate.pickRequired'));
      return;
    }
    const { max_workflows, max_agents_total } = swarmflow;
    if (max_workflows === null) {
      toast('danger', t('expertPackageTemplate.fieldRequired', {
        field: t('expertPackageTemplate.swarmflowMaxWorkflows'),
      }));
      return;
    }
    if (max_agents_total === null) {
      toast('danger', t('expertPackageTemplate.fieldRequired', {
        field: t('expertPackageTemplate.swarmflowMaxAgentsTotal'),
      }));
      return;
    }
    const swarmflowPayload: SwarmflowConfig = { ...swarmflow, max_workflows, max_agents_total };
    const packages = selected.map((item) => ({
      kind: item.kind,
      asset_id: item.id,
      version: item.version.slice(0, 64),
    }));
    setSaving(true);
    try {
      if (editing && template) {
        await ExpertPackageTemplateApi.update(template.template_id, {
          template_name: templateName,
          description: description.trim(),
          packages,
          swarmflow: swarmflowPayload,
          expected_revision: template.revision ?? 0,
        });
      } else {
        await ExpertPackageTemplateApi.create({
          template_name: templateName,
          description: description.trim(),
          packages,
          swarmflow: swarmflowPayload,
          enabled: true,
        });
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        toast('danger', t('expertPackageTemplate.conflictStale'));
        onConflict();
        return;
      }
      toast('danger', t('errors.saveFailed', {
        detail: error instanceof ApiError ? error.detail : (error as Error).message,
      }));
    } finally {
      setSaving(false);
    }
  };

  const column = (title: string, kind: ExpertPackageKind) => {
    const catalogItems = catalog.filter((item) => item.kind === kind);
    const savedOnly = selected.filter(
      (item) => item.kind === kind && !catalogItems.some((row) => row.id === item.id),
    );
    const empty = !catalogLoading && catalogItems.length === 0 && savedOnly.length === 0;
    return (
      <div className="min-w-0 flex-1">
        <div className="mb-2 text-sm font-medium">{title}</div>
        {catalogLoading ? (
          <div className="text-sm text-muted">{t('common.loading')}</div>
        ) : empty ? (
          <div className="text-sm text-muted">{t('expertPackageTemplate.catalogEmpty')}</div>
        ) : (
          <div className="flex max-h-64 flex-col gap-1 overflow-auto">
            {catalogItems.map((item) => {
              const active = selected.some((row) => itemKey(row) === itemKey(item));
              return (
                <button
                  key={itemKey(item)}
                  type="button"
                  aria-pressed={active}
                  className={`rounded border px-2 py-1.5 text-left text-sm ${active ? 'border-[var(--primary)] bg-[var(--accent-subtle)]' : 'border-border'}`}
                  onClick={() => toggle(item)}
                >
                  <div className="font-medium">{active ? '✓ ' : ''}{item.display_name || item.id}</div>
                  <div className="mono text-[11px] text-muted break-all">{item.id}</div>
                </button>
              );
            })}
            {savedOnly.map((item) => (
              <button
                key={itemKey(item)}
                type="button"
                aria-pressed
                className="rounded border border-[var(--primary)] bg-[var(--accent-subtle)] px-2 py-1.5 text-left text-sm"
                onClick={() => setSelected((current) => current.filter((row) => itemKey(row) !== itemKey(item)))}
              >
                <div className="font-medium">✓ {item.displayName}</div>
                <div className="mono text-[11px] text-muted break-all">{item.id}</div>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <Modal
      open={open}
      title={editing ? t('expertPackageTemplate.edit') : t('expertPackageTemplate.new')}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" disabled={saving} onClick={() => void save()}>
            {t('common.save')}
          </button>
        </>
      }
    >
      <div className="mb-3">
        <FieldLabel required>{t('expertPackageTemplate.templateName')}</FieldLabel>
        <LimitedTextInput value={name} onChange={setName} maxLength={128} />
      </div>
      <div className="mb-4">
        <FieldLabel>{t('expertPackageTemplate.templateDescription')}</FieldLabel>
        <LimitedTextInput value={description} onChange={setDescription} maxLength={512} />
      </div>
      <div className="mb-4">
        <div className="mb-2 text-sm font-medium">{t('expertPackageTemplate.swarmflowTitle')}</div>
        <div className="rounded border border-border p-3">
          <div className="flex items-center justify-between gap-3 py-1.5">
            <div className="min-w-0">
              <div className="text-sm">{t('expertPackageTemplate.swarmflowAllowed')}</div>
              <div className="text-[11px] text-muted">{t('expertPackageTemplate.swarmflowAllowedHint')}</div>
            </div>
            <Switch
              checked={swarmflow.allowed}
              onChange={(value) => setSwarmflow((cur) => ({ ...cur, allowed: value }))}
              aria-label={t('expertPackageTemplate.swarmflowAllowed')}
            />
          </div>
          <div className="flex items-center justify-between gap-3 py-1.5">
            <div className="min-w-0">
              <div className="text-sm">{t('expertPackageTemplate.swarmflowDefaultEnabled')}</div>
              <div className="text-[11px] text-muted">{t('expertPackageTemplate.swarmflowDefaultEnabledHint')}</div>
            </div>
            <Switch
              checked={swarmflow.default_enabled}
              onChange={(value) => setSwarmflow((cur) => ({ ...cur, default_enabled: value }))}
              aria-label={t('expertPackageTemplate.swarmflowDefaultEnabled')}
            />
          </div>
          <div className="grid grid-cols-2 gap-3 pt-2">
            <div>
              <label className="label">{t('expertPackageTemplate.swarmflowMaxWorkflows')}</label>
              <input
                type="number"
                min={1}
                step={1}
                className="input w-full"
                value={swarmflow.max_workflows ?? ''}
                onChange={(e) =>
                  setSwarmflow((cur) => ({ ...cur, max_workflows: parsePositiveInt(e.target.value) }))
                }
              />
            </div>
            <div>
              <label className="label">{t('expertPackageTemplate.swarmflowAgentsPerRun')}</label>
              <input
                type="number"
                min={1}
                step={1}
                className="input w-full"
                placeholder={t('expertPackageTemplate.swarmflowAgentsPerRunHint')}
                value={swarmflow.agents_per_run ?? ''}
                onChange={(e) =>
                  setSwarmflow((cur) => ({ ...cur, agents_per_run: parsePositiveInt(e.target.value) }))
                }
              />
            </div>
            <div>
              <label className="label">{t('expertPackageTemplate.swarmflowMaxAgentsTotal')}</label>
              <input
                type="number"
                min={1}
                step={1}
                className="input w-full"
                value={swarmflow.max_agents_total ?? ''}
                onChange={(e) =>
                  setSwarmflow((cur) => ({ ...cur, max_agents_total: parsePositiveInt(e.target.value) }))
                }
              />
            </div>
            <div>
              <label className="label">{t('expertPackageTemplate.swarmflowBudget')}</label>
              <input
                type="number"
                min={1}
                step={1}
                className="input w-full"
                placeholder={t('expertPackageTemplate.swarmflowBudgetHint')}
                value={swarmflow.swarmflow_budget ?? ''}
                onChange={(e) =>
                  setSwarmflow((cur) => ({ ...cur, swarmflow_budget: parsePositiveInt(e.target.value) }))
                }
              />
            </div>
          </div>
        </div>
      </div>
      <div>
        {catalogError && !catalogLoading && catalog.length === 0 ? (
          <div className="mb-2 text-sm text-muted">{t('expertPackageTemplate.catalogEmpty')}</div>
        ) : null}
        <div className="mb-2 text-sm text-muted">
          {t('expertPackageTemplate.selectedCount', { count: selected.length })}
        </div>
        <div className="flex gap-4">
          {column(t('expertPackageTemplate.kind.agent_template'), 'agent_template')}
          {column(t('expertPackageTemplate.kind.agent_group'), 'agent_group')}
        </div>
      </div>
    </Modal>
  );
}
