import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../../components/Modal';
import {
  ApiError,
  ImportExportApi,
  type ImportPreflightReport,
} from '../../../services/api';
import { toast } from '../../../stores/uiStore';

interface Props {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}

export function ImportClusterModal({ open, onClose, onImported }: Props) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportPreflightReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) {
      setFile(null);
      setReport(null);
      setError('');
      setBusy(false);
    }
  }, [open]);

  async function selectFile(next: File | null) {
    setFile(next);
    setReport(null);
    setError('');
    if (!next) return;
    if (!next.name.toLowerCase().endsWith('.xlsx')) {
      setError(t('topology.importXlsxOnly'));
      return;
    }
    setBusy(true);
    try {
      setReport(await ImportExportApi.preflightCluster(next));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function runImport() {
    if (!file || !report?.can_import) return;
    setBusy(true);
    setError('');
    try {
      const result = await ImportExportApi.importCluster(file, report.confirmation_token);
      toast('success', t('topology.importSuccess', {
        created: result.created_manager_objects + result.created_identity_objects.length,
        reused: result.reused_objects,
      }));
      onImported();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={t('topology.importTitle')}
      onClose={onClose}
      size="lg"
      footer={(
        <>
          <ModalCancelButton />
          <button
            className="btn primary"
            disabled={busy || !file || !report?.can_import}
            onClick={() => void runImport()}
          >
            {busy ? t('common.loading') : t('topology.importConfirm')}
          </button>
        </>
      )}
    >
      <div className="flex flex-col gap-4">
        <div className="text-sm text-muted">{t('topology.importHelp')}</div>
        <input
          className="input"
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          disabled={busy}
          onChange={(event) => void selectFile(event.target.files?.[0] ?? null)}
        />
        {error && <div className="rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>}
        {report && (
          <>
            <div className={`rounded border p-3 text-sm ${report.can_import ? 'border-success/30 bg-success/5' : 'border-danger/30 bg-danger/5'}`}>
              {report.can_import ? t('topology.preflightPassed') : t('topology.preflightBlocked')}
              <div className="mt-2 flex flex-wrap gap-2 mono text-xs">
                {Object.entries(report.summary).map(([key, count]) => (
                  <span key={key} className="badge">{key}: {count}</span>
                ))}
              </div>
            </div>
            <div className="max-h-72 overflow-auto rounded border border-line">
              <table className="table min-w-full">
                <thead><tr><th>action</th><th>object_type</th><th>object_id</th><th>detail</th></tr></thead>
                <tbody>
                  {report.actions.map((item, index) => (
                    <tr key={`${item.object_type}-${item.object_id}-${index}`}>
                      <td className={item.action === 'conflict' || item.action.startsWith('missing_') ? 'text-danger' : ''}>{item.action}</td>
                      <td>{item.object_type}</td>
                      <td className="mono text-xs break-all">{item.object_id}</td>
                      <td className="text-xs">{item.detail || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
