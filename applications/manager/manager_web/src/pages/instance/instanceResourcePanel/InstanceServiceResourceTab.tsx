import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../../components/Modal';
import { MatchExprEditor } from '../../../components/MatchExprEditor';
import { useAsync } from '../../../hooks/useAsync';
import { useFormDirty } from '../../../hooks/useFormDirty';
import {
  ApiError,
  InstanceServiceResource,
  ServiceConfigTemplateApi,
  InstanceServiceResourceRecord,
  InstanceServiceResourceApi,
} from '../../../services/api';
import { toast } from '../../../stores/uiStore';
import {
  matchExprToEditorString,
  parseMatchExpr,
  validateMatchExprModel,
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

type SortField =
  | 'resource_name'
  | 'template_name'
  | 'priority'
  | 'granted_by'
  | 'expires_at'
  | 'enabled'
  | 'updated_at';

/** Align with instance_service_resource ColumnDefinition length. */
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

function recordsToEditorString(records: InstanceServiceResourceRecord[]): string {
  const parts = records
    .map((g) => matchExprToEditorString(g.match_expr))
    .filter((s) => s.length > 0);
  if (parts.length === 0) return '';
  if (parts.length === 1) return parts[0];
  return JSON.stringify(parts);
}

function summarizeMatchExpr(records: InstanceServiceResourceRecord[], allLabel: string): string {
  if (!records.length) return '-';
  const parts = records.map((g) => matchExprToEditorString(g.match_expr)).filter(Boolean);
  if (!parts.length || parts.every((p) => !p)) return allLabel;
  if (parts.length === 1) return parts[0];
  return parts.join(' OR ');
}

function primaryRecord(row: InstanceServiceResource): InstanceServiceResourceRecord | undefined {
  const records = row.records ?? [];
  if (!records.length) return undefined;
  return [...records].sort((a, b) => {
    const dp = (b.priority ?? 0) - (a.priority ?? 0);
    if (dp !== 0) return dp;
    return String(b.updated_at ?? '').localeCompare(String(a.updated_at ?? ''));
  })[0];
}

export function InstanceServiceResourceTab({ instanceId }: Props) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [sortBy, setSortBy] = useState<SortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: SortField, value: ColumnSortValue) => {
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
      InstanceServiceResourceApi.listInstanceResources(instanceId, {
        page,
        page_size: pageSize,
        search: searchQuery || undefined,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [instanceId, page, pageSize, searchQuery, enabledFilter, sortBy, sortOrder],
  );
  const { data: catalogData } = useAsync(
    () => ServiceConfigTemplateApi.list({ page: 1, page_size: 200 }),
    [],
  );
  const items = rosterData?.items ?? [];
  const catalog = catalogData?.items ?? [];

  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [showAdd, setShowAdd] = useState(false);
  const [delTarget, setDelTarget] = useState<InstanceServiceResource | null>(null);
  const [confirmBatchDelete, setConfirmBatchDelete] = useState(false);
  const [addTemplateId, setAddTemplateId] = useState('');
  const [addResourceName, setAddResourceName] = useState('');
  const [addResourceDesc, setAddResourceDesc] = useState('');
  const [addPriority, setAddPriority] = useState(0);
  const [addMatchExpr, setAddMatchExpr] = useState('');
  const [addExpiresAt, setAddExpiresAt] = useState('');
  const [editing, setEditing] = useState<InstanceServiceResource | null>(null);
  const [editResourceName, setEditResourceName] = useState('');
  const [editResourceDesc, setEditResourceDesc] = useState('');
  const [editPriority, setEditPriority] = useState(0);
  const [matchExpr, setMatchExpr] = useState('');
  const [editExpiresAt, setEditExpiresAt] = useState('');
  const [busy, setBusy] = useState(false);
  const { markClean: markAddClean, isDirty: isAddDirty } = useFormDirty(showAdd);
  const { markClean: markEditClean, isDirty: isEditDirty } = useFormDirty(!!editing);

  const addDraft = {
    addTemplateId,
    addResourceName,
    addResourceDesc,
    addPriority,
    addMatchExpr,
    addExpiresAt,
  };
  const editDraft = { editResourceName, editResourceDesc, editPriority, matchExpr, editExpiresAt };

  useEffect(() => {
    setChecked(new Set());
    setPage(1);
  }, [instanceId]);

  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  useEffect(() => {
    if (!showAdd) return;
    markAddClean({
      addTemplateId: '',
      addResourceName: '',
      addResourceDesc: '',
      addPriority: 0,
      addMatchExpr: '',
      addExpiresAt: '',
    });
    setAddTemplateId('');
    setAddResourceName('');
    setAddResourceDesc('');
    setAddPriority(0);
    setAddMatchExpr('');
    setAddExpiresAt('');
  }, [showAdd, markAddClean]);

  useEffect(() => {
    if (editing) {
      const nextMatch = recordsToEditorString(editing.records ?? []);
      const first = primaryRecord(editing);
      const nextName = clipField(
        first?.resource_name ?? editing.resource_name ?? '',
        FIELD_MAX_LENGTH.resource_name,
      );
      const nextDesc = clipField(
        first?.resource_desc ?? editing.resource_desc ?? '',
        FIELD_MAX_LENGTH.resource_desc,
      );
      const nextPriority = first?.priority ?? 0;
      let nextExpires = '';
      if (first?.expires_at) {
        const d = new Date(first.expires_at);
        nextExpires = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      }
      setMatchExpr(nextMatch);
      setEditResourceName(nextName);
      setEditResourceDesc(nextDesc);
      setEditPriority(nextPriority);
      setEditExpiresAt(nextExpires);
      markEditClean({
        editResourceName: nextName,
        editResourceDesc: nextDesc,
        editPriority: nextPriority,
        matchExpr: nextMatch,
        editExpiresAt: nextExpires,
      });
    } else {
      setEditResourceName('');
      setEditResourceDesc('');
      setEditPriority(0);
      setEditExpiresAt('');
    }
  }, [editing, instanceId, markEditClean]);

  const candidates = catalog.map((b) => ({
    id: b.template_id,
    label: b.template_name,
  }));

  function toggleCheck(id: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setChecked((prev) =>
      prev.size === items.length ? new Set() : new Set(items.map((a) => a.resource_id)),
    );
  }

  async function removeSelected() {
    const selected = items.filter((row) => checked.has(row.resource_id));
    if (!selected.length) return;
    try {
      for (const row of selected) {
        await InstanceServiceResourceApi.remove(instanceId, row.resource_id);
      }
      toast('success', t('success.saved'));
      setChecked(new Set());
      reload();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function saveInstanceServiceResource() {
    if (!editing) return;
    if (!editResourceName.trim()) {
      toast('warn', t('policies.fieldRequired', { field: t('instanceDetail.resourcePanel.serviceResource.resourceName') }));
      return;
    }
    if (!Number.isInteger(editPriority)) {
      toast('warn', t('policies.fieldRequired', { field: t('instanceDetail.resourcePanel.serviceResource.priority') }));
      return;
    }
    const err = validateMatchExprModel(parseMatchExpr(matchExpr));
    if (err) {
      toast('danger', t('policies.matchExpr.invalid'));
      return;
    }
    setBusy(true);
    try {
      await InstanceServiceResourceApi.update(instanceId, editing.resource_id, {
        match_exprs: [matchExpr],
        resource_name: editResourceName.trim(),
        resource_desc: editResourceDesc.trim() || null,
        priority: editPriority,
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

  const allMatchLabel = t('instanceDetail.resourcePanel.serviceResource.scopeAll');
  const sr = 'instanceDetail.resourcePanel.serviceResource';

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
        <div className="min-w-[7.5rem] shrink-0">
          <div className="page-title truncate">{t(`${sr}.title`)}</div>
          <div className="page-subtitle truncate">{t(`${sr}.subtitle`)}</div>
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          {checked.size === 0 ? (
            <>
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                placeholder={t(`${sr}.searchPlaceholder`)}
                className="basis-full sm:basis-auto"
              />
              <button className="btn sm" onClick={() => void reload()}>
                {t('common.refresh')}
              </button>
              <button className="btn primary sm" onClick={() => setShowAdd(true)}>
                + {t(`${sr}.add`)}
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
                    <input
                      type="checkbox"
                      checked={items.length > 0 && checked.size === items.length}
                      onChange={toggleAll}
                    />
                  </th>
                  <th>
                    <TableColumnSort
                      label={t(`${sr}.resourceName`)}
                      value={sortBy === 'resource_name' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => handleSortChange('resource_name', value)}
                    />
                  </th>
                  <th>{t(`${sr}.resourceDesc`)}</th>
                  <th>
                    <TableColumnSort
                      label={t(`${sr}.template`)}
                      value={sortBy === 'template_name' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => handleSortChange('template_name', value)}
                    />
                  </th>
                  <th>{t(`${sr}.scopeLabel`)}</th>
                  <th>
                    <TableColumnSort
                      label={t(`${sr}.priority`)}
                      value={sortBy === 'priority' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => handleSortChange('priority', value)}
                    />
                  </th>
                  <th>
                    <TableColumnSort
                      label={t(`${sr}.grantedBy`)}
                      value={sortBy === 'granted_by' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => handleSortChange('granted_by', value)}
                    />
                  </th>
                  <th>
                    <TableColumnSort
                      label={t(`${sr}.expiresAt`)}
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
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={11}>
                      <Empty text={t('common.empty')} />
                    </td>
                  </tr>
                ) : (
                  items.map((row) => {
                    const first = primaryRecord(row);
                    return (
                      <tr key={row.resource_id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={checked.has(row.resource_id)}
                            onChange={() => toggleCheck(row.resource_id)}
                          />
                        </td>
                        <td className="align-top">
                          <div className="text-text-strong font-medium break-words">
                            {row.resource_name || row.resource_id}
                          </div>
                          <div className="text-[11px] text-muted mono break-all" title={row.resource_id}>
                            {row.resource_id}
                          </div>
                        </td>
                        <td className="text-sm text-muted max-w-[14rem] break-words">
                          {row.resource_desc || '-'}
                        </td>
                        <td className="align-top">
                          <div className="text-text-strong font-medium break-words">{row.template_name}</div>
                          <div className="text-[11px] text-muted mono break-all" title={row.template_id}>
                            {row.template_id}
                          </div>
                        </td>
                        <td
                          className="mono text-[11px] text-muted max-w-[14rem]"
                          title={summarizeMatchExpr(row.records ?? [], allMatchLabel)}
                        >
                          {summarizeMatchExpr(row.records ?? [], allMatchLabel)}
                        </td>
                        <td className="whitespace-nowrap">
                          <span className="pill accent mono text-[11px] tabular-nums">
                            {first?.priority ?? 0}
                          </span>
                        </td>
                        <td className="text-[11px] text-muted whitespace-nowrap">
                          {first?.granted_by ?? '-'}
                        </td>
                        <td className="text-[11px] text-muted whitespace-nowrap">
                          {first?.expires_at
                            ? formatTime(first.expires_at)
                            : t(`${sr}.neverExpires`)}
                        </td>
                        <td className="whitespace-nowrap">
                          <Switch
                            checked={first?.enabled !== false}
                            onChange={(enabled) => {
                              InstanceServiceResourceApi.update(instanceId, row.resource_id, {
                                match_exprs: (row.records ?? []).map((g) =>
                                  matchExprToEditorString(g.match_expr),
                                ),
                                resource_name:
                                  (first?.resource_name ?? row.resource_name ?? '').trim() || row.resource_id,
                                resource_desc: first?.resource_desc ?? row.resource_desc ?? null,
                                priority: first?.priority ?? 0,
                                enabled,
                              })
                                .then(() => {
                                  toast('success', t('success.saved'));
                                  reload();
                                })
                                .catch((e) =>
                                  toast('danger', e instanceof ApiError ? e.detail : String(e)),
                                );
                            }}
                            aria-label={
                              first?.enabled !== false ? t('common.enabled') : t('common.disabled')
                            }
                          />
                        </td>
                        <td className="mono text-[11px] text-muted whitespace-nowrap">
                          {formatTime(first?.updated_at)}
                        </td>
                        <td className="whitespace-nowrap min-w-[9.5rem]">
                          <div className="flex items-center gap-1">
                            <button className="btn sm ghost" onClick={() => setEditing(row)}>
                              {t('common.edit')}
                            </button>
                            <button className="btn sm danger" onClick={() => setDelTarget(row)}>
                              {t('common.delete')}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {rosterData && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={rosterData.total ?? items.length}
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
          title={t(`${sr}.add`)}
          onClose={() => setShowAdd(false)}
          dirty={isAddDirty(addDraft)}
          footer={
            <>
              <ModalCancelButton className="btn" />
              <button
                className="btn primary"
                style={{ marginLeft: 8 }}
                disabled={busy || !addTemplateId || !addResourceName.trim() || !Number.isInteger(addPriority)}
                onClick={async () => {
                  if (!addResourceName.trim()) {
                    toast('warn', t('policies.fieldRequired', { field: t(`${sr}.resourceName`) }));
                    return;
                  }
                  if (!addTemplateId) {
                    toast('warn', t('policies.fieldRequired', { field: t(`${sr}.template`) }));
                    return;
                  }
                  if (!Number.isInteger(addPriority)) {
                    toast('warn', t('policies.fieldRequired', { field: t(`${sr}.priority`) }));
                    return;
                  }
                  const err = validateMatchExprModel(parseMatchExpr(addMatchExpr));
                  if (err) {
                    toast('danger', t('policies.matchExpr.invalid'));
                    return;
                  }
                  setBusy(true);
                  try {
                    await InstanceServiceResourceApi.create(instanceId, {
                      ref_template_id: addTemplateId,
                      match_exprs: [addMatchExpr],
                      resource_name: addResourceName.trim(),
                      resource_desc: addResourceDesc.trim() || null,
                      priority: addPriority,
                      expires_at: addExpiresAt ? new Date(addExpiresAt).toISOString() : null,
                    });
                    toast('success', t('success.saved'));
                    setShowAdd(false);
                    setAddTemplateId('');
                    setAddResourceName('');
                    setAddResourceDesc('');
                    setAddPriority(0);
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
            <FieldLabel required>{t(`${sr}.resourceName`)}</FieldLabel>
            <LimitedTextInput
              className="mt-1"
              value={addResourceName}
              maxLength={FIELD_MAX_LENGTH.resource_name}
              onChange={setAddResourceName}
            />
          </label>
          <label className="block mb-3">
            <FieldLabel>{t(`${sr}.resourceDesc`)}</FieldLabel>
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
            <FieldLabel required>{t(`${sr}.template`)}</FieldLabel>
            <select
              className="input mt-1 w-full"
              value={addTemplateId}
              onChange={(e) => {
                const selectedTemplateId = e.target.value;
                setAddTemplateId(selectedTemplateId);
                const selected = catalog.find((item) => item.template_id === selectedTemplateId);
                if (selected && !addResourceName.trim()) {
                  setAddResourceName(clipField(selected.template_name || '', FIELD_MAX_LENGTH.resource_name));
                }
              }}
            >
              <option value="">{t('common.pleaseSelect')}</option>
              {candidates.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}({c.id})
                </option>
              ))}
            </select>
          </label>
          <label className="block mb-3">
            <FieldLabel required>{t(`${sr}.priority`)}</FieldLabel>
            <input
              className="input mt-1 w-full"
              type="number"
              step={1}
              value={Number.isInteger(addPriority) ? addPriority : ''}
              onChange={(e) => {
                const raw = e.target.value;
                setAddPriority(raw === '' ? NaN : Number(raw));
              }}
            />
          </label>
          <label className="block mb-3">
            <FieldLabel>{t(`${sr}.scopeLabel`)}</FieldLabel>
            <div className="mt-1">
              <MatchExprEditor value={addMatchExpr} onChange={setAddMatchExpr} />
            </div>
          </label>
          <label className="block">
            <FieldLabel>{t(`${sr}.expiresAt`)}</FieldLabel>
            <input
              type="datetime-local"
              className="input mt-1 w-full cursor-pointer"
              value={addExpiresAt}
              onChange={(e) => setAddExpiresAt(e.target.value)}
              onClick={(e) => (e.target as HTMLInputElement).showPicker?.()}
            />
            <div className="text-[11px] text-muted mt-1">{t(`${sr}.expiresHint`)}</div>
          </label>
        </Modal>
      )}

      {editing && (
        <Modal
          open
          size="lg"
          title={t(`${sr}.editGrant`, { name: editing.resource_name || editing.template_name })}
          onClose={() => setEditing(null)}
          dirty={isEditDirty(editDraft)}
          footer={
            <>
              <ModalCancelButton className="btn" />
              <button
                className="btn primary"
                style={{ marginLeft: 8 }}
                disabled={busy}
                onClick={() => void saveInstanceServiceResource()}
              >
                {t('common.save')}
              </button>
            </>
          }
        >
          <label className="block mb-3">
            <FieldLabel required>{t(`${sr}.resourceName`)}</FieldLabel>
            <LimitedTextInput
              className="mt-1"
              value={editResourceName}
              maxLength={FIELD_MAX_LENGTH.resource_name}
              onChange={setEditResourceName}
            />
          </label>
          <label className="block mb-3">
            <FieldLabel>{t(`${sr}.resourceDesc`)}</FieldLabel>
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
            <FieldLabel>{t(`${sr}.template`)}</FieldLabel>
            <div className="input mt-1 w-full !bg-[var(--bg-muted)] cursor-not-allowed">
              {editing.template_name}({editing.template_id})
            </div>
          </label>
          <label className="block mb-3">
            <FieldLabel required>{t(`${sr}.priority`)}</FieldLabel>
            <input
              className="input mt-1 w-full"
              type="number"
              step={1}
              value={Number.isInteger(editPriority) ? editPriority : ''}
              onChange={(e) => {
                const raw = e.target.value;
                setEditPriority(raw === '' ? NaN : Number(raw));
              }}
            />
          </label>
          <label className="block mb-3">
            <FieldLabel>{t(`${sr}.scopeLabel`)}</FieldLabel>
            <div className="mt-1">
              <MatchExprEditor
                key={editing.resource_id}
                value={matchExpr}
                onChange={setMatchExpr}
              />
            </div>
          </label>
          <label className="block mb-3">
            <FieldLabel>{t(`${sr}.expiresAt`)}</FieldLabel>
            <input
              type="datetime-local"
              className="input mt-1 w-full cursor-pointer"
              value={editExpiresAt}
              onChange={(e) => setEditExpiresAt(e.target.value)}
              onClick={(e) => (e.target as HTMLInputElement).showPicker?.()}
            />
            <div className="text-[11px] text-muted mt-1">{t(`${sr}.expiresHint`)}</div>
          </label>
        </Modal>
      )}

      <ConfirmDialog
        open={confirmBatchDelete}
        message={t(`${sr}.confirmRemove`, { n: checked.size })}
        danger
        onConfirm={async () => {
          await removeSelected();
          setConfirmBatchDelete(false);
        }}
        onClose={() => setConfirmBatchDelete(false)}
      />

      <ConfirmDialog
        open={!!delTarget}
        message={t(`${sr}.confirmRemove`, { n: 1 })}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await InstanceServiceResourceApi.remove(instanceId, delTarget.resource_id);
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
