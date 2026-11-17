import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { ConfigRefreshCostHint } from '../../components/ConfigRefreshCostHint';
import { ApiError, A2AOutboundTemplateApi } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type { A2ADiscoveryCandidate, A2AOutboundTemplate } from '../../types';

interface Props {
  open: boolean;
  template: A2AOutboundTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

export function A2AOutboundTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const [url, setUrl] = useState('');
  const [candidate, setCandidate] = useState<A2ADiscoveryCandidate | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [credential, setCredential] = useState('');
  const [initialCredential, setInitialCredential] = useState('');
  const [clearCredential, setClearCredential] = useState(false);
  const [credentialLoaded, setCredentialLoaded] = useState(true);
  const [connectTimeout, setConnectTimeout] = useState(10);
  const [syncWait, setSyncWait] = useState(120);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCandidate(null);
    setUrl(template?.source_url ?? '');
    setName(template?.template_name ?? '');
    setDescription(template?.description ?? '');
    setCredential('');
    setInitialCredential('');
    setClearCredential(false);
    setCredentialLoaded(!template);
    setConnectTimeout(template?.connect_timeout_seconds ?? 10);
    setSyncWait(template?.sync_wait_seconds ?? 120);
    if (template) {
      void A2AOutboundTemplateApi.getForEdit(template.template_id)
        .then((item) => {
          const value = item.credential ?? '';
          setCredential(value);
          setInitialCredential(value);
        })
        .catch((e) => toast('danger', e instanceof ApiError ? e.detail : (e as Error).message))
        .finally(() => setCredentialLoaded(true));
    }
  }, [open, template]);

  const discover = async () => {
    if (!url.trim()) return;
    setSaving(true);
    try {
      const item = await A2AOutboundTemplateApi.discover({ url: url.trim() });
      setCandidate(item);
      setName(String(item.agent_card.name || ''));
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : (e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const save = async () => {
    if (!name.trim() || (!template && !candidate)) return;
    setSaving(true);
    try {
      if (template) {
        const credentialUpdate = clearCredential
          ? { clear_credential: true }
          : credential !== initialCredential
            ? { credential }
            : {};
        await A2AOutboundTemplateApi.update(template.template_id, {
          template_name: name.trim(), description: description.trim() || null,
          ...credentialUpdate, connect_timeout_seconds: connectTimeout,
          sync_wait_seconds: syncWait,
        });
      } else if (candidate) {
        await A2AOutboundTemplateApi.create({
          discovery_id: candidate.discovery_id, template_name: name.trim(),
          description: description.trim() || null, credential,
          connect_timeout_seconds: connectTimeout, sync_wait_seconds: syncWait, enabled: true,
        });
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : (e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} title={template ? t('a2a.editAgentTitle') : t('a2a.discoverAgentTitle')} onClose={onClose} size="lg"
      footer={<><ModalCancelButton /><button className="btn primary" disabled={saving || !credentialLoaded || (!template && !candidate)} onClick={() => void save()}>{t('common.save')}</button></>}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {!template && <div className="md:col-span-2"><label className="label">{t('a2a.urlLabel')}</label><div className="flex gap-2"><input className="input flex-1" value={url} onChange={(e) => { setUrl(e.target.value); setCandidate(null); }} /><button className="btn" disabled={saving} onClick={() => void discover()}>{t('a2a.discover')}</button></div></div>}
        {candidate && <div className="md:col-span-2 card text-xs"><div className="font-medium">{t('a2a.discoveredName', { name: String(candidate.agent_card.name || '') })}</div><div className="mono text-muted break-all">{candidate.card_url}</div><div className="text-muted">JSON-RPC · {String(candidate.selected_interface.protocol_version || t('a2a.unknownVersion'))}</div></div>}
        <div className="md:col-span-2"><label className="label">{t('a2a.displayName')}</label><input className="input" maxLength={128} value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div className="md:col-span-2"><label className="label">{t('a2a.description')}</label><input className="input" maxLength={512} value={description} onChange={(e) => setDescription(e.target.value)} /></div>
        <div className="md:col-span-2"><label className="label">{t('a2a.credential')}</label><input className="input" type="password" maxLength={4096} disabled={!credentialLoaded || clearCredential} value={credential} onChange={(e) => setCredential(e.target.value)} />{template?.credential_configured && <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" disabled={!credentialLoaded} checked={clearCredential} onChange={(e) => setClearCredential(e.target.checked)} />{t('a2a.clearSavedCredential')}</label>}</div>
        <div><label className="label">{t('a2a.connectTimeout')}</label><input className="input" type="number" min={0.1} value={connectTimeout} onChange={(e) => setConnectTimeout(Number(e.target.value))} /></div>
        <div><label className="label">{t('a2a.syncWait')}</label><input className="input" type="number" min={0.1} value={syncWait} onChange={(e) => setSyncWait(Number(e.target.value))} /></div>
      </div>
      <ConfigRefreshCostHint scope="template" />
    </Modal>
  );
}
