import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Empty } from '../../components/Empty';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { ListSearchInput } from '../../components/ListSearchInput';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { Pagination } from '../../components/Pagination';
import { useAsync } from '../../hooks/useAsync';
import { useFormDirty } from '../../hooks/useFormDirty';
import { useListSearch } from '../../hooks/useListSearch';
import { AgentDefinitionApi, ApiError, type AgentDefinition } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { truncate } from '../../utils/format';

const NAME_MAX_LENGTH = 128;

/**
 * Agent 模板列表：Agent 配置模板 × 运行时模板的派生目录。
 * 管理面只允许改 Agent Name；行由模板变更自动维护。
 */
export function AgentDefinitionsPage() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [editing, setEditing] = useState<AgentDefinition | null>(null);

  const { data, loading, error, reload } = useAsync(
    () => AgentDefinitionApi.list({
      page,
      page_size: pageSize,
      search: searchQuery,
    }),
    [page, pageSize, searchQuery],
  );

  useEffect(() => setPage(1), [searchQuery]);

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
                        <button
                          className="btn sm ghost"
                          onClick={() => setEditing(row)}
                        >
                          {t('agentDefinition.edit')}
                        </button>
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
      <RenameAgentNameModal
        row={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          void reload();
        }}
      />
    </>
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
        <label className="label">{t('agentDefinition.column.name')}</label>
        <LimitedTextInput
          value={name}
          maxLength={NAME_MAX_LENGTH}
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
