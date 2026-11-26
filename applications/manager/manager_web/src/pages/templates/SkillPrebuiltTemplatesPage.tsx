import { agentResourceReferencesPath } from '../../utils/agentTemplateNavigation';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { SkillPrebuiltTemplateApi, ApiError } from '../../services/api';
import type { SkillPrebuiltTemplate } from '../../types';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Switch } from '../../components/Switch';
import { TableColumnFilter } from '../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../components/TableColumnSort';
import { ListSearchInput } from '../../components/ListSearchInput';
import { SkillPrebuiltTemplateModal } from './SkillPrebuiltTemplateModal';
import { toast } from '../../stores/uiStore';
import { formatTime, truncate } from '../../utils/format';
import {
  deleteBlockedByReferences,
  disableBlockedByReferences,
  templateInUseMessage,
} from '../../utils/templateInUse';
import { useRouter } from '../../router';
import { ResourceExportButton, ResourceImportButton } from '../../components/ResourceImportExport';
import { WorkspaceNote } from '../../components/WorkspaceNote';

type SkillPrebuiltTemplateSortField =
  | 'template_name'
  | 'description'
  | 'skill_id'
  | 'source_id'
  | 'updated_at';

type InstallModeFilter = '' | 'spi' | 'url';

/** 安装方式筛选时一次拉齐再前端分页；须 ≤ 后端 page_size 上限 200 */
const INSTALL_MODE_FETCH_SIZE = 200;

const SKILL_ID_DISPLAY_MAX = 28;

function isSpiInstall(row: SkillPrebuiltTemplate): boolean {
  return Boolean((row.source_id || '').trim());
}

function InstallSourceCell({ row }: { row: SkillPrebuiltTemplate }) {
  const { t } = useTranslation();
  const sourceId = (row.source_id || '').trim();
  if (sourceId) {
    const versionId = (row.version_id || '').trim();
    const full = versionId ? `${sourceId}@${versionId}` : sourceId;
    return (
      <div className="min-w-[9rem] max-w-[16rem]">
        <span className="tag">{t('skillWhitelistTemplate.modeSpiLabel')}</span>
        <div className="mono text-[11px] mt-1 break-all" title={full}>
          <span className="text-text-strong">{sourceId}</span>
          {versionId ? <span className="text-muted"> @{versionId}</span> : null}
        </div>
      </div>
    );
  }
  const packageUrl = (row.package_url || '').trim();
  if (packageUrl) {
    return (
      <div className="min-w-[9rem] max-w-[16rem]">
        <span className="tag">{t('skillWhitelistTemplate.modeUrlLabel')}</span>
        <div className="mono text-[11px] text-muted mt-1 break-all" title={packageUrl}>
          {truncate(packageUrl, 40)}
        </div>
      </div>
    );
  }
  return <span className="text-[11px] text-muted">—</span>;
}

export function SkillPrebuiltTemplatesPage() {
  const { t, i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  const { navigate } = useRouter();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [installModeFilter, setInstallModeFilter] = useState<InstallModeFilter>('');
  const [sortBy, setSortBy] = useState<SkillPrebuiltTemplateSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: SkillPrebuiltTemplateSortField, value: ColumnSortValue) => {
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
    setPage(1);
  }, [searchQuery, installModeFilter]);

  const modeFilterActive = installModeFilter !== '';

  const { data, loading, error, reload } = useAsync(
    () =>
      SkillPrebuiltTemplateApi.list({
        page: modeFilterActive ? 1 : page,
        page_size: modeFilterActive ? INSTALL_MODE_FETCH_SIZE : pageSize,
        search: searchQuery,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [page, pageSize, searchQuery, enabledFilter, installModeFilter, sortBy, sortOrder, modeFilterActive],
  );

  const [items, setItems] = useState<SkillPrebuiltTemplate[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<SkillPrebuiltTemplate | null>(null);
  const [delTarget, setDelTarget] = useState<SkillPrebuiltTemplate | null>(null);
  const [bindGuideOpen, setBindGuideOpen] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  useEffect(() => {
    if (data?.items) {
      setItems(data.items);
    }
  }, [data]);

  const filteredItems = useMemo(() => {
    if (installModeFilter === 'spi') {
      return items.filter(isSpiInstall);
    }
    if (installModeFilter === 'url') {
      return items.filter((row) => !isSpiInstall(row));
    }
    return items;
  }, [items, installModeFilter]);

  const pageItems = useMemo(() => {
    if (!modeFilterActive) return filteredItems;
    const start = (page - 1) * pageSize;
    return filteredItems.slice(start, start + pageSize);
  }, [filteredItems, modeFilterActive, page, pageSize]);

  const total = modeFilterActive ? filteredItems.length : (data?.total ?? data?.items.length ?? 0);

  const copySkillId = async (skillId: string) => {
    try {
      await navigator.clipboard.writeText(skillId);
      toast('success', t('skillWhitelistTemplate.skillIdCopied'));
    } catch {
      // 非安全上下文下浏览器可能禁用剪贴板；保持静默。
    }
  };

  const toggleEnabled = async (row: SkillPrebuiltTemplate, enabled: boolean) => {
    if (togglingId) return;
    const blocked = disableBlockedByReferences(enabled, row.reference_count, t);
    if (blocked) {
      toast('warn', blocked);
      return;
    }
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.template_id === row.template_id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.template_id);
    try {
      await SkillPrebuiltTemplateApi.update(row.template_id, { enabled });
      if (enabledFilter !== '' && enabled !== (enabledFilter === 'true')) {
        setItems((list) => list.filter((item) => item.template_id !== row.template_id));
      }
      toast('success', t('success.saved'));
    } catch (e) {
      setItems((list) =>
        list.map((item) => (item.template_id === row.template_id ? { ...item, enabled: previous } : item)),
      );
      const localized = templateInUseMessage(e, t, 'disable');
      toast('danger', localized ?? t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setTogglingId(null);
    }
  };

  const requestDelete = (row: SkillPrebuiltTemplate) => {
    const blocked = deleteBlockedByReferences(row.reference_count, t);
    if (blocked) {
      toast('warn', blocked);
      return;
    }
    setDelTarget(row);
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
            <div className="page-title truncate" title={t('skillWhitelistTemplate.title')}>
              {t('skillWhitelistTemplate.title')}
            </div>
            <div className="page-subtitle truncate" title={t('skillWhitelistTemplate.subtitle')}>
              {t('skillWhitelistTemplate.subtitle')}
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder={t('skillWhitelistTemplate.searchPlaceholder')}
            className="basis-full sm:basis-auto"
          />
          <button className="btn sm" onClick={() => void reload()}>
            {t('common.refresh')}
          </button>
          <ResourceImportButton resourceType="skill" onImported={() => void reload()} />
          <button
            className="btn primary sm"
            onClick={() => {
              setEditing(null);
              setModalOpen(true);
            }}
          >
            + {t('skillWhitelistTemplate.new')}
          </button>
        </div>
      </div>

      <div className="flex w-full min-w-0 shrink-0 flex-col gap-4">
      <div className="card !p-0">
        {loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : error ? (
          <div className="p-4">
            <div className="text-sm text-danger">{t('errors.loadFailed', { detail: error })}</div>
            {installModeFilter ? (
              <button
                type="button"
                className="btn sm mt-2"
                onClick={() => {
                  setInstallModeFilter('');
                  setPage(1);
                }}
              >
                {t('skillWhitelistTemplate.clearInstallFilter')}
              </button>
            ) : null}
          </div>
        ) : (
          <div className="overflow-x-auto">
          <table className="table w-max min-w-full">
            <thead>
              <tr>
                <th>
                  <TableColumnSort
                    label={t('skillWhitelistTemplate.templateName')}
                    value={sortBy === 'template_name' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('template_name', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('skillWhitelistTemplate.templateDescription')}
                    value={sortBy === 'description' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('description', value)}
                  />
                </th>
                <th>
                  <TableColumnFilter
                    label={t('skillWhitelistTemplate.installSource')}
                    value={installModeFilter}
                    options={[
                      { value: '', label: t('common.all') },
                      { value: 'spi', label: t('skillWhitelistTemplate.modeSpiLabel') },
                      { value: 'url', label: t('skillWhitelistTemplate.modeUrlLabel') },
                    ]}
                    onChange={(value) => {
                      setInstallModeFilter(value as InstallModeFilter);
                      setPage(1);
                    }}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('skillWhitelistTemplate.skillId')}
                    value={sortBy === 'skill_id' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('skill_id', value)}
                  />
                </th>
                <th>
                  <TableColumnFilter
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
                </th>
                <th>{t('skillWhitelistTemplate.referenceCount')}</th>
                <th>
                  <TableColumnSort
                    label={t('skillWhitelistTemplate.updatedAt')}
                    value={sortBy === 'updated_at' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('updated_at', value)}
                  />
                </th>
                <th className="whitespace-nowrap min-w-[9.5rem]">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.length === 0 ? (
                <tr>
                  <td colSpan={8}>
                    <Empty text={t('common.empty')} />
                  </td>
                </tr>
              ) : pageItems.map((row) => (
                <tr key={row.template_id}>
                  <td className="align-top">
                    <div className="text-text-strong font-medium break-words">{row.template_name}</div>
                    <div className="text-[11px] text-muted mono break-all" title={row.template_id}>
                      {row.template_id}
                    </div>
                  </td>
                  <td className="text-[11px] text-muted max-w-[14rem]" title={row.description ?? undefined}>
                    {row.description ? truncate(row.description, 48) : '—'}
                  </td>
                  <td className="align-top">
                    <InstallSourceCell row={row} />
                  </td>
                  <td className="align-top">
                    <div className="flex items-start gap-1 max-w-[16rem]">
                      <span
                        className="mono text-xs text-text-strong min-w-0 break-all"
                        title={row.skill_id}
                      >
                        {truncate(row.skill_id, SKILL_ID_DISPLAY_MAX)}
                      </span>
                      <button
                        type="button"
                        className="btn sm ghost shrink-0 !px-1.5"
                        title={t('skillWhitelistTemplate.copySkillId')}
                        aria-label={t('skillWhitelistTemplate.copySkillId')}
                        onClick={() => void copySkillId(row.skill_id)}
                      >
                        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                          <rect x="9" y="9" width="13" height="13" rx="2" />
                          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                        </svg>
                      </button>
                    </div>
                  </td>
                  <td className="whitespace-nowrap">
                    <Switch
                      checked={row.enabled}
                      disabled={togglingId === row.template_id}
                      aria-label={row.enabled ? t('common.enabled') : t('common.disabled')}
                      onChange={(enabled) => void toggleEnabled(row, enabled)}
                    />
                  </td>
                  <td className="whitespace-nowrap">
                    {row.reference_count > 0 ? (
                      <button type="button"
                        className="tag cursor-pointer"
                        title={t('skillWhitelistTemplate.referenceHint')}
                        onClick={() => navigate(agentResourceReferencesPath('skill', row.template_id, row.template_name))}
                      >
                        {row.reference_count}
                      </button>
                    ) : (
                      <span className="text-[11px] text-muted">0</span>
                    )}
                  </td>
                  <td className="mono text-[11px] text-muted whitespace-nowrap">{formatTime(row.updated_at)}</td>
                  <td className="whitespace-nowrap min-w-[9.5rem]">
                      <div className="flex items-center gap-1">
                        <ResourceExportButton resourceType="skill" resourceIds={[row.template_id]} />
                        <button
                        className="btn sm ghost"
                        onClick={() => {
                          setEditing(row);
                          setModalOpen(true);
                        }}
                      >
                        {t('common.edit')}
                      </button>
                      <button className="btn sm danger" onClick={() => requestDelete(row)}>
                        {t('common.delete')}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {data && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={total}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      )}

      <WorkspaceNote title={t('skillWhitelistTemplate.title')}>
        {t('skillWhitelistTemplate.subtitle')}
        {zh ? '。' : '. '}
        {zh
          ? '保存后，在 Agent 或集群配置中引用；修改已被引用的资源前，请先查看关联模板和集群。'
          : 'Reference saved resources from an Agent or cluster configuration. Review linked templates and clusters before changing a referenced resource.'}
      </WorkspaceNote>
      </div>
      </div>

      <SkillPrebuiltTemplateModal
        open={modalOpen}
        template={editing}
        onClose={() => setModalOpen(false)}
        onSaved={({ created }) => {
          setModalOpen(false);
          void reload();
          if (created) setBindGuideOpen(true);
        }}
      />

      <ConfirmDialog
        open={bindGuideOpen}
        title={t('skillWhitelistTemplate.bindGuideTitle')}
        message={t('skillWhitelistTemplate.bindGuideMessage')}
        confirmText={t('skillWhitelistTemplate.goAgentTemplate')}
        cancelText={t('common.close')}
        onConfirm={() => navigate('/agent-templates')}
        onClose={() => setBindGuideOpen(false)}
      />

      <ConfirmDialog
        open={!!delTarget}
        message={t('skillWhitelistTemplate.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await SkillPrebuiltTemplateApi.remove(delTarget.template_id);
            toast('success', t('success.deleted'));
            void reload();
          } catch (e) {
            const localized = templateInUseMessage(e, t, 'delete');
            toast('danger', localized ?? t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
          }
        }}
        onClose={() => setDelTarget(null)}
      />
    </>
  );
}
