import { useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { ServiceConfigTemplateApi, ApiError } from '../../services/api';
import type { ServiceConfigTemplate } from '../../types';
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
import { ServiceConfigTemplateModal } from './ServiceConfigTemplateModal';
import { toast } from '../../stores/uiStore';
import { bumpGuideRevision } from '../../stores/guideStore';
import { formatTime, truncate } from '../../utils/format';
import { useRouter } from '../../router';
import { runtimeCatalogPath, runtimeClusterReturnPath } from '../../utils/runtimeResourceNavigation';
import {
  downloadJson,
  exportFilename,
  exportTemplateRawdata,
  parseConfigSyncImport,
} from '../../utils/serviceConfigExport';
import { WorkspaceNote } from '../../components/WorkspaceNote';
import './ServiceConfigTemplatesPage.css';

type ServiceConfigTemplateSortField =
  | 'template_name'
  | 'description'
  | 'updated_at';

/**
 * 运行时模板列表页。新建/编辑均为弹窗：
 * - autoNew：/service-config-templates/new 时自动打开新建弹窗（引导页跳转入口）
 * - autoEditId：/service-config-templates/:templateId 时自动拉取并打开编辑弹窗
 */
export function ServiceConfigTemplatesPage({
  autoNew = false,
  autoEditId,
}: {
  autoNew?: boolean;
  autoEditId?: string;
}) {
  const { t } = useTranslation();
  const { params, navigate } = useRouter();
  const catalogPath = runtimeCatalogPath('service-config-templates', params);
  const clusterReturnPath = runtimeClusterReturnPath(params);
  const preparedRuntime = params.get('preparedRuntime');
  const preparedReturnPath = preparedRuntime ? runtimeClusterReturnPath(params, preparedRuntime) : null;
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [sortBy, setSortBy] = useState<ServiceConfigTemplateSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: ServiceConfigTemplateSortField, value: ColumnSortValue) => {
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
  }, [searchQuery]);

  const { data, loading, error, reload } = useAsync(
    () =>
      ServiceConfigTemplateApi.list({
        page,
        page_size: pageSize,
        search: searchQuery,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [page, pageSize, searchQuery, enabledFilter, sortBy, sortOrder]
  );

  const [items, setItems] = useState<ServiceConfigTemplate[]>([]);
  const [delTarget, setDelTarget] = useState<ServiceConfigTemplate | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ServiceConfigTemplate | null>(null);
  const handledAutoAction = useRef('');

  /** 经由 /new 或 /:templateId 进入时自动打开弹窗；关闭后回到列表 URL */
  useEffect(() => {
    const action = autoNew ? 'new' : autoEditId || '';
    if (!action) { handledAutoAction.current = ''; return; }
    if (handledAutoAction.current === action) return;
    handledAutoAction.current = action;
    if (autoNew) {
      setEditing(null);
      setModalOpen(true);
      return;
    }
    if (autoEditId) {
      void ServiceConfigTemplateApi.get(autoEditId)
        .then((row) => {
          setEditing(row);
          setModalOpen(true);
        })
        .catch((e) => {
          toast(
            'danger',
            t('errors.loadFailed', {
              detail: e instanceof ApiError ? e.detail : (e as Error).message,
            }),
          );
          navigate(catalogPath, { replace: true });
        });
    }
  }, [autoNew, autoEditId, navigate, t, catalogPath]);

  const closeAndBack = () => {
    setModalOpen(false);
    if (autoNew || autoEditId) navigate(catalogPath, { replace: true });
  };

  const recordPreparedRuntime = (saved: ServiceConfigTemplate) => {
    const context = new URLSearchParams(params);
    if (clusterReturnPath && saved.enabled) context.set('preparedRuntime', saved.template_id);
    else if (context.get('preparedRuntime') === saved.template_id) context.delete('preparedRuntime');
    if (clusterReturnPath || autoNew || autoEditId) {
      navigate(runtimeCatalogPath('service-config-templates', context), { replace: true });
    }
  };

  const clearPreparedRuntime = (id: string) => {
    if (params.get('preparedRuntime') !== id) return;
    const context = new URLSearchParams(params);
    context.delete('preparedRuntime');
    navigate(runtimeCatalogPath('service-config-templates', context), { replace: true });
  };

  useEffect(() => {
    if (data?.items) {
      setItems(data.items);
    }
  }, [data]);

  const handleExport = (row: ServiceConfigTemplate) => {
    void (async () => {
      try {
        // 绑定容器规格按容器模板目录解析（目录缺失回退存量内联）
        downloadJson(exportFilename(row), await exportTemplateRawdata(row));
        toast('success', t('serviceConfigTemplate.exportOk'));
      } catch (e) {
        toast(
          'danger',
          t('serviceConfigTemplate.exportFailed', {
            detail: e instanceof Error ? e.message : String(e),
          }),
        );
      }
    })();
  };

  const handleDiscoverServerConfig = async () => {
    setImporting(true);
    try {
      const result = await ServiceConfigTemplateApi.discoverAgentserverConfig();
      if (!result.found || result.config === undefined) {
        toast('danger', result.configured ? t('serviceConfigTemplate.serverConfigNotFound', { defaultValue: '服务器未找到 agentserver.json，请检查部署工具目录配置。' }) : t('serviceConfigTemplate.serverConfigUnavailable', { defaultValue: '服务端未配置部署工具目录；请使用本地文件导入。' }));
        return;
      }
      const { body, templateCount } = parseConfigSyncImport(result.config);
      const saved = await ServiceConfigTemplateApi.create(body);
      recordPreparedRuntime(saved);
      toast('success', templateCount > 1 ? t('serviceConfigTemplate.importOkFirstOfMany', { count: templateCount }) : t('serviceConfigTemplate.importOk'));
      void reload(); bumpGuideRevision();
    } catch (err) {
      toast('danger', t('serviceConfigTemplate.importFailed', { detail: err instanceof ApiError ? err.detail : err instanceof Error ? err.message : String(err) }));
    } finally { setImporting(false); }
  };

  const handleImportFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      void (async () => {
        setImporting(true);
        try {
          const text = String(reader.result ?? '');
          const parsed = JSON.parse(text) as unknown;
          const { body, templateCount } = parseConfigSyncImport(parsed);
          const saved = await ServiceConfigTemplateApi.create(body);
          recordPreparedRuntime(saved);
          toast(
            'success',
            templateCount > 1
              ? t('serviceConfigTemplate.importOkFirstOfMany', { count: templateCount })
              : t('serviceConfigTemplate.importOk'),
          );
          void reload();
          bumpGuideRevision();
        } catch (err) {
          toast(
            'danger',
            t('serviceConfigTemplate.importFailed', {
              detail:
                err instanceof ApiError
                  ? err.detail
                  : err instanceof Error
                    ? err.message
                    : String(err),
            }),
          );
        } finally {
          setImporting(false);
        }
      })();
    };
    reader.onerror = () => {
      toast('danger', t('serviceConfigTemplate.importFailed', { detail: 'read error' }));
    };
    reader.readAsText(file, 'UTF-8');
  };

  const toggleEnabled = async (row: ServiceConfigTemplate, enabled: boolean) => {
    if (togglingId) return;
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.template_id === row.template_id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.template_id);
    try {
      await ServiceConfigTemplateApi.update(row.template_id, { enabled });
      if (!enabled) clearPreparedRuntime(row.template_id);
      if (enabledFilter !== '' && enabled !== (enabledFilter === 'true')) {
        setItems((list) => list.filter((item) => item.template_id !== row.template_id));
      }
      toast('success', t('success.saved'));
    } catch (e) {
      setItems((list) =>
        list.map((item) => (item.template_id === row.template_id ? { ...item, enabled: previous } : item)),
      );
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        {clusterReturnPath && (
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <span className="text-muted">正在为当前集群准备运行时模板。保存模板后，还需要返回集群添加运行资源绑定。</span>
            <div className="flex flex-wrap gap-2">
              <button className="btn sm" onClick={() => navigate(clusterReturnPath)}>← 返回集群运行资源</button>
              {preparedReturnPath && <button className="btn sm primary" onClick={() => navigate(preparedReturnPath)}>绑定刚保存的运行时模板 →</button>}
            </div>
          </div>
        )}
        <div className="page-header runtime-catalog-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="runtime-catalog-header__identity">
            <div className="page-title truncate" title={t('serviceConfigTemplate.title')}>
              {t('serviceConfigTemplate.title')}
            </div>
            <div className="page-subtitle truncate" title={t('serviceConfigTemplate.subtitle')}>
              {t('serviceConfigTemplate.subtitle')}
            </div>
          </div>
          <div className="runtime-catalog-header__toolbar">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder={t('serviceConfigTemplate.searchPlaceholder')}
            className="runtime-catalog-header__search"
          />
          <button className="btn sm" onClick={() => void reload()}>
            {t('common.refresh')}
          </button>
          <input
            ref={importInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={handleImportFile}
          />
          <button className="btn sm" disabled={importing} onClick={() => void handleDiscoverServerConfig()} title={t('serviceConfigTemplate.discoverServerHint', { defaultValue: '从 Manager 服务端读取部署期间生成的 agentserver.json' })}>
            {t('serviceConfigTemplate.discoverServer', { defaultValue: '从服务器发现' })}
          </button>
          <button
            className="btn sm"
            disabled={importing}
            title={t('containerTemplate.importRuntimeAndContainerHint')}
            onClick={() => importInputRef.current?.click()}
          >
            {importing ? t('common.loading') : t('serviceConfigTemplate.import')}
          </button>
          <button
            className="btn primary sm"
            onClick={() => {
              setEditing(null);
              setModalOpen(true);
            }}
          >
            + {t('serviceConfigTemplate.new')}
          </button>
        </div>
      </div>

      <div className="flex w-full min-w-0 shrink-0 flex-col gap-4">
      <div className="card manager-page-table !p-0">
        {loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : error ? (
          <div className="p-4 text-sm text-danger">{t('errors.loadFailed', { detail: error })}</div>
        ) : (
          <div className="overflow-x-auto">
          <table className="table w-max min-w-full">
            <thead>
              <tr>
                <th>
                  <TableColumnSort
                    label={t('serviceConfigTemplate.templateName')}
                    value={sortBy === 'template_name' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('template_name', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('serviceConfigTemplate.templateDescription')}
                    value={sortBy === 'description' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('description', value)}
                  />
                </th>
                <th>{t('serviceConfigTemplate.mainImage')}</th>
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
                <th>
                  <TableColumnSort
                    label={t('serviceConfigTemplate.updatedAt')}
                    value={sortBy === 'updated_at' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('updated_at', value)}
                  />
                </th>
                <th className="whitespace-nowrap min-w-[12rem]">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={6}>
                    <Empty text={t('common.empty')} />
                  </td>
                </tr>
              ) : items.map((row) => (
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
                  <td
                    className="mono text-[11px] text-muted min-w-[10rem] max-w-[18rem] break-all align-top"
                    title={row.main_image ?? undefined}
                  >
                    {row.main_image || '—'}
                  </td>
                  <td className="whitespace-nowrap">
                    <Switch
                      checked={row.enabled}
                      disabled={togglingId === row.template_id}
                      aria-label={row.enabled ? t('common.enabled') : t('common.disabled')}
                      onChange={(enabled) => void toggleEnabled(row, enabled)}
                    />
                  </td>
                  <td className="mono text-[11px] text-muted whitespace-nowrap">{formatTime(row.updated_at)}</td>
                  <td className="whitespace-nowrap min-w-[12rem]">
                    <div className="flex items-center gap-1">
                      {clusterReturnPath && (
                        <button
                          className="btn sm primary"
                          disabled={!row.enabled}
                          title={row.enabled ? '返回集群填写绑定范围并保存' : '请先启用此运行规格'}
                          onClick={() => { const target = runtimeClusterReturnPath(params, row.template_id); if (target) navigate(target); }}
                        >
                          绑定到当前集群
                        </button>
                      )}
                      <button
                        className="btn sm ghost"
                        onClick={() => {
                          setEditing(row);
                          setModalOpen(true);
                        }}
                      >
                        {t('common.edit')}
                      </button>
                      <button className="btn sm ghost" onClick={() => handleExport(row)}>
                        {t('serviceConfigTemplate.export')}
                      </button>
                      <button className="btn sm danger" onClick={() => setDelTarget(row)}>
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
          total={data.total ?? data.items.length}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      )}
      <WorkspaceNote title="运行规格决定 Agent 使用哪套运行资源" action={<button className="btn sm" onClick={() => navigate(runtimeCatalogPath('container-templates', params))}>管理容器模板 →</button>}>
        运行规格由容器模板组装而成，必须在具体服务实例中启用后，Agent 才能使用它运行。
      </WorkspaceNote>
      </div>
      </div>

      <ServiceConfigTemplateModal
        open={modalOpen}
        template={editing}
        onClose={closeAndBack}
        onSaved={(saved) => {
          setModalOpen(false);
          bumpGuideRevision();
          void reload();
          recordPreparedRuntime(saved);
        }}
      />

      <ConfirmDialog
        open={!!delTarget}
        message={t('serviceConfigTemplate.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await ServiceConfigTemplateApi.remove(delTarget.template_id);
            clearPreparedRuntime(delTarget.template_id);
            toast('success', t('success.deleted'));
            void reload();
            bumpGuideRevision();
          } catch (e) {
            toast('danger', t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
          }
        }}
        onClose={() => setDelTarget(null)}
      />
    </>
  );
}
