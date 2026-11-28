import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, DeploymentApi, ImportExportApi, InstanceApi } from '../services/api';
import { useRouter } from '../router';
import { bumpGuideRevision } from '../stores/guideStore';
import { toast } from '../stores/uiStore';
import { Modal } from './Modal';

/** Shared by the cluster catalog and workbench; unbind/undeploy always require confirmation. */
export function ClusterManagementActions({ instanceId, name, namespace, onDeleted, compact = false, size = compact ? 'sm' : 'md' }: {
  instanceId: string;
  name: string;
  /** 卸载任务的目标命名空间；未提供时只显示解绑（工作台头部无 namespace 上下文） */
  namespace?: string;
  onDeleted: () => void;
  compact?: boolean;
  size?: 'sm' | 'md';
}) {
  const { t, i18n } = useTranslation();
  const { navigate } = useRouter();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmUndeployOpen, setConfirmUndeployOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [undeploying, setUndeploying] = useState(false);
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

  async function unbindCluster() {
    if (operation.current) return;
    operation.current = true;
    setDeleting(true);
    setError(null);
    try {
      const result = await InstanceApi.remove(instanceId);
      if (!result.deleted) throw new Error(tr('集群未解绑，请刷新后重试。', 'The cluster was not unbound. Refresh and try again.'));
      toast('success', t('topology.unbindSuccess'));
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

  async function undeployCluster() {
    if (operation.current) return;
    operation.current = true;
    setUndeploying(true);
    setError(null);
    try {
      const created = await DeploymentApi.create({
        action: 'down',
        namespace: namespace ?? '',
        jiuwenclaw_name: name,
      });
      await InstanceApi.remove(instanceId).catch(() => undefined);
      toast('success', t('topology.undeployStarted'));
      bumpGuideRevision();
      setConfirmUndeployOpen(false);
      onDeleted();
      void navigate(`/deployments/${created.deployment_id}`);
    } catch (error) {
      setError(t('errors.saveFailed', { detail: detailOf(error) }));
    } finally {
      operation.current = false;
      setUndeploying(false);
    }
  }

  const busy = exporting || deleting || undeploying;
  const sizeClass = size === 'sm' ? ' sm' : '';
  // 列表操作列与模型页一致：小号 ghost；危险操作用 danger
  const quietClass = `btn${sizeClass}${size === 'sm' ? ' ghost' : ''}`;
  const dangerClass = `btn danger cluster-delete-button${sizeClass}`;

  return <>
    <button type="button" className={quietClass} disabled={busy} onClick={() => void exportConfiguration()}>
      {exporting ? tr('导出中…', 'Exporting…') : compact ? t('topology.export') : tr('导出配置', 'Export configuration')}
    </button>
    {namespace && <button type="button" className={dangerClass} disabled={busy} onClick={() => { setError(null); setConfirmUndeployOpen(true); }}>
      {t('topology.undeploy')}
    </button>}
    <button type="button" className={quietClass} disabled={busy} onClick={() => { setError(null); setConfirmOpen(true); }}>
      {t('topology.unbind')}
    </button>
    <Modal open={confirmOpen} title={t('topology.unbind')} onClose={() => { if (!deleting) setConfirmOpen(false); }} footer={<>
      <button type="button" className="btn" disabled={deleting} onClick={() => setConfirmOpen(false)}>{t('common.cancel')}</button>
      <button type="button" className="btn danger cluster-delete-button" disabled={deleting} onClick={() => void unbindCluster()}>{deleting ? tr('解绑中…', 'Unbinding…') : t('common.confirm')}</button>
    </>}>
      <p>{t('topology.unbindConfirm')}</p>
      <p className="text-xs text-muted mono mt-2 break-all">{instanceId}</p>
      {error && <p className="text-sm text-danger mt-3" role="alert">{error}</p>}
    </Modal>
    {namespace && <Modal open={confirmUndeployOpen} title={t('topology.undeploy')} onClose={() => { if (!undeploying) setConfirmUndeployOpen(false); }} footer={<>
      <button type="button" className="btn" disabled={undeploying} onClick={() => setConfirmUndeployOpen(false)}>{t('common.cancel')}</button>
      <button type="button" className="btn danger cluster-delete-button" disabled={undeploying} onClick={() => void undeployCluster()}>{undeploying ? tr('发起中…', 'Starting…') : t('common.confirm')}</button>
    </>}>
      <p>{t('topology.undeployConfirm', { namespace, name })}</p>
      <p className="text-xs text-muted mono mt-2 break-all">{instanceId}</p>
      {error && <p className="text-sm text-danger mt-3" role="alert">{error}</p>}
    </Modal>}
  </>;
}
