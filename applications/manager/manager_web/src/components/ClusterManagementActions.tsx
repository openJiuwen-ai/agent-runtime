import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, ImportExportApi, InstanceApi } from '../services/api';
import { bumpGuideRevision } from '../stores/guideStore';
import { toast } from '../stores/uiStore';
import { Modal } from './Modal';

/** Shared by the cluster catalog and workbench; deletion always requires confirmation. */
export function ClusterManagementActions({ instanceId, name, onDeleted, compact = false, size = compact ? 'sm' : 'md' }: {
  instanceId: string;
  name: string;
  onDeleted: () => void;
  compact?: boolean;
  size?: 'sm' | 'md';
}) {
  const { t, i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef(false);
  const detailOf = (error: unknown) => error instanceof ApiError ? error.detail : (error as Error).message;

  async function exportConfiguration() {
    if (operation.current) return;
    operation.current = true;
    setExporting(true);
    try {
      const { blob, filename } = await ImportExportApi.exportCluster(instanceId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      toast('success', t('topology.exportSuccess'));
    } catch (error) {
      toast('danger', `${t('topology.exportFailed')}${detailOf(error)}`);
    } finally {
      operation.current = false;
      setExporting(false);
    }
  }

  async function deleteCluster() {
    if (operation.current) return;
    operation.current = true;
    setDeleting(true);
    setError(null);
    try {
      const result = await InstanceApi.remove(instanceId);
      if (!result.deleted) throw new Error(tr('集群未删除，请刷新后重试。', 'The cluster was not deleted. Refresh and try again.'));
      toast('success', t('success.deleted'));
      bumpGuideRevision();
      setConfirmOpen(false);
      onDeleted();
    } catch (error) {
      setError(t('errors.deleteFailed', { detail: detailOf(error) }));
    } finally {
      operation.current = false;
      setDeleting(false);
    }
  }

  return <>
    <button type="button" className={`btn${size === 'sm' ? ' sm' : ''}`} disabled={exporting || deleting} onClick={() => void exportConfiguration()}>
      {exporting ? tr('导出中…', 'Exporting…') : compact ? t('topology.export') : tr('导出配置', 'Export configuration')}
    </button>
    <button type="button" className={`btn danger cluster-delete-button${size === 'sm' ? ' sm' : ''}`} disabled={exporting || deleting} onClick={() => { setError(null); setConfirmOpen(true); }}>
      {compact ? t('common.delete') : tr('删除集群', 'Delete cluster')}
    </button>
    <Modal open={confirmOpen} title={tr('删除集群', 'Delete cluster')} onClose={() => { if (!deleting) setConfirmOpen(false); }} footer={<>
      <button type="button" className="btn" disabled={deleting} onClick={() => setConfirmOpen(false)}>{t('common.cancel')}</button>
      <button type="button" className="btn danger cluster-delete-button" disabled={deleting} onClick={() => void deleteCluster()}>{deleting ? tr('删除中…', 'Deleting…') : tr('确认删除', 'Confirm deletion')}</button>
    </>}>
      <p>{tr(`确认删除集群「${name}」？`, `Delete cluster “${name}”?`)}</p>
      <p className="text-xs text-muted mono mt-2 break-all">{instanceId}</p>
      {error && <p className="text-sm text-danger mt-3" role="alert">{error}</p>}
    </Modal>
  </>;
}
