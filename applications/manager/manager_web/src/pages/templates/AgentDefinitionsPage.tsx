import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { ListSearchInput } from '../../components/ListSearchInput';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { Pagination } from '../../components/Pagination';
import { useAsync } from '../../hooks/useAsync';
import { useFormDirty } from '../../hooks/useFormDirty';
import { useGuideAutoOpen } from '../../hooks/useGuideAutoOpen';
import { useListSearch } from '../../hooks/useListSearch';
import {
  AgentDefinitionApi,
  AgentTemplateApi,
  ApiError,
  ServiceConfigTemplateApi,
  type AgentDefinition,
} from '../../services/api';
import { bumpGuideRevision } from '../../stores/guideStore';
import { toast } from '../../stores/uiStore';
import { truncate } from '../../utils/format';

const NAME_MAX_LENGTH = 128;
const TEMPLATE_PAGE_SIZE = 200;

/** 从 ApiError 里取后端返回的错误码（``detail.code``）。 */
function apiErrorCode(error: unknown): string | undefined {
  if (!(error instanceof ApiError)) return undefined;
  const raw = error.raw as { detail?: { code?: string } } | undefined;
  return raw?.detail?.code;
}

/**
 * Agent 模板列表：管理员手动新增「Agent 配置模板 + 运行时模板」组合，
 * 维护每个组合（agent_id）对应的 Agent 名称。
 */
export function AgentDefinitionsPage() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [editing, setEditing] = useState<AgentDefinition | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AgentDefinition | null>(null);

  const { data, loading, error, reload } = useAsync(
    () => AgentDefinitionApi.list({
      page,
      page_size: pageSize,
      search: searchQuery,
    }),
    [page, pageSize, searchQuery],
  );

  useEffect(() => setPage(1), [searchQuery]);
  // 从总览/侧边栏的「未配置 Agent 模板」跳转过来时，自动打开新增弹窗。
  useGuideAutoOpen('agentDefinitionNew', () => setCreating(true));

  const items = data?.items ?? [];

  const copyAgentId = async (agentId: string) => {
    try {
      await navigator.clipboard.writeText(agentId);
      toast('success', t('agentDefinition.copied'));
    } catch {
      // 非安全上下文下浏览器可能禁用剪贴板；保持静默。
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
            <div className="page-title">{t('agentDefinition.title')}</div>
            <div className="page-subtitle">{t('agentDefinition.subtitle')}</div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('agentDefinition.searchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <button className="btn sm primary" onClick={() => setCreating(true)}>
              + {t('agentDefinition.new')}
            </button>
            <button className="btn sm" onClick={() => void reload()}>
              {t('common.refresh')}
            </button>
          </div>
        </div>
        <div className="card !p-0">
          {loading ? (
            <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
          ) : error ? (
            <div className="p-4 text-sm text-danger">
              {t('errors.loadFailed', { detail: error })}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="table w-max min-w-full">
                <thead>
                  <tr>
                    <th className="cell-keep-case">{t('agentDefinition.column.name')}</th>
                    <th className="cell-keep-case">{t('agentDefinition.column.id')}</th>
                    <th>{t('agentDefinition.column.agentConfig')}</th>
                    <th>{t('agentDefinition.column.runtime')}</th>
                    <th>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 ? (
                    <tr>
                      <td colSpan={5}>
                        <Empty text={t('agentDefinition.empty')} />
                      </td>
                    </tr>
                  ) : items.map((row) => (
                    <tr key={row.agent_id}>
                      <td className="align-top">
                        <div className="font-medium text-text-strong">{row.agent_name}</div>
                      </td>
                      <td className="align-top">
                        <div className="flex items-center gap-2">
                          <span className="mono max-w-[18rem] break-all text-xs" title={row.agent_id}>
                            {row.agent_id}
                          </span>
                          <button
                            type="button"
                            className="btn ghost sm shrink-0"
                            title={t('agentDefinition.copyId')}
                            onClick={() => void copyAgentId(row.agent_id)}
                          >
                            {t('common.copy')}
                          </button>
                        </div>
                      </td>
                      <td className="align-top">
                        <div className="text-sm">
                          {row.agent_config_template_name || t('common.none')}
                        </div>
                        <div className="mono text-[11px] text-muted" title={row.agent_config_template_id}>
                          {truncate(row.agent_config_template_id, 36)}
                        </div>
                      </td>
                      <td className="align-top">
                        <div className="text-sm">
                          {row.runtime_template_name || t('common.none')}
                        </div>
                        <div className="mono text-[11px] text-muted" title={row.runtime_template_id}>
                          {truncate(row.runtime_template_id, 36)}
                        </div>
                      </td>
                      <td className="whitespace-nowrap align-top">
                        <div className="flex items-center gap-2">
                          <button
                            className="btn sm ghost"
                            onClick={() => setEditing(row)}
                          >
                            {t('agentDefinition.edit')}
                          </button>
                          <button
                            className="btn sm danger"
                            onClick={() => setDeleteTarget(row)}
                          >
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
        {data ? (
          <Pagination
            page={page}
            pageSize={pageSize}
            total={data.total ?? items.length}
            onChange={(nextPage, nextSize) => {
              setPage(nextPage);
              setPageSize(nextSize);
            }}
          />
        ) : null}
      </div>
      <CreateAgentDefinitionModal
        open={creating}
        onClose={() => setCreating(false)}
        onSaved={() => {
          setCreating(false);
          void reload();
          bumpGuideRevision();
        }}
      />
      <RenameAgentNameModal
        row={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          void reload();
        }}
      />
      <ConfirmDialog
        open={!!deleteTarget}
        danger
        message={t('agentDefinition.deleteConfirm')}
        onConfirm={async () => {
          if (!deleteTarget) return;
          try {
            await AgentDefinitionApi.remove(deleteTarget.agent_id);
            toast('success', t('success.deleted'));
            setDeleteTarget(null);
            void reload();
            bumpGuideRevision();
          } catch (e) {
            toast('danger', t('errors.deleteFailed', {
              detail: e instanceof ApiError ? e.detail : (e as Error).message,
            }));
          }
        }}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}

function CreateAgentDefinitionModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [configTemplates, setConfigTemplates] = useState<{ id: string; name: string }[]>([]);
  const [runtimeTemplates, setRuntimeTemplates] = useState<{ id: string; name: string }[]>([]);
  const [loadingTemplates, setLoadingTemplates] = useState(false);
  const [configId, setConfigId] = useState('');
  const [runtimeId, setRuntimeId] = useState('');
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');

  useEffect(() => {
    if (!open) return;
    setConfigId('');
    setRuntimeId('');
    setName('');
    setFormError('');
    let cancelled = false;
    setLoadingTemplates(true);
    void (async () => {
      try {
        const [configs, runtimes] = await Promise.all([
          AgentTemplateApi.list({ page: 1, page_size: TEMPLATE_PAGE_SIZE, enabled: true }),
          ServiceConfigTemplateApi.list({ page: 1, page_size: TEMPLATE_PAGE_SIZE, enabled: true }),
        ]);
        if (cancelled) return;
        setConfigTemplates(
          configs.items.map((item) => ({ id: item.template_id, name: item.template_name })),
        );
        setRuntimeTemplates(
          runtimes.items.map((item) => ({ id: item.template_id, name: item.template_name })),
        );
      } catch (e) {
        if (!cancelled) {
          toast('danger', t('errors.loadFailed', {
            detail: e instanceof ApiError ? e.detail : (e as Error).message,
          }));
        }
      } finally {
        if (!cancelled) setLoadingTemplates(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, t]);

  const noTemplates = !loadingTemplates
    && (configTemplates.length === 0 || runtimeTemplates.length === 0);

  const submit = async () => {
    if (saving) return;
    const value = name.trim();
    if (!configId || !runtimeId) {
      setFormError(t('agentDefinition.templateRequired'));
      return;
    }
    if (!value) {
      setFormError(t('agentDefinition.nameRequired'));
      return;
    }
    if (value.length > NAME_MAX_LENGTH) {
      setFormError(t('agentDefinition.nameTooLong'));
      return;
    }
    setSaving(true);
    setFormError('');
    try {
      await AgentDefinitionApi.create({
        agent_config_template_id: configId,
        runtime_template_id: runtimeId,
        agent_name: value,
      });
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      const code = apiErrorCode(e);
      if (code === 'agent_name_duplicated') {
        setFormError(t('agentDefinition.nameDuplicated'));
        return;
      }
      if (code === 'agent_id_duplicated') {
        setFormError(t('agentDefinition.combinationDuplicated'));
        return;
      }
      if (code === 'template_unavailable') {
        setFormError(t('agentDefinition.templateUnavailable'));
        return;
      }
      toast('danger', t('errors.saveFailed', {
        detail: e instanceof ApiError ? e.detail : (e as Error).message,
      }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t('agentDefinition.createTitle')}
      onClose={() => { if (!saving) onClose(); }}
      footer={
        <>
          <ModalCancelButton />
          <button
            className="btn primary"
            onClick={() => void submit()}
            disabled={saving || loadingTemplates || noTemplates}
          >
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <label className="label">
            {t('agentDefinition.column.agentConfig')}
            <span className="text-danger ml-0.5" aria-hidden="true">*</span>
          </label>
          <select
            className="select"
            value={configId}
            onChange={(e) => { setConfigId(e.target.value); setFormError(''); }}
            disabled={loadingTemplates}
            aria-required="true"
          >
            <option value="" disabled hidden>{t('agentDefinition.selectAgentConfig')}</option>
            {configTemplates.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-2">
          <label className="label">
            {t('agentDefinition.column.runtime')}
            <span className="text-danger ml-0.5" aria-hidden="true">*</span>
          </label>
          <select
            className="select"
            value={runtimeId}
            onChange={(e) => { setRuntimeId(e.target.value); setFormError(''); }}
            disabled={loadingTemplates}
            aria-required="true"
          >
            <option value="" disabled hidden>{t('agentDefinition.selectRuntime')}</option>
            {runtimeTemplates.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-2">
          <label className="label">
            {t('agentDefinition.column.name')}
            <span className="text-danger ml-0.5" aria-hidden="true">*</span>
          </label>
          <LimitedTextInput
            value={name}
            maxLength={NAME_MAX_LENGTH}
            required
            onChange={(value) => { setName(value); setFormError(''); }}
          />
          <p className="text-[11px] text-muted">{t('agentDefinition.createHint')}</p>
        </div>
        {noTemplates ? (
          <div className="text-[11px] text-warn">{t('agentDefinition.noTemplates')}</div>
        ) : null}
        {formError ? (
          <div className="text-[11px] text-danger" role="alert">{formError}</div>
        ) : null}
      </div>
    </Modal>
  );
}

function RenameAgentNameModal({
  row,
  onClose,
  onSaved,
}: {
  row: AgentDefinition | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(!!row);
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [duplicated, setDuplicated] = useState(false);

  useEffect(() => {
    if (!row) return;
    setName(row.agent_name);
    markClean({ name: row.agent_name });
    setDuplicated(false);
  }, [row, markClean]);

  const submit = async () => {
    if (!row || saving) return;
    const value = name.trim();
    if (!value) {
      toast('warn', t('agentDefinition.nameRequired'));
      return;
    }
    if (value.length > NAME_MAX_LENGTH) {
      toast('warn', t('agentDefinition.nameTooLong'));
      return;
    }
    setSaving(true);
    setDuplicated(false);
    try {
      await AgentDefinitionApi.rename(row.agent_id, value);
      toast('success', t('success.saved'));
      onSaved();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setDuplicated(true);
        return;
      }
      toast('danger', t('errors.saveFailed', {
        detail: error instanceof ApiError ? error.detail : (error as Error).message,
      }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={!!row}
      title={t('agentDefinition.editTitle')}
      onClose={() => { if (!saving) onClose(); }}
      dirty={isDirty({ name })}
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" onClick={() => void submit()} disabled={saving}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <label className="label">
          {t('agentDefinition.column.name')}
          <span className="text-danger ml-0.5" aria-hidden="true">*</span>
        </label>
        <LimitedTextInput
          value={name}
          maxLength={NAME_MAX_LENGTH}
          required
          onChange={(value) => {
            setName(value);
            setDuplicated(false);
          }}
        />
        <p className="text-[11px] text-muted">{t('agentDefinition.editHint')}</p>
        {duplicated ? (
          <div className="text-[11px] text-danger" role="alert">
            {t('agentDefinition.nameDuplicated')}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
