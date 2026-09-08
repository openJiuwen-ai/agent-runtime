import { useEffect, useState } from 'react';
import { Modal, ModalCancelButton } from '../../components/Modal';
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
  const [url, setUrl] = useState('');
  const [candidate, setCandidate] = useState<A2ADiscoveryCandidate | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [tags, setTags] = useState('');
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
    setTags(template?.a2a_tags?.join(', ') ?? '');
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
    const a2a_tags = tags.split(',').map((x) => x.trim()).filter(Boolean);
    try {
      if (template) {
        const credentialUpdate = clearCredential
          ? { clear_credential: true }
          : credential !== initialCredential
            ? { credential }
            : {};
        await A2AOutboundTemplateApi.update(template.template_id, {
          template_name: name.trim(), description: description.trim() || undefined,
          a2a_tags, ...credentialUpdate, connect_timeout_seconds: connectTimeout,
          sync_wait_seconds: syncWait,
        });
      } else if (candidate) {
        await A2AOutboundTemplateApi.create({
          discovery_id: candidate.discovery_id, template_name: name.trim(),
          description: description.trim() || undefined, a2a_tags, credential,
          connect_timeout_seconds: connectTimeout, sync_wait_seconds: syncWait, enabled: true,
        });
      }
      toast('success', '保存成功');
      onSaved();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : (e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} title={template ? '编辑 A2A Agent' : '发现并注册 A2A Agent'} onClose={onClose} size="lg"
      footer={<><ModalCancelButton /><button className="btn primary" disabled={saving || !credentialLoaded || (!template && !candidate)} onClick={() => void save()}>保存</button></>}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {!template && <div className="md:col-span-2"><label className="label">Agent Base URL 或 Card URL</label><div className="flex gap-2"><input className="input flex-1" value={url} onChange={(e) => { setUrl(e.target.value); setCandidate(null); }} /><button className="btn" disabled={saving} onClick={() => void discover()}>发现</button></div></div>}
        {candidate && <div className="md:col-span-2 card text-xs"><div className="font-medium">已发现：{String(candidate.agent_card.name || '')}</div><div className="mono text-muted break-all">{candidate.card_url}</div><div className="text-muted">JSON-RPC · {String(candidate.selected_interface.protocol_version || '未知版本')}</div></div>}
        <div><label className="label">显示名称</label><input className="input" maxLength={128} value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div><label className="label">标签（逗号分隔）</label><input className="input" value={tags} onChange={(e) => setTags(e.target.value)} /></div>
        <div className="md:col-span-2"><label className="label">描述</label><input className="input" maxLength={512} value={description} onChange={(e) => setDescription(e.target.value)} /></div>
        <div className="md:col-span-2"><label className="label">凭据</label><input className="input" type="password" maxLength={4096} disabled={!credentialLoaded || clearCredential} value={credential} onChange={(e) => setCredential(e.target.value)} />{template?.credential_configured && <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" disabled={!credentialLoaded} checked={clearCredential} onChange={(e) => setClearCredential(e.target.checked)} />清除已保存凭据</label>}</div>
        <div><label className="label">连接超时（秒）</label><input className="input" type="number" min={0.1} value={connectTimeout} onChange={(e) => setConnectTimeout(Number(e.target.value))} /></div>
        <div><label className="label">同步等待（秒）</label><input className="input" type="number" min={0.1} value={syncWait} onChange={(e) => setSyncWait(Number(e.target.value))} /></div>
      </div>
    </Modal>
  );
}
