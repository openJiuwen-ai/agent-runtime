import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, PackageAllowlistApi, InstanceApi, OrgApi, UserApi } from '../../services/api';
import type { PackageAllowlistPackage, PackageAllowlistCatalogItem } from '../../types/applicationConfig';
import type { InstanceSummary } from '../../types/instance';
import { Empty } from '../../components/Empty';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { MatchExprEditor } from '../../components/MatchExprEditor';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { useAsync } from '../../hooks/useAsync';
import { toast } from '../../stores/uiStore';
import { useRouter } from '../../router';
import {
  AGENT_RESOURCE_MATCH_FIELDS,
  describeMatchExprScope,
  matchExprToEditorString,
  type MatchField,
  type MatchOp,
} from '../../utils/matchExpr';

type Kind = PackageAllowlistPackage['kind'];

interface Draft {
  kind: Kind;
  id: string;
  matchExpr: string;
}

const EMPTY_DRAFT: Draft = { kind: 'agent_template', id: '', matchExpr: '' };

type ScopeField = 'group_id' | 'user_id';

const SCOPE_PHRASE_KEY: Record<MatchOp, Record<ScopeField, string>> = {
  in: {
    user_id: 'expertAllowlist.scopeUsers',
    group_id: 'expertAllowlist.scopeOrgs',
  },
  'not in': {
    user_id: 'expertAllowlist.scopeExcludeUsers',
    group_id: 'expertAllowlist.scopeExcludeOrgs',
  },
};

function packageKey(kind: string, id: string): string {
  return `${kind}\n${id}`;
}

export function ExpertAllowlistPage() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [clusters, setClusters] = useState<InstanceSummary[]>([]);
  const [clustersLoading, setClustersLoading] = useState(true);
  const [clusterId, setClusterId] = useState('');
  const [packages, setPackages] = useState<PackageAllowlistPackage[]>([]);
  const [catalog, setCatalog] = useState<PackageAllowlistCatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [draftDirty, setDraftDirty] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<PackageAllowlistPackage | null>(null);
  const loadGen = useRef(0);
  const catalogGen = useRef(0);

  const allLabel = t('instanceDetail.resourcePanel.agent.scopeAll');
  const { data: orgPage } = useAsync(() => OrgApi.list(), []);
  const { data: userPage } = useAsync(() => UserApi.list(), []);
  const orgNames = useMemo(() => {
    const names = new Map<string, string>();
    for (const org of orgPage?.items ?? []) names.set(org.group_id, org.display_name || org.group_id);
    return names;
  }, [orgPage]);
  const userNames = useMemo(() => {
    const names = new Map<string, string>();
    for (const user of userPage?.items ?? []) names.set(user.user_id, user.display_name || user.user_id);
    return names;
  }, [userPage]);
  const resolveScopeName = useCallback((field: MatchField, id: string) => {
    if (field === 'user_id') return userNames.get(id) || id;
    if (field === 'group_id') return orgNames.get(id) || id;
    return id;
  }, [orgNames, userNames]);
  const presentScope = useCallback((expr: PackageAllowlistPackage['match_expr']) => {
    const described = describeMatchExprScope(expr, {
      resolveName: resolveScopeName,
      phrase: (field, op, names) => {
        const joined = names.join(t('expertAllowlist.scopeNameSep'));
        if (field !== 'group_id' && field !== 'user_id') return joined;
        return t(SCOPE_PHRASE_KEY[op][field], { names: joined });
      },
      andSep: t('expertAllowlist.scopeAnd'),
      orSep: t('expertAllowlist.scopeOr'),
      groupOpen: t('expertAllowlist.scopeGroupOpen'),
      groupClose: t('expertAllowlist.scopeGroupClose'),
    });
    if (!described) return { text: allLabel, title: allLabel };
    return { text: described.display, title: described.raw };
  }, [allLabel, resolveScopeName, t]);

  useEffect(() => {
    let cancelled = false;
    setClustersLoading(true);
    InstanceApi.list({ page: 1, page_size: 100, sort_by: 'jiuwenclaw_name', sort_order: 'asc' })
      .then((page) => {
        if (cancelled) return;
        const items = page.items || [];
        setClusters(items);
        setClusterId((current) => current || items[0]?.jiuwenclaw_id || '');
      })
      .catch((e) => {
        if (!cancelled) {
          toast('danger', e instanceof ApiError ? e.detail : (e as Error).message);
        }
      })
      .finally(() => {
        if (!cancelled) setClustersLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const reload = useCallback(async (instanceId: string) => {
    if (!instanceId) {
      setPackages([]);
      setLoadError(null);
      return;
    }
    const gen = ++loadGen.current;
    setLoading(true);
    setLoadError(null);
    try {
      const data = await PackageAllowlistApi.get(instanceId);
      if (gen !== loadGen.current) return;
      setPackages(data.packages || []);
    } catch (e) {
      if (gen !== loadGen.current) return;
      if (e instanceof ApiError && e.status === 404) {
        setPackages([]);
      } else {
        setPackages([]);
        setLoadError(e instanceof ApiError ? e.detail : (e as Error).message);
      }
    } finally {
      if (gen === loadGen.current) setLoading(false);
    }
  }, []);

  const reloadCatalog = useCallback(async (instanceId: string) => {
    if (!instanceId) {
      setCatalog([]);
      setCatalogError('');
      return;
    }
    const gen = ++catalogGen.current;
    setCatalogLoading(true);
    try {
      const page = await PackageAllowlistApi.catalog(instanceId);
      if (gen !== catalogGen.current) return;
      setCatalog(page.items || []);
      setCatalogError(page.error || '');
    } catch (e) {
      if (gen !== catalogGen.current) return;
      setCatalog([]);
      setCatalogError(e instanceof ApiError ? e.detail : (e as Error).message);
    } finally {
      if (gen === catalogGen.current) setCatalogLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload(clusterId);
    void reloadCatalog(clusterId);
  }, [clusterId, reload, reloadCatalog]);

  const publish = async (next: PackageAllowlistPackage[]) => {
    if (!clusterId) return false;
    setSaving(true);
    try {
      const data = await PackageAllowlistApi.upsert(clusterId, { packages: next });
      setPackages(data.packages || []);
      toast('success', t('success.saved'));
      return true;
    } catch (e) {
      toast(
        'danger',
        t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }),
      );
      return false;
    } finally {
      setSaving(false);
    }
  };

  const openCreate = () => {
    setEditingKey(null);
    setDraft(EMPTY_DRAFT);
    setDraftDirty(false);
    setModalOpen(true);
  };

  const openEdit = (row: PackageAllowlistPackage) => {
    setEditingKey(packageKey(row.kind, row.id));
    setDraft({
      kind: row.kind,
      id: row.id,
      matchExpr: matchExprToEditorString(row.match_expr),
    });
    setDraftDirty(false);
    setModalOpen(true);
  };

  const saveDraft = async () => {
    const id = draft.id.trim();
    const key = packageKey(draft.kind, id);
    if (packages.some((item) => packageKey(item.kind, item.id) === key && key !== editingKey)) {
      toast('danger', t('expertAllowlist.duplicate'));
      return;
    }
    const nextRow: PackageAllowlistPackage = {
      kind: draft.kind,
      id,
      ...(draft.matchExpr.trim() ? { match_expr: draft.matchExpr.trim() } : {}),
    };
    const next = editingKey
      ? packages.map((item) => (packageKey(item.kind, item.id) === editingKey ? nextRow : item))
      : [...packages, nextRow];
    if (await publish(next)) setModalOpen(false);
  };

  const removeRow = async () => {
    if (!deleteTarget) return;
    const key = packageKey(deleteTarget.kind, deleteTarget.id);
    setDeleteTarget(null);
    await publish(packages.filter((item) => packageKey(item.kind, item.id) !== key));
  };

  const kindLabel = (kind: string) =>
    kind === 'agent_group' ? t('expertAllowlist.kindGroup') : t('expertAllowlist.kindTemplate');

  const catalogName = (kind: string, id: string) => {
    const match = catalog.find((item) => item.kind === kind && item.id === id);
    const name = match?.display_name?.trim() || '';
    return name && name !== id ? name : '';
  };

  const choices = catalog.filter((item) => item.kind === draft.kind);
  const canAdd = Boolean(clusterId) && !saving && !loading && !catalogLoading && catalog.length > 0;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
        <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
          <div className="page-title truncate" title={t('nav.expertPackages')}>
            {t('nav.expertPackages')}
          </div>
          <div className="page-subtitle truncate" title={t('expertAllowlist.subtitle')}>
            {t('expertAllowlist.subtitle')}
          </div>
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          <select
            className="select"
            aria-label={t('guide.selectCluster')}
            value={clusterId}
            disabled={
              clustersLoading
              || clusters.length === 0
              || loading
              || catalogLoading
              || saving
              || modalOpen
            }
            onChange={(event) => {
              setLoading(true);
              setCatalogLoading(true);
              setClusterId(event.target.value);
            }}
          >
            {clusters.length === 0 && <option value="">{t('guide.selectCluster')}</option>}
            {clusters.map((item) => (
              <option key={item.jiuwenclaw_id} value={item.jiuwenclaw_id}>
                {item.jiuwenclaw_name}
              </option>
            ))}
          </select>
          <button
            className="btn sm"
            onClick={() => {
              void reload(clusterId);
              void reloadCatalog(clusterId);
            }}
            disabled={!clusterId || loading}
          >
            {t('common.refresh')}
          </button>
          <button className="btn primary sm" onClick={openCreate} disabled={!canAdd}>
            + {t('expertAllowlist.add')}
          </button>
        </div>
      </div>
      {catalogError ? (
        <p className="text-sm text-muted">{t('expertAllowlist.catalogError', { message: catalogError })}</p>
      ) : null}

      <div className="card !p-0">
        {clustersLoading || loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : clusters.length === 0 ? (
          <div className="p-4">
            <Empty text={t('guide.noClusters')} />
            <div className="flex justify-center pb-4">
              <button className="btn sm" onClick={() => navigate('/instances')}>
                {t('nav.instances')}
              </button>
            </div>
          </div>
        ) : loadError ? (
          <div className="p-4 text-sm text-danger">
            {t('errors.loadFailed', { detail: loadError })}
          </div>
        ) : packages.length === 0 ? (
          <Empty text={t('expertAllowlist.empty')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="table w-max min-w-full">
              <thead>
                <tr>
                  <th>{t('expertAllowlist.kind')}</th>
                  <th>{t('expertAllowlist.packageId')}</th>
                  <th>{t('instanceDetail.resourcePanel.agent.scopeLabel')}</th>
                  <th>{t('common.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {packages.map((row) => {
                  const scope = presentScope(row.match_expr);
                  return (
                    <tr key={packageKey(row.kind, row.id)}>
                      <td className="whitespace-nowrap">{kindLabel(row.kind)}</td>
                      <td className="break-all">
                        {catalogName(row.kind, row.id) ? (
                          <div className="text-text-strong">{catalogName(row.kind, row.id)}</div>
                        ) : null}
                        <div className="mono text-xs">{row.id}</div>
                      </td>
                      <td className="max-w-[24rem] break-words text-sm" title={scope.title}>
                        {scope.text}
                      </td>
                      <td className="whitespace-nowrap">
                        <div className="flex items-center gap-1">
                          <button className="btn sm ghost" onClick={() => openEdit(row)} disabled={saving}>
                            {t('common.edit')}
                          </button>
                          <button className="btn sm danger" onClick={() => setDeleteTarget(row)} disabled={saving}>
                            {t('common.delete')}
                          </button>
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

      <Modal
        open={modalOpen}
        size="lg"
        title={editingKey ? t('expertAllowlist.edit') : t('expertAllowlist.add')}
        dirty={draftDirty}
        onClose={() => setModalOpen(false)}
        footer={
          <>
            <ModalCancelButton className="btn" />
            <button
              className="btn primary"
              style={{ marginLeft: 8 }}
              disabled={saving || !draft.id.trim()}
              onClick={() => void saveDraft()}
            >
              {saving ? t('common.loading') : t('common.save')}
            </button>
          </>
        }
      >
        {editingKey ? (
          <div className="mb-3">
            <div className="text-sm font-medium">{kindLabel(draft.kind)}</div>
            {catalogName(draft.kind, draft.id) ? (
              <div className="mt-1 text-sm text-text-strong">{catalogName(draft.kind, draft.id)}</div>
            ) : null}
            <div className="mono text-xs text-muted break-all">{draft.id}</div>
          </div>
        ) : (
          <>
            <label className="block mb-3">
              <span className="text-sm font-medium">{t('expertAllowlist.kind')}</span>
              <select
                className="select mt-1 w-full"
                value={draft.kind}
                onChange={(event) => {
                  setDraftDirty(true);
                  setDraft({ ...draft, kind: event.target.value as Kind, id: '' });
                }}
              >
                <option value="agent_template">{t('expertAllowlist.kindTemplate')}</option>
                <option value="agent_group">{t('expertAllowlist.kindGroup')}</option>
              </select>
            </label>
            <div className="mb-3">
              <div className="text-sm font-medium">{t('expertAllowlist.pickPackage')}</div>
              {choices.length === 0 ? (
                <p className="mt-1 text-sm text-muted">{t('expertAllowlist.catalogKindEmpty')}</p>
              ) : (
                <div className="mt-1 flex max-h-64 flex-col gap-1 overflow-auto">
                  {choices.map((item) => {
                    const selected = draft.id === item.id;
                    const name = item.display_name?.trim() || item.id;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        className={selected ? 'btn primary sm justify-start' : 'btn sm justify-start'}
                        onClick={() => {
                          setDraftDirty(true);
                          setDraft({ ...draft, id: item.id });
                        }}
                      >
                        <span className="font-medium">{name}</span>
                        {item.version ? <span className="mono text-xs ml-2">{item.version}</span> : null}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </>
        )}
        <label className="block">
          <span className="text-sm font-medium">{t('instanceDetail.resourcePanel.agent.scopeLabel')}</span>
          <div className="mt-1">
            <MatchExprEditor
              value={draft.matchExpr}
              allowedFields={AGENT_RESOURCE_MATCH_FIELDS}
              onChange={(value) => {
                setDraftDirty(true);
                setDraft({ ...draft, matchExpr: value });
              }}
            />
          </div>
          <p className="text-[11px] text-muted mt-1">{t('expertAllowlist.scopeHint')}</p>
        </label>
      </Modal>

      <ConfirmDialog
        open={deleteTarget !== null}
        message={t('expertAllowlist.deleteConfirm', { id: deleteTarget?.id || '' })}
        danger
        onConfirm={() => void removeRow()}
        onClose={() => setDeleteTarget(null)}
      />
    </div>
  );
}
