import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { Switch } from '../../components/Switch';
import { A2AOutboundTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';

interface Props {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

export function A2ADiscoverySettingsModal({ open, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const [allowHttp, setAllowHttp] = useState(false);
  const [allowPrivateNetwork, setAllowPrivateNetwork] = useState(false);
  const [allowPublicHttp, setAllowPublicHttp] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    void A2AOutboundTemplateApi.getDiscoverySettings()
      .then((settings) => {
        if (cancelled) return;
        setAllowHttp(settings.allow_http);
        setAllowPrivateNetwork(settings.allow_private_network);
        setAllowPublicHttp(settings.allow_public_http);
      })
      .catch((error) => {
        if (cancelled) return;
        toast('danger', error instanceof ApiError ? error.detail : (error as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [open]);

  const save = async () => {
    setSaving(true);
    try {
      await A2AOutboundTemplateApi.updateDiscoverySettings({
        allow_http: allowHttp,
        allow_private_network: allowPrivateNetwork,
        allow_public_http: allowPublicHttp,
      });
      toast('success', t('a2a.networkSettingsSaved'));
      onSaved();
    } catch (error) {
      toast('danger', error instanceof ApiError ? error.detail : (error as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t('a2a.networkSettings')}
      onClose={onClose}
      footer={<><ModalCancelButton /><button className="btn primary" disabled={loading || saving} onClick={() => void save()}>{t('common.save')}</button></>}
    >
      <div className="flex flex-col gap-4">
        <p className="text-xs text-muted">{t('a2a.networkSettingsHint')}</p>
        <div className="flex items-start justify-between gap-4">
          <div><div className="font-medium">{t('a2a.allowHttp')}</div><div className="mt-1 text-xs text-muted">{t('a2a.allowHttpHint')}</div></div>
          <Switch checked={allowHttp} disabled={loading} aria-label={t('a2a.allowHttp')} onChange={setAllowHttp} />
        </div>
        <div className="flex items-start justify-between gap-4">
          <div><div className="font-medium">{t('a2a.allowPrivateNetwork')}</div><div className="mt-1 text-xs text-muted">{t('a2a.allowPrivateNetworkHint')}</div></div>
          <Switch checked={allowPrivateNetwork} disabled={loading} aria-label={t('a2a.allowPrivateNetwork')} onChange={setAllowPrivateNetwork} />
        </div>
        <div className="flex items-start justify-between gap-4">
          <div><div className="font-medium">{t('a2a.allowPublicHttp')}</div><div className="mt-1 text-xs text-muted">{t('a2a.allowPublicHttpHint')}</div></div>
          <Switch checked={allowPublicHttp} disabled={loading} aria-label={t('a2a.allowPublicHttp')} onChange={setAllowPublicHttp} />
        </div>
        <div className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">{t('a2a.httpWarning')}</div>
      </div>
    </Modal>
  );
}
