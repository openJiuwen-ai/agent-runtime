import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useFormDirty } from '../../hooks/useFormDirty';
import { useRouter } from '../../router';
import { InstanceApi, ApiError } from '../../services/api';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { safeStringify } from '../../utils/format';
import { toast } from '../../stores/uiStore';
import { InstanceConfigPanel } from './instanceConfigPanel/InstanceConfigPanel';
import { InstanceDetailPanel } from './instanceDetailPanel/instanceDetailPanel';
import { InstanceAccessPanel } from './instanceAccessPanel/InstanceAccessPanel';
import { InstanceResourcePanel } from './instanceResourcePanel/InstanceResourcePanel';
import { InstancePlaceholderPanel } from './InstancePlaceholderPanel';

export type InstancePageTab =
  | 'access'
  | 'resources'
  | 'config'
  | 'status'
  | 'tokenQuota'
  | 'cost'
  | 'audit';

interface Props {
  instanceId: string;
  tab?: InstancePageTab;
}

export function InstanceDetailPage({ instanceId, tab = 'access' }: Props) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const instance = useAsync(() => InstanceApi.get(instanceId), [instanceId]);

  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState('');
  const checkJson = useInvalidJsonChecker();
  const { markClean, isDirty } = useFormDirty(editOpen);

  const mainTabs: { key: InstancePageTab; label: string; href: string }[] = [
    { key: 'access', label: t('instanceDetail.tabs.access'), href: `/instances/${instanceId}/access` },
    { key: 'resources', label: t('instanceDetail.tabs.resources'), href: `/instances/${instanceId}/resources` },
    { key: 'config', label: t('instanceDetail.tabs.config'), href: `/instances/${instanceId}/config` },
    { key: 'status', label: t('instanceDetail.tabs.status'), href: `/instances/${instanceId}/status` },
    { key: 'tokenQuota', label: t('instanceDetail.tabs.tokenQuota'), href: `/instances/${instanceId}/token-quota` },
    { key: 'cost', label: t('instanceDetail.tabs.cost'), href: `/instances/${instanceId}/cost` },
    { key: 'audit', label: t('instanceDetail.tabs.audit'), href: `/instances/${instanceId}/audit` },
  ];

  const handleOpenEdit = () => {
    const next = safeStringify(instance.data?.data ?? {}, 2);
    setEditText(next);
    markClean(next);
    setEditOpen(true);
  };

  const submitData = async () => {
    const err = checkJson(editText);
    if (err) {
      toast('danger', err);
      return;
    }
    try {
      await InstanceApi.update(instanceId, { data: tryParseJson(editText, {}) });
      toast('success', t('success.saved'));
      setEditOpen(false);
      void instance.reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4 overflow-x-auto">
        <div className="page-header flex w-full min-w-0 shrink-0 flex-col items-stretch gap-3 lg:grid lg:grid-cols-[1fr_auto_1fr] lg:items-center lg:gap-4">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 lg:justify-self-start">
            <button
              type="button"
              className="btn ghost sm shrink-0"
              onClick={() => navigate('/instances')}
              aria-label={t('topology.title')}
              title={t('topology.title')}
            >
              ←
            </button>
            <div className="min-w-0 max-w-full flex-1 sm:max-w-[16rem] sm:flex-none">
              <div
                className="page-title truncate"
                title={instance.data?.jiuwenclaw_name ?? undefined}
              >
                {instance.data?.jiuwenclaw_name ?? '…'}
              </div>
              <div className="text-[11px] text-muted mono truncate" title={instanceId}>
                {instanceId}
              </div>
            </div>
          </div>

          <div className="tabs-bar max-w-full shrink-0 self-center overflow-x-auto lg:justify-self-center">
            {mainTabs.map((it) => (
              <button
                key={it.key}
                onClick={() => navigate(it.href)}
                className={`tab ${tab === it.key ? 'active' : ''}`}
              >
                {it.label}
              </button>
            ))}
          </div>

          <div aria-hidden="true" className="hidden min-w-0 lg:block" />
        </div>

        <div className="w-full min-w-0 shrink-0">
          {tab === 'access' && <InstanceAccessPanel instanceId={instanceId} />}
          {tab === 'resources' && <InstanceResourcePanel instanceId={instanceId} />}
          {tab === 'config' && <InstanceConfigPanel instanceId={instanceId} />}
          {tab === 'status' && (
            <InstanceDetailPanel
              instance={instance}
              onOpenEdit={handleOpenEdit}
              onRefresh={() => void instance.reload()}
            />
          )}
          {tab === 'tokenQuota' && (
            <InstancePlaceholderPanel
              titleKey="instanceDetail.tokenQuota.title"
              subtitleKey="instanceDetail.tokenQuota.subtitle"
            />
          )}
          {tab === 'cost' && (
            <InstancePlaceholderPanel
              titleKey="instanceDetail.cost.title"
              subtitleKey="instanceDetail.cost.subtitle"
            />
          )}
          {tab === 'audit' && (
            <InstancePlaceholderPanel
              titleKey="instanceDetail.audit.title"
              subtitleKey="instanceDetail.audit.subtitle"
            />
          )}
        </div>
      </div>

      <Modal
        open={editOpen}
        title={t('instanceDetail.editData')}
        onClose={() => setEditOpen(false)}
        dirty={isDirty(editText)}
        size="lg"
        footer={
          <>
            <ModalCancelButton />
            <button className="btn primary" onClick={submitData}>
              {t('common.save')}
            </button>
          </>
        }
      >
        <JsonField label="instance_info.data" value={editText} onChange={setEditText} rows={14} />
      </Modal>
    </>
  );
}
