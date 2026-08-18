import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../../hooks/useAsync';
import {
  ApiError,
  InstanceBindingApi,
  InstanceGrant,
  LoginPolicy,
  NO_ORG_GROUP_ID,
  Org,
  OrgApi,
} from '../../../services/api';
import { AddToInstanceModal } from '../../iam/instanceBinding';
import { toast } from '../../../stores/uiStore';
import { Empty } from '../../../components/Empty';
import { Pagination } from '../../../components/Pagination';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { ListSearchInput } from '../../../components/ListSearchInput';
import { useListSearch } from '../../../hooks/useListSearch';
import { TableColumnFilter } from '../../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../../components/TableColumnSort';
import { Switch } from '../../../components/Switch';
import { HintTooltip } from '../../../components/HintTooltip';
import { formatTime } from '../../../utils/format';

const IA = 'instanceDetail.accessPanel.instance';

type BoundOrgRow = Org & { grant: InstanceGrant };
type AccessSortOrder = 'asc' | 'desc';

function matchKw(parts: string[], kw: string): boolean {
  if (!kw) return true;
  return parts.some((p) => p.toLowerCase().includes(kw));
}

function compareText(a: string, b: string, order: AccessSortOrder): number {
  const r = a.localeCompare(b, undefined, { sensitivity: 'base' });
  return order === 'asc' ? r : -r;
}

function compareNullableTime(
  a: string | null | undefined,
  b: string | null | undefined,
  order: AccessSortOrder,
): number {
  const ta = a ? Date.parse(a) : Number.POSITIVE_INFINITY;
  const tb = b ? Date.parse(b) : Number.POSITIVE_INFINITY;
  const na = Number.isFinite(ta) ? ta : Number.POSITIVE_INFINITY;
  const nb = Number.isFinite(tb) ? tb : Number.POSITIVE_INFINITY;
  if (na === nb) return 0;
  return order === 'asc' ? na - nb : nb - na;
}

function compareBool(a: boolean, b: boolean, order: AccessSortOrder): number {
  const va = a ? 1 : 0;
  const vb = b ? 1 : 0;
  return order === 'asc' ? va - vb : vb - va;
}

interface Props {
  instanceId: string;
}

type OrgSortField =
  | 'name'
  | 'granted_by'
  | 'login_policy'
  | 'expires_at'
  | 'enabled'
  | 'updated_at';

export function InstanceOrgsTab({ instanceId }: Props) {
  const { t } = useTranslation();

  const { data: orgsData, reload: reloadOrgs } = useAsync(() => OrgApi.list(), []);
  const { data: bindingData, loading, reload: reloadBinding } = useAsync(
    () => InstanceBindingApi.listOrgs(instanceId),
    [instanceId],
  );

  const allOrgs = (orgsData?.items ?? []).filter((o) => o.group_id !== NO_ORG_GROUP_ID);

  const orgGrantById = useMemo(() => {
    const m = new Map<string, InstanceGrant>();
    for (const g of bindingData?.items ?? []) m.set(g.subject_id, g);
    return m;
  }, [bindingData?.items]);

  const boundOrgIds = useMemo(
    () => new Set(bindingData?.group_ids ?? [...orgGrantById.keys()]),
    [bindingData?.group_ids, orgGrantById],
  );

  const boundOrgs = useMemo((): BoundOrgRow[] => {
    return allOrgs
      .filter((o) => boundOrgIds.has(o.group_id))
      .map((o) => {
        const grant = orgGrantById.get(o.group_id);
        if (!grant) return null;
        return { ...o, grant };
      })
      .filter((x): x is BoundOrgRow => x != null);
  }, [allOrgs, boundOrgIds, orgGrantById]);

  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [showAdd, setShowAdd] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string[] | null>(null);
  const [enabledFilter, setEnabledFilter] = useState('');
  const [loginPolicyFilter, setLoginPolicyFilter] = useState('');
  const [sortBy, setSortBy] = useState<OrgSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: OrgSortField, value: ColumnSortValue) => {
    if (value === '') {
      setSortBy('');
      setSortOrder('asc');
    } else {
      setSortBy(field);
      setSortOrder(value);
    }
    setPage(1);
  };

  useEffect(() => {
    setChecked(new Set());
    setPage(1);
  }, [instanceId]);

  useEffect(() => {
    setPage(1);
  }, [searchQuery, enabledFilter, loginPolicyFilter]);

  const filtered = useMemo(() => {
    const kw = searchQuery?.toLowerCase() ?? '';
    let rows = boundOrgs.filter((o) =>
      matchKw(
        [
          o.group_id,
          o.name,
          o.grant.granted_by ?? '',
          o.grant.login_policy ?? 'allow',
          o.grant.enabled ? 'enabled' : 'disabled',
        ],
        kw,
      ),
    );
    if (enabledFilter === 'true') rows = rows.filter((o) => o.grant.enabled !== false);
    if (enabledFilter === 'false') rows = rows.filter((o) => o.grant.enabled === false);
    if (loginPolicyFilter === 'allow' || loginPolicyFilter === 'deny') {
      rows = rows.filter((o) => (o.grant.login_policy ?? 'allow') === loginPolicyFilter);
    }
    if (!sortBy) return rows;
    const order = sortOrder;
    return [...rows].sort((a, b) => {
      switch (sortBy) {
        case 'name':
          return compareText(a.name || a.group_id, b.name || b.group_id, order);
        case 'granted_by':
          return compareText(a.grant.granted_by ?? '', b.grant.granted_by ?? '', order);
        case 'login_policy':
          return compareText(a.grant.login_policy ?? 'allow', b.grant.login_policy ?? 'allow', order);
        case 'expires_at':
          return compareNullableTime(a.grant.expires_at, b.grant.expires_at, order);
        case 'enabled':
          return compareBool(a.grant.enabled !== false, b.grant.enabled !== false, order);
        case 'updated_at':
          return compareNullableTime(a.grant.updated_at, b.grant.updated_at, order);
        default:
          return 0;
      }
    });
  }, [boundOrgs, searchQuery, enabledFilter, loginPolicyFilter, sortBy, sortOrder]);

  const paged = useMemo(() => {
    const offset = (page - 1) * pageSize;
    return filtered.slice(offset, offset + pageSize);
  }, [filtered, page, pageSize]);

  function toggle(id: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setChecked((prev) =>
      prev.size === paged.length && paged.length > 0
        ? new Set()
        : new Set(paged.map((o) => o.group_id)),
    );
  }

  async function remove(ids: string[]) {
    if (!ids.length) return;
    try {
      await InstanceBindingApi.unbindOrgs(instanceId, ids);
      toast('success', t('success.saved'));
      setChecked(new Set());
      reloadBinding();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    }
  }

  const candidates = allOrgs
    .filter((o) => !boundOrgIds.has(o.group_id))
    .map((o) => ({ id: o.group_id, label: o.name, sub: o.group_id }));

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
        <div className="min-w-[7.5rem] shrink-0">
          <div className="page-title truncate">{t(`${IA}.orgs`)}</div>
          <div className="page-subtitle truncate">{t(`${IA}.orgsHint`)}</div>
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          {checked.size === 0 ? (
            <>
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                placeholder={t(`${IA}.searchOrgsPlaceholder`)}
                className="basis-full sm:basis-auto"
              />
              <button className="btn sm" onClick={() => void reloadBinding()}>
                {t('common.refresh')}
              </button>
              <button className="btn primary sm" onClick={() => setShowAdd(true)}>
                + {t(`${IA}.addOrgs`)}
              </button>
            </>
          ) : (
            <button className="btn danger sm" onClick={() => setPendingDelete(Array.from(checked))}>
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
                      checked={paged.length > 0 && checked.size === paged.length}
                      onChange={toggleAll}
                    />
                  </th>
                  <th>
                    <TableColumnSort
                      label={t(`${IA}.orgName`)}
                      value={sortBy === 'name' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => handleSortChange('name', value)}
                    />
                  </th>
                  <th>
                    <TableColumnSort
                      label={t(`${IA}.grantedBy`)}
                      value={sortBy === 'granted_by' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => handleSortChange('granted_by', value)}
                    />
                  </th>
                  <th>
                    <div className="th-filter">
                      <span className="th-filter__label inline-flex items-center gap-1">
                        {t(`${IA}.loginPolicy`)}
                        <HintTooltip text={t(`${IA}.loginPolicyHint`)} />
                      </span>
                      <TableColumnSort
                        iconOnly
                        label={t(`${IA}.loginPolicy`)}
                        value={sortBy === 'login_policy' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('login_policy', value)}
                      />
                      <TableColumnFilter
                        iconOnly
                        label={t(`${IA}.loginPolicy`)}
                        value={loginPolicyFilter}
                        options={[
                          { value: '', label: t('common.all') },
                          { value: 'allow', label: t(`${IA}.loginAllow`) },
                          { value: 'deny', label: t(`${IA}.loginDeny`) },
                        ]}
                        onChange={(value) => {
                          setLoginPolicyFilter(value);
                          setPage(1);
                        }}
                      />
                    </div>
                  </th>
                  <th>
                    <TableColumnSort
                      label={t(`${IA}.expiresAt`)}
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
                {paged.length === 0 ? (
                  <tr>
                    <td colSpan={8}>
                      <Empty text={t(`${IA}.noOrgs`)} />
                    </td>
                  </tr>
                ) : (
                  paged.map((o) => (
                    <OrgRow
                      key={o.group_id}
                      org={o}
                      checked={checked.has(o.group_id)}
                      onToggle={() => toggle(o.group_id)}
                      onDelete={() => setPendingDelete([o.group_id])}
                      onToggleEnabled={(enabled) => {
                        InstanceBindingApi.updateOrgGrant(instanceId, o.group_id, { enabled })
                          .then(() => {
                            toast('success', t('success.saved'));
                            reloadBinding();
                          })
                          .catch((e) =>
                            toast('danger', e instanceof ApiError ? e.detail : String(e)),
                          );
                      }}
                      onChangeLoginPolicy={(login_policy) => {
                        InstanceBindingApi.updateOrgGrant(instanceId, o.group_id, { login_policy })
                          .then(() => {
                            toast('success', t('success.saved'));
                            reloadBinding();
                          })
                          .catch((e) =>
                            toast('danger', e instanceof ApiError ? e.detail : String(e)),
                          );
                      }}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Pagination
        page={page}
        pageSize={pageSize}
        total={filtered.length}
        onChange={(p, ps) => {
          setPage(p);
          setPageSize(ps);
        }}
      />

      {showAdd && (
        <AddToInstanceModal
          title={t(`${IA}.addOrgs`)}
          candidates={candidates}
          showExpiresAt
          onConfirm={async ({ ids, expires_at, login_policy }) => {
            await InstanceBindingApi.bindOrgs(instanceId, ids, { expires_at, login_policy });
            toast('success', t('success.saved'));
            reloadBinding();
            reloadOrgs();
          }}
          onClose={() => setShowAdd(false)}
        />
      )}

      <ConfirmDialog
        open={!!pendingDelete}
        message={t(`${IA}.confirmRemoveOrgs`, { n: pendingDelete?.length ?? 0 })}
        danger
        onConfirm={async () => {
          if (!pendingDelete) return;
          await remove(pendingDelete);
          setPendingDelete(null);
        }}
        onClose={() => setPendingDelete(null)}
      />
    </div>
  );
}

function OrgRow({
  org,
  checked,
  onToggle,
  onDelete,
  onToggleEnabled,
  onChangeLoginPolicy,
}: {
  org: BoundOrgRow;
  checked: boolean;
  onToggle: () => void;
  onDelete: () => void;
  onToggleEnabled: (enabled: boolean) => void;
  onChangeLoginPolicy: (policy: LoginPolicy) => void;
}) {
  const { t } = useTranslation();
  const policy = (org.grant.login_policy === 'deny' ? 'deny' : 'allow') as LoginPolicy;
  return (
    <tr>
      <td>
        <input type="checkbox" checked={checked} onChange={onToggle} />
      </td>
      <td className="align-top">
        <div className="text-text-strong font-medium break-words">{org.name}</div>
        <div className="text-[11px] text-muted mono break-all" title={org.group_id}>
          {org.group_id}
        </div>
      </td>
      <td className="text-[11px] text-muted whitespace-nowrap">
        {org.grant.granted_by ?? '-'}
      </td>
      <td className="whitespace-nowrap">
        <select
          className="input sm !w-[5.75rem] !min-w-[5.75rem] !max-w-[5.75rem] !px-1.5"
          value={policy}
          onChange={(e) => onChangeLoginPolicy(e.target.value === 'deny' ? 'deny' : 'allow')}
          aria-label={t(`${IA}.loginPolicy`)}
        >
          <option value="allow">{t(`${IA}.loginAllow`)}</option>
          <option value="deny">{t(`${IA}.loginDeny`)}</option>
        </select>
      </td>
      <td className="text-[11px] text-muted whitespace-nowrap">
        {org.grant.expires_at ? formatTime(org.grant.expires_at) : t(`${IA}.neverExpires`)}
      </td>
      <td className="whitespace-nowrap">
        <Switch
          checked={org.grant.enabled !== false}
          onChange={onToggleEnabled}
          aria-label={org.grant.enabled !== false ? t('common.enabled') : t('common.disabled')}
        />
      </td>
      <td className="text-[11px] text-muted whitespace-nowrap">
        {org.grant.updated_at ? formatTime(org.grant.updated_at) : '-'}
      </td>
      <td className="whitespace-nowrap min-w-[6rem]">
        <button className="btn sm danger" onClick={onDelete}>
          {t('common.delete')}
        </button>
      </td>
    </tr>
  );
}
