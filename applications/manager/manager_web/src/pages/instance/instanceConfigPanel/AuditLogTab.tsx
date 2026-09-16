import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, AuditLogApi } from '../../../services/api';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { Switch } from '../../../components/Switch';
import { toast } from '../../../stores/uiStore';
import { formatTime } from '../../../utils/format';
import type { AuditOtelProtocol } from '../../../types';
import {
  CONTENT_FIELD_CANDIDATES,
  DEFAULT_FORM,
  HEADER_FIELD_CANDIDATES,
  TIMESTAMP_FORMAT_FIXED,
  clientValidate,
  ensureEnabledForRequired,
  headersToText,
  mapFromGet,
  parseHeadersText,
  restoreDefaultFields,
  toUpsertBody,
  type AuditLogFormState,
} from './auditLogDefaults';

interface Props {
  instanceId: string;
}

function toggleInList(list: string[], field: string, checked: boolean): string[] {
  if (checked) {
    return list.includes(field) ? list : [...list, field];
  }
  return list.filter((x) => x !== field);
}

export function AuditLogTab({ instanceId }: Props) {
  const { t } = useTranslation();
  const [form, setForm] = useState<AuditLogFormState>(DEFAULT_FORM);
  const [headersText, setHeadersText] = useState(headersToText(DEFAULT_FORM.otel.headers));
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hasRemoteConfig, setHasRemoteConfig] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null | undefined>();
  const [saving, setSaving] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const applyForm = useCallback((next: AuditLogFormState) => {
    setForm(next);
    setHeadersText(headersToText(next.otel.headers));
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await AuditLogApi.get(instanceId);
      applyForm(mapFromGet(data));
      setHasRemoteConfig(true);
      setUpdatedAt(data.updated_at);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        applyForm(DEFAULT_FORM);
        setHasRemoteConfig(false);
        setUpdatedAt(undefined);
      } else {
        setLoadError(e instanceof ApiError ? e.detail : (e as Error).message);
      }
    } finally {
      setLoading(false);
    }
  }, [applyForm, instanceId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const toggleHeader = (field: string, checked: boolean) => {
    if (!checked && form.format.required_fields.includes(field)) {
      toast('danger', t('instanceConfig.auditLog.cannotUncheckRequired'));
      return;
    }
    setForm((prev) => ({
      ...prev,
      format: {
        ...prev.format,
        header_fields: toggleInList(prev.format.header_fields, field, checked),
      },
    }));
  };

  const toggleContent = (field: string, checked: boolean) => {
    if (!checked && form.format.required_fields.includes(field)) {
      toast('danger', t('instanceConfig.auditLog.cannotUncheckRequired'));
      return;
    }
    setForm((prev) => ({
      ...prev,
      format: {
        ...prev.format,
        content_fields: toggleInList(prev.format.content_fields, field, checked),
      },
    }));
  };

  const toggleRequired = (field: string, checked: boolean) => {
    setForm((prev) => {
      let next = {
        ...prev,
        format: {
          ...prev.format,
          required_fields: toggleInList(prev.format.required_fields, field, checked),
        },
      };
      if (checked) {
        next = ensureEnabledForRequired(next, field);
      }
      return next;
    });
  };

  const save = async () => {
    const errKey = clientValidate(form, headersText);
    if (errKey) {
      toast('danger', t(`instanceConfig.auditLog.validation.${errKey}`));
      return;
    }
    setSaving(true);
    try {
      const headers = parseHeadersText(headersText);
      const body = toUpsertBody({
        ...form,
        otel: { ...form.otel, headers },
        format: { ...form.format, timestamp_format: TIMESTAMP_FORMAT_FIXED },
      });
      const data = await AuditLogApi.upsert(instanceId, body);
      setHasRemoteConfig(true);
      setUpdatedAt(data.updated_at);
      applyForm(mapFromGet(data));
      toast('success', t('success.saved'));
    } catch (e) {
      toast(
        'danger',
        t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message })
      );
    } finally {
      setSaving(false);
    }
  };

  const removeConfig = async () => {
    try {
      await AuditLogApi.remove(instanceId);
      applyForm(DEFAULT_FORM);
      setHasRemoteConfig(false);
      setUpdatedAt(undefined);
      toast('success', t('instanceConfig.auditLog.deleted'));
    } catch (e) {
      toast(
        'danger',
        t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message })
      );
    }
  };

  if (loading) {
    return <div className="p-4 text-sm text-muted">{t('common.loading')}</div>;
  }

  if (loadError) {
    return (
      <div className="p-4 text-sm text-danger">
        {t('errors.loadFailed', { detail: loadError })}
        <button className="btn sm ml-2" onClick={() => void reload()}>
          {t('common.refresh')}
        </button>
      </div>
    );
  }

  const requiredCandidates = [
    ...new Set([...HEADER_FIELD_CANDIDATES, ...CONTENT_FIELD_CANDIDATES]),
  ];

  return (
    <div className="flex flex-col gap-3">
      <p className="text-[12px] text-muted">{t('instanceConfig.auditLog.intro')}</p>

      <div className="flex items-center gap-2 flex-wrap">
        {hasRemoteConfig && (
          <>
            <span className="pill sm ok">
              <span className="statusDot ok" />
              {t('instanceConfig.auditLog.managed')}
            </span>
            {updatedAt && (
              <span className="text-[11px] text-muted mono">{formatTime(updatedAt)}</span>
            )}
          </>
        )}
        <button className="btn sm" onClick={() => void reload()}>
          {t('common.refresh')}
        </button>
        {hasRemoteConfig && (
          <button className="btn sm danger" onClick={() => setDeleteOpen(true)}>
            {t('instanceConfig.auditLog.resetToDefault')}
          </button>
        )}
        <button
          className="btn sm"
          onClick={() => setForm((prev) => restoreDefaultFields(prev))}
        >
          {t('instanceConfig.auditLog.restoreDefaultFields')}
        </button>
        <button className="btn primary sm" onClick={() => void save()} disabled={saving}>
          {saving ? t('common.loading') : t('common.save')}
        </button>
      </div>

      <div className="card flex flex-col gap-3">
        <div className="text-sm font-medium">{t('instanceConfig.auditLog.sections.identity')}</div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="label">{t('instanceConfig.auditLog.dataCenter')}</label>
            <input
              className="input w-full"
              maxLength={32}
              value={form.data_center}
              onChange={(e) => setForm((prev) => ({ ...prev, data_center: e.target.value }))}
            />
          </div>
          <div>
            <label className="label">{t('instanceConfig.auditLog.systemCode')}</label>
            <input
              className="input w-full"
              maxLength={64}
              value={form.system_code}
              onChange={(e) => setForm((prev) => ({ ...prev, system_code: e.target.value }))}
            />
          </div>
          <div>
            <label className="label">{t('instanceConfig.auditLog.node')}</label>
            <input
              className="input w-full"
              maxLength={128}
              value={form.node}
              onChange={(e) => setForm((prev) => ({ ...prev, node: e.target.value }))}
            />
          </div>
        </div>
        <p className="text-[11px] text-muted">{t('instanceConfig.auditLog.serviceHint')}</p>
      </div>

      <div className="card flex flex-col gap-3">
        <div className="text-sm font-medium">{t('instanceConfig.auditLog.sections.otel')}</div>
        <div className="flex items-center gap-2">
          <Switch
            checked={form.otel.enabled}
            onChange={(checked) =>
              setForm((prev) => ({ ...prev, otel: { ...prev.otel, enabled: checked } }))
            }
            aria-label={t('instanceConfig.auditLog.otelEnabled')}
          />
          <span className="text-sm">{t('instanceConfig.auditLog.otelEnabled')}</span>
        </div>
        <p className="text-[11px] text-muted">{t('instanceConfig.auditLog.otelEnabledHint')}</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="label">{t('instanceConfig.auditLog.otelEndpoint')}</label>
            <input
              className="input w-full"
              value={form.otel.endpoint}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  otel: { ...prev.otel, endpoint: e.target.value },
                }))
              }
            />
          </div>
          <div>
            <label className="label">{t('instanceConfig.auditLog.otelProtocol')}</label>
            <select
              className="select w-full"
              value={form.otel.protocol}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  otel: { ...prev.otel, protocol: e.target.value as AuditOtelProtocol },
                }))
              }
            >
              <option value="grpc">grpc</option>
              <option value="http">http</option>
            </select>
          </div>
        </div>
        <div>
          <label className="label">{t('instanceConfig.auditLog.otelHeaders')}</label>
          <textarea
            className="input w-full min-h-[5rem] font-mono text-[12px]"
            value={headersText}
            onChange={(e) => setHeadersText(e.target.value)}
          />
        </div>
      </div>

      <div className="card flex flex-col gap-3">
        <div className="text-sm font-medium">{t('instanceConfig.auditLog.sections.format')}</div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="label">{t('instanceConfig.auditLog.schemaVersion')}</label>
            <input
              className="input w-full"
              value={form.format.schema_version}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  format: { ...prev.format, schema_version: e.target.value },
                }))
              }
            />
          </div>
          <div>
            <label className="label">{t('instanceConfig.auditLog.placeholder')}</label>
            <input
              className="input w-full"
              value={form.format.placeholder}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  format: { ...prev.format, placeholder: e.target.value },
                }))
              }
            />
          </div>
          <div>
            <label className="label">{t('instanceConfig.auditLog.timestampFormat')}</label>
            <input className="input w-full" value={TIMESTAMP_FORMAT_FIXED} disabled readOnly />
            <p className="text-[11px] text-muted mt-1">
              {t('instanceConfig.auditLog.timestampFormatHint')}
            </p>
          </div>
        </div>

        <FieldCheckboxGroup
          title={t('instanceConfig.auditLog.headerFields')}
          candidates={HEADER_FIELD_CANDIDATES}
          selected={form.format.header_fields}
          requiredFields={form.format.required_fields}
          onToggle={toggleHeader}
        />
        <FieldCheckboxGroup
          title={t('instanceConfig.auditLog.contentFields')}
          candidates={CONTENT_FIELD_CANDIDATES}
          selected={form.format.content_fields}
          requiredFields={form.format.required_fields}
          onToggle={toggleContent}
        />
        <div>
          <div className="text-sm mb-1">{t('instanceConfig.auditLog.requiredFields')}</div>
          <p className="text-[11px] text-muted mb-2">
            {t('instanceConfig.auditLog.requiredSubsetHint')}
          </p>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
            {requiredCandidates.map((field) => (
              <label key={field} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.format.required_fields.includes(field)}
                  onChange={(e) => toggleRequired(field, e.target.checked)}
                />
                <span className="mono text-[12px]">{field}</span>
              </label>
            ))}
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={deleteOpen}
        message={t('instanceConfig.auditLog.deleteConfirm')}
        danger
        onConfirm={removeConfig}
        onClose={() => setDeleteOpen(false)}
      />
    </div>
  );
}

function FieldCheckboxGroup({
  title,
  candidates,
  selected,
  requiredFields,
  onToggle,
}: {
  title: string;
  candidates: string[];
  selected: string[];
  requiredFields: string[];
  onToggle: (field: string, checked: boolean) => void;
}) {
  const selectedSet = new Set(selected);
  const requiredSet = new Set(requiredFields);
  return (
    <div>
      <div className="text-sm mb-2">{title}</div>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
        {candidates.map((field) => {
          const isRequired = requiredSet.has(field);
          return (
            <label
              key={field}
              className={`flex items-center gap-2 text-sm ${isRequired ? 'opacity-90' : ''}`}
              title={isRequired ? 'required' : undefined}
            >
              <input
                type="checkbox"
                checked={selectedSet.has(field)}
                onChange={(e) => onToggle(field, e.target.checked)}
              />
              <span className="mono text-[12px]">
                {field}
                {isRequired ? ' *' : ''}
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
