import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, UserPreferenceDefaultApi } from '../../../services/api';
import type { PreferredLanguage } from '../../../types/applicationConfig';
import { toast } from '../../../stores/uiStore';
import { formatTime } from '../../../utils/format';

interface Props {
  instanceId: string;
}

const OPTIONS: PreferredLanguage[] = ['zh', 'en'];

export function AdvancedConfigTab({ instanceId }: Props) {
  const { t } = useTranslation();
  const [language, setLanguage] = useState<PreferredLanguage>('zh');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hasRemoteConfig, setHasRemoteConfig] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null | undefined>();
  const [saving, setSaving] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await UserPreferenceDefaultApi.get(instanceId);
      setLanguage(data.preferred_language);
      setHasRemoteConfig(true);
      setUpdatedAt(data.updated_at);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setLanguage('zh');
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
    setSaving(true);
    try {
      const data = await UserPreferenceDefaultApi.upsert(instanceId, {
        preferred_language: language,
      });
      setLanguage(data.preferred_language);
      setHasRemoteConfig(true);
      setUpdatedAt(data.updated_at);
      toast('success', t('success.saved'));
    } catch (e) {
      toast(
        'danger',
        t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }),
      );
    } finally {
      setSaving(false);
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
              {t('instanceConfig.advanced.managed')}
            </span>
            {updatedAt && (
              <span className="text-[11px] text-muted mono">{formatTime(updatedAt)}</span>
            )}
          </>
        )}
        <button className="btn sm" onClick={() => void reload()}>
          {t('common.refresh')}
        </button>
        <button className="btn primary sm" onClick={() => void save()} disabled={saving}>
          {saving ? t('common.loading') : t('common.save')}
        </button>
      </div>

      <p className="text-[12px] text-muted leading-relaxed">{t('instanceConfig.advanced.intro')}</p>

      <div className="card">
        <label className="label">{t('instanceConfig.advanced.preferredLanguage')}</label>
        <p className="text-[11px] text-muted mb-1">
          {t('instanceConfig.advanced.preferredLanguageHint')}
        </p>
        <select
          className="input w-full max-w-xs"
          value={language}
          onChange={(e) => setLanguage(e.target.value as PreferredLanguage)}
        >
          {OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`instanceConfig.advanced.lang.${opt}`)}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
