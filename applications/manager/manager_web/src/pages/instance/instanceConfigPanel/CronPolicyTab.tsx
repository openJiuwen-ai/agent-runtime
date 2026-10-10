import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, CronPolicyApi } from '../../../services/api';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { toast } from '../../../stores/uiStore';
import { formatTime } from '../../../utils/format';

const DEFAULT_LIMIT = '5';

interface Props {
  instanceId: string;
}

function isNonNegativeInteger(value: string): boolean {
  return /^\d+$/.test(value.trim());
}

export function CronPolicyTab({ instanceId }: Props) {
  const { t } = useTranslation();
  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hasRemoteConfig, setHasRemoteConfig] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null | undefined>();
  const [saving, setSaving] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await CronPolicyApi.get(instanceId);
      setLimit(String(data.max_jobs_per_user));
      setHasRemoteConfig(true);
      setUpdatedAt(data.updated_at);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setLimit(DEFAULT_LIMIT);
        setHasRemoteConfig(false);
        setUpdatedAt(undefined);
      } else {
        setLoadError(e instanceof ApiError ? e.detail : (e as Error).message);
      }
    } finally {
      setLoading(false);
    }
  }, [instanceId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const save = async () => {
    if (!isNonNegativeInteger(limit)) {
      toast('danger', t('instanceConfig.cronPolicy.invalid'));
      return;
    }
    setSaving(true);
    try {
      const data = await CronPolicyApi.upsert(instanceId, {
        max_jobs_per_user: Number(limit.trim()),
      });
      setLimit(String(data.max_jobs_per_user));
      setHasRemoteConfig(true);
      setUpdatedAt(data.updated_at);
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
      await CronPolicyApi.remove(instanceId);
      setLimit(DEFAULT_LIMIT);
      setHasRemoteConfig(false);
      setUpdatedAt(undefined);
      toast('success', t('instanceConfig.cronPolicy.deleted'));
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

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        {hasRemoteConfig && (
          <>
            <span className="pill sm ok">
              <span className="statusDot ok" />
              {t('instanceConfig.cronPolicy.managed')}
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
            {t('instanceConfig.cronPolicy.resetToYaml')}
          </button>
        )}
        <button className="btn primary sm" onClick={() => void save()} disabled={saving}>
          {saving ? t('common.loading') : t('common.save')}
        </button>
      </div>

      <p className="text-[12px] text-muted leading-relaxed">{t('instanceConfig.cronPolicy.intro')}</p>

      <div className="card">
        <label className="label">{t('instanceConfig.cronPolicy.maxJobs')}</label>
        <p className="text-[11px] text-muted mb-1">{t('instanceConfig.cronPolicy.maxJobsHint')}</p>
        <input
          type="number"
          min={0}
          step={1}
          className="input w-full max-w-xs"
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
        />
      </div>

      <ConfirmDialog
        open={deleteOpen}
        message={t('instanceConfig.cronPolicy.deleteConfirm')}
        danger
        onConfirm={removeConfig}
        onClose={() => setDeleteOpen(false)}
      />
    </div>
  );
}
