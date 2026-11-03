import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { ExpertPackageTemplateApi, PackageCatalogApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type { ExpertPackageKind, ExpertPackageTemplate, PackageCatalogItem } from '../../types';

interface Props {
  open: boolean;
  template: ExpertPackageTemplate | null;
  onClose: () => void;
  onSaved: () => void;
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

export function ExpertPackageTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const editing = template !== null;
  const [catalog, setCatalog] = useState<PackageCatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [selected, setSelected] = useState<Selection[]>([]);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (template) {
      setName(template.template_name);
      setDescription(template.description || '');
      setSelected((template.packages ?? []).map((item) => ({
        kind: item.kind,
        id: item.asset_id,
        displayName: item.asset_id,
        version: item.version || '',
      })));
    } else {
      setName('');
      setDescription('');
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
        });
      } else {
        await ExpertPackageTemplateApi.create({
          template_name: templateName,
          description: description.trim(),
          packages,
          enabled: true,
        });
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (error) {
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
      <div>
        <FieldLabel required>{t('expertPackageTemplate.hubPackage')}</FieldLabel>
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
