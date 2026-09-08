import { useEffect, useMemo, useState } from 'react';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { A2AAccessPolicyTemplateApi, A2AOutboundTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type { A2AAccessPolicyMode, A2AAccessPolicyTemplate, A2AOutboundTemplate } from '../../types';

interface Props { open: boolean; policy: A2AAccessPolicyTemplate | null; onClose: () => void; onSaved: () => void; }

export function A2AAccessPolicyModal({ open, policy, onClose, onSaved }: Props) {
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
    setName(policy?.policy_name ?? ''); setDescription(policy?.description ?? '');
    setMode(policy?.mode ?? 'allowlist'); setEnabled(policy?.enabled ?? true);
    setMembers(policy?.member_template_ids ?? []);
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
  }, [open, policy]);

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
      const body = { policy_name: name.trim(), description: description.trim() || undefined, mode, member_template_ids: members, enabled };
      if (policy) await A2AAccessPolicyTemplateApi.update(policy.policy_id, body);
      else await A2AAccessPolicyTemplateApi.create(body);
      toast('success', '保存成功'); onSaved();
    } catch (e) { toast('danger', e instanceof ApiError ? e.detail : (e as Error).message); }
    finally { setSaving(false); }
  };

  return <Modal open={open} title={policy ? '编辑访问策略' : '新建访问策略'} onClose={onClose} size="lg"
    footer={<><ModalCancelButton /><button className="btn primary" disabled={saving || !name.trim()} onClick={() => void save()}>保存</button></>}>
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div><label className="label">策略名称</label><input className="input" maxLength={128} value={name} onChange={(e) => setName(e.target.value)} /></div>
      <div><label className="label">策略模式</label><select className="select" value={mode} onChange={(e) => setMode(e.target.value as A2AAccessPolicyMode)}><option value="allowlist">白名单</option><option value="denylist">黑名单</option></select></div>
      <div className="md:col-span-2"><label className="label">描述</label><input className="input" maxLength={512} value={description} onChange={(e) => setDescription(e.target.value)} /></div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />启用策略</label>
      <div className="md:col-span-2"><label className="label">A2A Agent 成员</label>
        <div className="text-xs text-muted mb-2">{mode === 'allowlist' ? '空白名单将拒绝访问全部 A2A Agent。' : '空黑名单将允许访问全部已注册 A2A Agent；停用状态会在执行时另行过滤。'}</div>
        <input className="input mb-2" aria-label="搜索 A2A Agent" placeholder="搜索 Agent 名称或 ID" value={agentSearch} onChange={(e) => setAgentSearch(e.target.value)} />
        <div className="card !p-3 h-[258px] overflow-y-auto space-y-2">
          {!agents.length ? <div className="text-sm text-muted">暂无已注册 A2A Agent</div> : !filteredAgents.length ? <div className="text-sm text-muted">未找到匹配的 A2A Agent</div> : filteredAgents.map((agent) => <label key={agent.template_id} className="flex min-h-10 items-start gap-2 text-sm"><input type="checkbox" checked={members.includes(agent.template_id)} onChange={() => toggleMember(agent.template_id)} /><span><span className="font-medium">{agent.template_name}</span><span className="block text-xs text-muted mono">{agent.template_id}</span></span></label>)}
        </div>
      </div>
    </div>
  </Modal>;
}
