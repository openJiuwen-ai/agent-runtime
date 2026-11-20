import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, ContentMaskingApi } from '../../../services/api';
import { ConfigRefreshCostHint } from '../../../components/ConfigRefreshCostHint';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { LimitedTextInput } from '../../../components/LimitedTextInput';
import { Switch } from '../../../components/Switch';
import { toast } from '../../../stores/uiStore';
import { formatTime } from '../../../utils/format';
import type { ContentMaskingRule } from '../../../types';

const MAX_RULES = 32;
const TITLE_MAX = 64;
const PATTERN_MAX = 256;

interface Props {
  instanceId: string;
}

export function ContentMaskingTab({ instanceId }: Props) {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(false);
  const [rules, setRules] = useState<ContentMaskingRule[]>([]);
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
      const data = await ContentMaskingApi.get(instanceId);
      setEnabled(Boolean(data.enabled));
      setRules(Array.isArray(data.rules) ? data.rules : []);
      setHasRemoteConfig(true);
      setUpdatedAt(data.updated_at);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setEnabled(false);
        setRules([]);
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

  const updateRule = (index: number, key: keyof ContentMaskingRule, value: string) => {
    setRules((list) => list.map((item, i) => (i === index ? { ...item, [key]: value } : item)));
  };

  const addRule = () => {
    if (rules.length >= MAX_RULES) {
      toast('warn', t('instanceConfig.contentMasking.maxRules', { max: MAX_RULES }));
      return;
    }
    setRules((list) => [...list, { title: '', pattern: '' }]);
  };

  const removeRule = (index: number) => {
    setRules((list) => list.filter((_, i) => i !== index));
  };

  const save = async () => {
    for (let i = 0; i < rules.length; i += 1) {
      const rule = rules[i];
      if (!rule.title.trim() || !rule.pattern.trim()) {
        toast('warn', t('instanceConfig.contentMasking.ruleRequired'));
        return;
      }
      try {
        // 前端先做一次语法校验，最终严格校验仍以 Gateway 为准
        // eslint-disable-next-line no-new
        new RegExp(rule.pattern);
      } catch (e) {
        toast(
          'danger',
          t('instanceConfig.contentMasking.patternInvalid', { detail: (e as Error).message })
        );
        return;
      }
    }

    setSaving(true);
    try {
      const data = await ContentMaskingApi.upsert(instanceId, {
        enabled,
        rules: rules.map((rule) => ({
          title: rule.title.trim(),
          pattern: rule.pattern.trim(),
        })),
      });
      setEnabled(Boolean(data.enabled));
      setRules(Array.isArray(data.rules) ? data.rules : []);
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
      await ContentMaskingApi.remove(instanceId);
      setEnabled(false);
      setRules([]);
      setHasRemoteConfig(false);
      setUpdatedAt(undefined);
      toast('success', t('instanceConfig.contentMasking.deleted'));
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
              {t('instanceConfig.contentMasking.managed')}
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
            {t('instanceConfig.contentMasking.resetToYaml')}
          </button>
        )}
        <button className="btn primary sm" onClick={() => void save()} disabled={saving}>
          {saving ? t('common.loading') : t('common.save')}
        </button>
      </div>

      <ConfigRefreshCostHint />

      <div className="card flex flex-col gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Switch
              checked={enabled}
              onChange={setEnabled}
              aria-label={t('instanceConfig.contentMasking.globalSwitch')}
            />
            <span className="text-sm font-medium">
              {t('instanceConfig.contentMasking.globalSwitch')}
            </span>
          </div>
          <p className="text-[11px] text-muted mt-1">
            {t('instanceConfig.contentMasking.globalSwitchHint')}
          </p>
        </div>

        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="text-sm font-medium">
              {t('instanceConfig.contentMasking.rules')}
            </div>
            <p className="text-[11px] text-muted mt-1">
              {t('instanceConfig.contentMasking.rulesHint', { max: MAX_RULES })}
            </p>
          </div>
          <button className="btn sm" onClick={addRule}>
            + {t('instanceConfig.contentMasking.addRule')}
          </button>
        </div>

        {rules.length === 0 ? (
          <div className="text-sm text-muted">{t('instanceConfig.contentMasking.emptyRules')}</div>
        ) : (
          <div className="flex flex-col gap-3">
            {rules.map((rule, index) => (
              <div
                // eslint-disable-next-line react/no-array-index-key
                key={index}
                className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] gap-2 items-end"
              >
                <div className="min-w-0">
                  <label className="label">
                    {t('instanceConfig.contentMasking.ruleTitle')}
                  </label>
                  <LimitedTextInput
                    value={rule.title}
                    maxLength={TITLE_MAX}
                    onChange={(v) => updateRule(index, 'title', v)}
                  />
                </div>
                <div className="min-w-0">
                  <label className="label">
                    {t('instanceConfig.contentMasking.rulePattern')}
                  </label>
                  <LimitedTextInput
                    className="mono text-xs"
                    value={rule.pattern}
                    maxLength={PATTERN_MAX}
                    placeholder={t('instanceConfig.contentMasking.rulePatternPlaceholder')}
                    onChange={(v) => updateRule(index, 'pattern', v)}
                  />
                </div>
                <button className="btn sm danger" onClick={() => removeRule(index)}>
                  {t('common.delete')}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={deleteOpen}
        message={t('instanceConfig.contentMasking.deleteConfirm')}
        danger
        costHint
        onConfirm={removeConfig}
        onClose={() => setDeleteOpen(false)}
      />
    </div>
  );
}
