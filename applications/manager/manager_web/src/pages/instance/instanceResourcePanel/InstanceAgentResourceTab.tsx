import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../../components/Modal';
import { MatchExprEditor } from '../../../components/MatchExprEditor';
import { useAsync } from '../../../hooks/useAsync';
import {
  InstanceAgentResourceRecord,
  InstanceAgentResourceApi,
  AgentTemplateApi,
  ApiError,
  InstanceAgentResource,
} from '../../../services/api';
import { toast } from '../../../stores/uiStore';
import {
  matchExprToEditorString,
  parseMatchExpr,
  validateMatchExprModel,
  AGENT_RESOURCE_MATCH_FIELDS,
} from '../../../utils/matchExpr';
import { formatTime } from '../../../utils/format';
import { Empty } from '../../../components/Empty';
import { Pagination } from '../../../components/Pagination';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { Switch } from '../../../components/Switch';
import { LimitedTextInput } from '../../../components/LimitedTextInput';
import { ListSearchInput } from '../../../components/ListSearchInput';
import { useListSearch } from '../../../hooks/useListSearch';
import { TableColumnFilter } from '../../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../../components/TableColumnSort';

interface Props {
  instanceId: string;
}

type AgentInstanceSortField =
  | 'resource_id'
  | 'template_name'
  | 'granted_by'
  | 'expires_at'
  | 'enabled'
  | 'updated_at';

/** Align with instance_agent_resource ColumnDefinition length. */
const FIELD_MAX_LENGTH = {
  resource_name: 128,
  resource_desc: 512,
} as const;

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <span className="text-sm font-medium">
      {children}
      {required ? <span className="text-danger ml-0.5" aria-hidden="true">*</span> : null}
    </span>
  );
}

function recordsToEditorString(records: InstanceAgentResourceRecord[]): string {
  const parts = records
    .map((g) => matchExprToEditorString(g.match_expr))
    .filter((s) => s.length > 0);
  if (parts.length === 0) return '';
  if (parts.length === 1) return parts[0];
  return JSON.stringify(parts);
}

function summarizeMatchExpr(records: InstanceAgentResourceRecord[], allLabel: string): string {
  if (!records.length) return '-';
  const parts = records.map((g) => matchExprToEditorString(g.match_expr)).filter(Boolean);
  if (!parts.length || parts.every((p) => !p)) return allLabel;
  if (parts.length === 1) return parts[0];
  return parts.join(' OR ');
}

export function InstanceAgentResourceTab({ instanceId }: Props) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [sortBy, setSortBy] = useState<AgentInstanceSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: AgentInstanceSortField, value: ColumnSortValue) => {
    if (value === '') {
      setSortBy('');
      setSortOrder('asc');
    } else {
      setSortBy(field);
      setSortOrder(value);
    }
    setPage(1);
  };

  const { data: rosterData, loading, reload } = useAsync(
    () =>
      InstanceAgentResourceApi.listInstanceAgentResources(instanceId, {
        page,
        page_size: pageSize,
        search: searchQuery || undefined,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [instanceId, page, pageSize, searchQuery, enabledFilter, sortBy, sortOrder],
  );
  const { data: catalogData } = useAsync(() => AgentTemplateApi.list(), []);
  const agents = rosterData?.items ?? [];
  const catalog = catalogData?.items ?? [];

  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [showAdd, setShowAdd] = useState(false);
  const [delTarget, setDelTarget] = useState<InstanceAgentResource | null>(null);
  const [confirmBatchDelete, setConfirmBatchDelete] = useState(false);
  const [addAgentId, setAddAgentId] = useState('');
  const [addResourceName, setAddResourceName] = useState('');
  const [addResourceDesc, setAddResourceDesc] = useState('');
  const [addMatchExpr, setAddMatchExpr] = useState('');
  const [addExpiresAt, setAddExpiresAt] = useState('');
  const [editing, setEditing] = useState<InstanceAgentResource | null>(null);
  const [editResourceName, setEditResourceName] = useState('');
  const [editResourceDesc, setEditResourceDesc] = useState('');
  const [matchExpr, setMatchExpr] = useState('');
  const [editExpiresAt, setEditExpiresAt] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setChecked(new Set());
    setPage(1);
  }, [instanceId]);

  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  useEffect(() => {
    if (editing) {
      setMatchExpr(recordsToEditorString(editing.records ?? []));
      const first = (editing.records ?? [])[0];
      setEditResourceName(
        clipField(first?.resource_name ?? editing.resource_name ?? '', FIELD_MAX_LENGTH.resource_name),
      );
      setEditResourceDesc(
        clipField(first?.resource_desc ?? editing.resource_desc ?? '', FIELD_MAX_LENGTH.resource_desc),
      );
      if (first?.expires_at) {
        const d = new Date(first.expires_at);
        const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
        setEditExpiresAt(local);
      } else {
        setEditExpiresAt('');
      }
    } else {
      setEditResourceName('');
      setEditResourceDesc('');
      setEditExpiresAt('');
    }
  }, [editing, instanceId]);

  const candidates = catalog.map((b) => ({ id: b.template_id, label: b.template_name, sub: b.template_id }));

  function toggleCheck(id: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleAll() {
    setChecked((prev) => (prev.size === agents.length ? new Set() : new Set(agents.map((a) => a.resource_id))));
  }

  async function removeSelected() {
    const ids = Array.from(checked);
    if (!ids.length) return;
    try {
      for (const id of ids) {
        const row = agents.find((item) => item.resource_id === id);
        if (!row) continue;
        await InstanceAgentResourceApi.remove(instanceId, row.resource_id);
      }
      toast('success', t('success.saved'));
      setChecked(new Set());
      reload();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function saveInstanceAgentResource() {
    if (!editing) return;
    if (!editResourceName.trim()) {
      toast('warn', t('policies.fieldRequired', { field: t('instanceDetail.resourcePanel.agent.resourceName') }));
      return;
    }
    const err = validateMatchExprModel(parseMatchExpr(matchExpr), AGENT_RESOURCE_MATCH_FIELDS);
    if (err) {
      toast('danger', t('policies.matchExpr.invalid'));
      return;
    }
    setBusy(true);
    try {
      await InstanceAgentResourceApi.update(instanceId, editing.resource_id, {
        match_exprs: [matchExpr],
        resource_name: editResourceName.trim(),
        resource_desc: editResourceDesc.trim() || null,
        expires_at: editExpiresAt ? new Date(editExpiresAt).toISOString() : null,
      });
      toast('success', t('success.saved'));
      setEditing(null);
      reload();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const allMatchLabel = t('instanceDetail.resourcePanel.agent.scopeAll');

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
        <div className="min-w-[7.5rem] shrink-0">
          <div className="page-title truncate">{t('instanceDetail.resourcePanel.agent.title')}</div>
          <div className="page-subtitle truncate">{t('instanceDetail.resourcePanel.agent.subtitle')}</div>
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          {checked.size === 0 ? (
            <>
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                placeholder={t('instanceDetail.resourcePanel.agent.searchPlaceholder')}
                className="basis-full sm:basis-auto"
              />
              <button className="btn sm" onClick={() => void reload()}>
                {t('common.refresh')}
              </button>
              <button className="btn primary sm" onClick={() => setShowAdd(true)}>
                + {t('instanceDetail.resourcePanel.agent.add')}
              </button>
            </>
          ) : (
            <button className="btn danger sm" onClick={() => setConfirmBatchDelete(true)}>
              {t('common.delete')}({checked.size})
            </button>
          )}
        </div>
      </div>

      <div className="card !p-0">
        {loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : (
          <div className="overflow-x-auto">
          <table className="table w-max min-w-full">
            <thead>
              <tr>
                <th style={{ width: 32 }}>
                  <input type="checkbox" checked={agents.length > 0 && checked.size === agents.length} onChange={toggleAll} />
                </th>
                <th>
                  <TableColumnSort
                    label={t('instanceDetail.resourcePanel.agent.resourceName')}
                    value={sortBy === 'resource_id' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('resource_id', value)}
                  />
                </th>
                <th>{t('instanceDetail.resourcePanel.agent.resourceDesc')}</th>
                <th>
                  <TableColumnSort
                    label={t('instanceDetail.resourcePanel.agent.agentTemplate')}
                    value={sortBy === 'template_name' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('template_name', value)}
                  />
                </th>
                <th>{t('instanceDetail.resourcePanel.agent.scopeLabel')}</th>
                <th>
                  <TableColumnSort
                    label={t('instanceDetail.resourcePanel.agent.grantedBy')}
                    value={sortBy === 'granted_by' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('granted_by', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('instanceDetail.resourcePanel.agent.expiresAt')}
                    value={sortBy === 'expires_at' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('expires_at', value)}
                  />
                </th>
                <th>
                  <div className="th-filter">
                    <span className="th-filter__label">{t('common.enabled')}</span>
                    <TableColumnSort
                      iconOnly
                      label={t('common.enabled')}
                      value={sortBy === 'enabled' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => handleSortChange('enabled', value)}
                    />
                    <TableColumnFilter
                      iconOnly
                      label={t('common.enabled')}
                      value={enabledFilter}
                      options={[
                        { value: '', label: t('common.all') },
                        { value: 'true', label: t('common.enabled') },
                        { value: 'false', label: t('common.disabled') },
                      ]}
                      onChange={(value) => {
                        setEnabledFilter(value);
                        setPage(1);
                      }}
                    />
                  </div>
                </th>
                <th>
                  <TableColumnSort
                    label={t('common.updatedAt')}
                    value={sortBy === 'updated_at' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('updated_at', value)}
                  />
                </th>
                <th className="whitespace-nowrap min-w-[6rem]">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {agents.length === 0 ? (
                <tr><td colSpan={10}><Empty text={t('common.empty')} /></td></tr>
              ) : agents.map((a) => {
                const first = (a.records ?? [])[0] as InstanceAgentResourceRecord | undefined;
                return (
                  <tr key={`${a.template_id}:${a.resource_id}`}>
                    <td><input type="checkbox" checked={checked.has(a.resource_id)} onChange={() => toggleCheck(a.resource_id)} /></td>
                    <td className="align-top">
                      <div className="text-text-strong font-medium break-words">
                        {a.resource_name || a.resource_id}
                      </div>
                      <div className="text-[11px] text-muted mono break-all" title={a.resource_id}>
                        {a.resource_id}
                      </div>
                    </td>
                    <td className="text-[11px] text-muted break-words max-w-[14rem] align-top">
                      {a.resource_desc || '-'}
                    </td>
                    <td className="align-top">
                      <div className="text-text-strong font-medium break-words">{a.template_name}</div>
                      <div className="text-[11px] text-muted mono break-all" title={a.template_id}>{a.template_id}</div>
                    </td>
                    <td className="mono text-[11px] text-muted max-w-[14rem]" title={summarizeMatchExpr(a.records ?? [], allMatchLabel)}>
                      {summarizeMatchExpr(a.records ?? [], allMatchLabel)}
                    </td>
                    <td className="text-[11px] text-muted whitespace-nowrap">{first?.granted_by ?? '-'}</td>
                    <td className="text-[11px] text-muted whitespace-nowrap">{first?.expires_at ? formatTime(first.expires_at) : t('instanceDetail.resourcePanel.agent.neverExpires')}</td>
                    <td className="whitespace-nowrap">
                      <Switch
                        checked={first?.enabled !== false}
                        onChange={(enabled) => {
                          InstanceAgentResourceApi.update(instanceId, a.resource_id, {
                            match_exprs: (a.records ?? []).map((g) => matchExprToEditorString(g.match_expr)),
                            resource_name: (first?.resource_name ?? a.resource_name ?? '').trim() || a.resource_id,
                            resource_desc: first?.resource_desc ?? a.resource_desc ?? null,
                            enabled,
                          }).then(() => { toast('success', t('success.saved')); reload(); })
                            .catch((e) => toast('danger', e instanceof ApiError ? e.detail : String(e)));
                        }}
                        aria-label={first?.enabled !== false ? t('common.enabled') : t('common.disabled')}
                      />
                    </td>
                    <td className="mono text-[11px] text-muted whitespace-nowrap">{formatTime(first?.updated_at)}</td>
                    <td className="whitespace-nowrap min-w-[9.5rem]">
                      <div className="flex items-center gap-1">
                        <button className="btn sm ghost" onClick={() => setEditing(a)}>{t('common.edit')}</button>
                        <button className="btn sm danger" onClick={() => setDelTarget(a)}>{t('common.delete')}</button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {rosterData && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={rosterData.total ?? agents.length}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      )}

      {showAdd && (
        <Modal
          open
          size="lg"
          title={t('instanceDetail.resourcePanel.agent.add')}
          onClose={() => setShowAdd(false)}
          footer={
            <>
              <button className="btn" onClick={() => setShowAdd(false)}>{t('common.cancel')}</button>
              <button
                className="btn primary"
                style={{ marginLeft: 8 }}
                disabled={busy || !addAgentId || !addResourceName.trim()}
                onClick={async () => {
                  if (!addAgentId) {
                    toast('warn', t('policies.fieldRequired', { field: t('instanceDetail.resourcePanel.agent.agentLabel') }));
                    return;
                  }
                  if (!addResourceName.trim()) {
                    toast('warn', t('policies.fieldRequired', { field: t('instanceDetail.resourcePanel.agent.resourceName') }));
                    return;
                  }
                  const err = validateMatchExprModel(parseMatchExpr(addMatchExpr), AGENT_RESOURCE_MATCH_FIELDS);
                  if (err) { toast('danger', t('policies.matchExpr.invalid')); return; }
                  setBusy(true);
                  try {
                    await InstanceAgentResourceApi.create(instanceId, {
                      ref_template_id: addAgentId,
                      match_exprs: [addMatchExpr],
                      resource_name: addResourceName.trim(),
                      resource_desc: addResourceDesc.trim() || null,
                      expires_at: addExpiresAt ? new Date(addExpiresAt).toISOString() : null,
                    });
                    toast('success', t('success.saved'));
                    setShowAdd(false);
                    setAddAgentId('');
                    setAddResourceName('');
                    setAddResourceDesc('');
                    setAddMatchExpr('');
                    setAddExpiresAt('');
                    reload();
                  } catch (e) {
                    toast('danger', e instanceof ApiError ? e.detail : String(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {t('common.confirm')}
              </button>
            </>
          }
        >
          <label className="block mb-3">
            <FieldLabel required>{t('instanceDetail.resourcePanel.agent.resourceName')}</FieldLabel>
            <LimitedTextInput
              className="mt-1"
              value={addResourceName}
              maxLength={FIELD_MAX_LENGTH.resource_name}
              onChange={setAddResourceName}
            />
          </label>
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.resourceDesc')}</FieldLabel>
            <textarea
              className="textarea mt-1 w-full min-h-[76px]"
              value={addResourceDesc}
              maxLength={FIELD_MAX_LENGTH.resource_desc}
              onChange={(e) => setAddResourceDesc(clipField(e.target.value, FIELD_MAX_LENGTH.resource_desc))}
            />
            <div className="text-[11px] text-muted mt-1 text-right">
              {addResourceDesc.length}/{FIELD_MAX_LENGTH.resource_desc}
            </div>
          </label>
          <label className="block mb-3">
            <FieldLabel required>{t('instanceDetail.resourcePanel.agent.agentLabel')}</FieldLabel>
            <select
              className="input mt-1 w-full"
              value={addAgentId}
              onChange={(e) => {
                const selectedTemplateId = e.target.value;
                setAddAgentId(selectedTemplateId);
                const selected = catalog.find((item) => item.template_id === selectedTemplateId);
                if (selected && !addResourceName.trim()) {
                  setAddResourceName(clipField(selected.template_name || '', FIELD_MAX_LENGTH.resource_name));
                }
              }}
            >
              <option value="">{t('common.pleaseSelect')}</option>
              {candidates.map((c) => (
                <option key={c.id} value={c.id}>{c.label}({c.id})</option>
              ))}
            </select>
          </label>
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.scopeLabel')}</FieldLabel>
            <div className="mt-1">
              <MatchExprEditor
                value={addMatchExpr}
                onChange={setAddMatchExpr}
                allowedFields={AGENT_RESOURCE_MATCH_FIELDS}
              />
            </div>
          </label>
          <label className="block">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.expiresAt')}</FieldLabel>
            <input
              type="datetime-local"
              className="input mt-1 w-full cursor-pointer"
              value={addExpiresAt}
              onChange={(e) => setAddExpiresAt(e.target.value)}
              onClick={(e) => (e.target as HTMLInputElement).showPicker?.()}
            />
            <div className="text-[11px] text-muted mt-1">{t('instanceDetail.resourcePanel.agent.expiresHint')}</div>
          </label>
        </Modal>
      )}

      {editing && (
        <Modal
          open
          size="lg"
          title={t('instanceDetail.resourcePanel.agent.editGrant', { name: editing.template_name })}
          onClose={() => setEditing(null)}
          footer={
            <>
              <button className="btn" onClick={() => setEditing(null)}>{t('common.cancel')}</button>
              <button className="btn primary" style={{ marginLeft: 8 }} disabled={busy} onClick={() => void saveInstanceAgentResource()}>
                {t('common.save')}
              </button>
            </>
          }
        >
          <label className="block mb-3">
            <FieldLabel required>{t('instanceDetail.resourcePanel.agent.resourceName')}</FieldLabel>
            <LimitedTextInput
              className="mt-1"
              value={editResourceName}
              maxLength={FIELD_MAX_LENGTH.resource_name}
              onChange={setEditResourceName}
            />
          </label>
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.resourceDesc')}</FieldLabel>
            <textarea
              className="textarea mt-1 w-full min-h-[76px]"
              value={editResourceDesc}
              maxLength={FIELD_MAX_LENGTH.resource_desc}
              onChange={(e) => setEditResourceDesc(clipField(e.target.value, FIELD_MAX_LENGTH.resource_desc))}
            />
            <div className="text-[11px] text-muted mt-1 text-right">
              {editResourceDesc.length}/{FIELD_MAX_LENGTH.resource_desc}
            </div>
          </label>
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.agentLabel')}</FieldLabel>
            <div className="input mt-1 w-full !bg-[var(--bg-muted)] cursor-not-allowed">{editing.template_name}({editing.template_id})</div>
          </label>
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.scopeLabel')}</FieldLabel>
            <div className="mt-1">
              <MatchExprEditor
                key={`${editing.template_id}:${editing.resource_id}`}
                value={matchExpr}
                onChange={setMatchExpr}
                allowedFields={AGENT_RESOURCE_MATCH_FIELDS}
              />
            </div>
          </label>
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.expiresAt')}</FieldLabel>
            <input
              type="datetime-local"
              className="input mt-1 w-full cursor-pointer"
              value={editExpiresAt}
              onChange={(e) => setEditExpiresAt(e.target.value)}
              onClick={(e) => (e.target as HTMLInputElement).showPicker?.()}
            />
            <div className="text-[11px] text-muted mt-1">{t('instanceDetail.resourcePanel.agent.expiresHint')}</div>
          </label>
        </Modal>
      )}

      <ConfirmDialog
        open={confirmBatchDelete}
        message={t('instanceDetail.resourcePanel.agent.confirmRemove', { n: checked.size })}
        danger
        onConfirm={async () => {
          await removeSelected();
          setConfirmBatchDelete(false);
        }}
        onClose={() => setConfirmBatchDelete(false)}
      />

      <ConfirmDialog
        open={!!delTarget}
        message={t('instanceDetail.resourcePanel.agent.confirmRemove', { n: 1 })}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await InstanceAgentResourceApi.remove(instanceId, delTarget.resource_id);
            toast('success', t('success.saved'));
            void reload();
          } catch (e) {
            toast('danger', e instanceof ApiError ? e.detail : String(e));
          } finally {
            setDelTarget(null);
          }
        }}
        onClose={() => setDelTarget(null)}
      />
    </div>
  );
}
