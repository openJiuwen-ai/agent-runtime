import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { ConfigRefreshCostHint } from '../../components/ConfigRefreshCostHint';
import { A2AAccessPolicyTemplateApi, A2AOutboundTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { useFormDirty } from '../../hooks/useFormDirty';
import type { A2AAccessPolicyMode, A2AAccessPolicyTemplate, A2AOutboundTemplate } from '../../types';

interface Props { open: boolean; policy: A2AAccessPolicyTemplate | null; copyFrom?: A2AAccessPolicyTemplate; onClose: () => void; onSaved: (saved: A2AAccessPolicyTemplate) => void; }

export function A2AAccessPolicyModal({ open, policy, copyFrom, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [mode, setMode] = useState<A2AAccessPolicyMode>('allowlist');
  const [enabled, setEnabled] = useState(true);
  const [members, setMembers] = useState<string[]>([]);
  const [agents, setAgents] = useState<A2AOutboundTemplate[]>([]);
  const [agentSearch, setAgentSearch] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    const sourcePolicy = policy ?? copyFrom;
    const nextName = !policy && copyFrom ? `${copyFrom.policy_name.slice(0, 117)} - copy` : sourcePolicy?.policy_name ?? '';
    setName(nextName); setDescription(sourcePolicy?.description ?? '');
    setMode(sourcePolicy?.mode ?? 'allowlist'); setEnabled(sourcePolicy?.enabled ?? true);
    setMembers(sourcePolicy?.member_template_ids ?? []);
    markClean({ name: nextName, description: sourcePolicy?.description ?? '', mode: sourcePolicy?.mode ?? 'allowlist', enabled: sourcePolicy?.enabled ?? true, members: sourcePolicy?.member_template_ids ?? [] });
    setAgentSearch('');
    const loadAgents = async () => {
      const first = await A2AOutboundTemplateApi.list({ page: 1, page_size: 200 });
      const items = [...(first.items ?? [])];
      for (let page = 2; items.length < first.total; page += 1) {
        const next = await A2AOutboundTemplateApi.list({ page, page_size: 200 });
        if (!next.items.length) break;
        items.push(...next.items);
      }
      return items;
    };
    void loadAgents()
      .then(setAgents)
      .catch((e) => toast('danger', e instanceof ApiError ? e.detail : (e as Error).message));
  }, [open, policy, copyFrom, markClean]);

  const toggleMember = (id: string) => setMembers((current) =>
    current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const filteredAgents = useMemo(() => {
    const query = agentSearch.trim().toLocaleLowerCase();
    if (!query) return agents;
    return agents.filter((agent) =>
      agent.template_name.toLocaleLowerCase().includes(query)
      || agent.template_id.toLocaleLowerCase().includes(query));
  }, [agentSearch, agents]);
  const save = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const body = { policy_name: name.trim(), description: description.trim() || null, mode, member_template_ids: members, enabled };
      const saved = policy ? await A2AAccessPolicyTemplateApi.update(policy.policy_id, body)
        : await A2AAccessPolicyTemplateApi.create(body);
      toast('success', t('success.saved')); onSaved(saved);
    } catch (e) { toast('danger', e instanceof ApiError ? e.detail : (e as Error).message); }
    finally { setSaving(false); }
  };

  return <Modal open={open} title={policy ? t('a2a.editPolicyTitle') : t('a2a.newPolicyTitle')} onClose={onClose} size="lg"
    dirty={isDirty({ name, description, mode, enabled, members })}
    footer={<><ModalCancelButton /><button className="btn primary" disabled={saving || !name.trim()} onClick={() => void save()}>{t('common.save')}</button></>}>
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div><label className="label">{t('a2a.policyName')}</label><input className="input" maxLength={128} value={name} onChange={(e) => setName(e.target.value)} /></div>
      <div><label className="label">{t('a2a.policyMode')}</label><select className="select" value={mode} onChange={(e) => setMode(e.target.value as A2AAccessPolicyMode)}><option value="allowlist">{t('a2a.modeAllowlist')}</option><option value="denylist">{t('a2a.modeDenylist')}</option></select></div>
      <div className="md:col-span-2"><label className="label">{t('a2a.description')}</label><input className="input" maxLength={512} value={description} onChange={(e) => setDescription(e.target.value)} /></div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />{t('a2a.enablePolicy')}</label>
      <div className="md:col-span-2"><label className="label">{t('a2a.policyMembers')}</label>
        <div className="text-xs text-muted mb-2">{mode === 'allowlist' ? t('a2a.allowlistHint') : t('a2a.denylistHint')}</div>
        <input className="input mb-2" aria-label={t('a2a.searchAgentAria')} placeholder={t('a2a.searchAgentPlaceholder')} value={agentSearch} onChange={(e) => setAgentSearch(e.target.value)} />
        <div className="card !p-3 h-[258px] overflow-y-auto space-y-2">
          {!agents.length ? <div className="text-sm text-muted">{t('a2a.noRegisteredAgents')}</div> : !filteredAgents.length ? <div className="text-sm text-muted">{t('a2a.noMatchingAgents')}</div> : filteredAgents.map((agent) => <label key={agent.template_id} className="flex min-h-10 items-start gap-2 text-sm"><input type="checkbox" checked={members.includes(agent.template_id)} onChange={() => toggleMember(agent.template_id)} /><span><span className="font-medium">{agent.template_name}</span><span className="block text-xs text-muted mono">{agent.template_id}</span></span></label>)}
        </div>
      </div>
    </div>
    {policy && <ConfigRefreshCostHint scope="template" />}
  </Modal>;
}
