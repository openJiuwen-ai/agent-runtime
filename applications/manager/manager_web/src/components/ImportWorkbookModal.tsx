import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ApiError,
  ImportExportApi,
  type ImportPreflightReport,
} from '../services/api';
import { toast } from '../stores/uiStore';
import { Modal, ModalCancelButton } from './Modal';

interface Props {
  open: boolean;
  resourceType: string;
  onClose: () => void;
  onImported: () => void;
}

function localizeImportError(
  error: unknown,
  t: ReturnType<typeof useTranslation>['t'],
  i18n: ReturnType<typeof useTranslation>['i18n'],
  fallbackKey: 'importExport.preflightFailed' | 'importExport.importFailed',
): string {
  if (!(error instanceof ApiError)) {
    return t(fallbackKey);
  }
  const raw = error.raw as { detail?: { code?: string } | string } | undefined;
  const code =
    raw?.detail && typeof raw.detail === 'object' && typeof raw.detail.code === 'string'
      ? raw.detail.code
      : '';
  if (code) {
    const key = `importExport.errors.${code}`;
    if (i18n.exists(key)) return t(key);
  }
  const detail = (error.detail || '').toLowerCase();
  if (detail.includes('invalid xlsx') || detail.includes('workbook is empty')) {
    return t('importExport.errors.invalid_workbook');
  }
  if (detail.includes('unsupported format')) {
    return t('importExport.errors.unsupported_format');
  }
  if (detail.includes('blocking preflight')) {
    return t('importExport.preflightBlocked');
  }
  return t(fallbackKey);
}

export function ImportWorkbookModal({ open, resourceType, onClose, onImported }: Props) {
  const { t, i18n } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportPreflightReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) {
      setFile(null);
      setReport(null);
      setBusy(false);
      setError('');
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, [open]);

  async function selectFile(next: File | null) {
    setFile(next);
    setReport(null);
    setError('');
    if (!next) return;
    if (!next.name.toLowerCase().endsWith('.xlsx')) {
      setError(t('importExport.xlsxOnly'));
      return;
    }
    setBusy(true);
    try {
      setReport(await ImportExportApi.preflight(resourceType, next));
    } catch (nextError) {
      setError(localizeImportError(nextError, t, i18n, 'importExport.preflightFailed'));
    } finally {
      setBusy(false);
    }
  }

  async function runImport() {
    if (!file || !report?.can_import) return;
    setBusy(true);
    setError('');
    try {
      const result = await ImportExportApi.import(resourceType, file, report.confirmation_token);
      toast('success', t('importExport.importSuccess', {
        created: result.created_manager_objects + result.created_identity_objects.length,
        reused: result.reused_objects,
      }));
      onImported();
    } catch (nextError) {
      setError(localizeImportError(nextError, t, i18n, 'importExport.importFailed'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={t('importExport.importTitle')}
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
            {busy ? t('common.loading') : t('importExport.confirmImport')}
          </button>
        </>
      )}
    >
      <div className="flex flex-col gap-4">
        <div className="text-sm text-muted">{t('importExport.importHelp')}</div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileInputRef}
            className="sr-only"
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            disabled={busy}
            onChange={(event) => void selectFile(event.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            className="btn sm"
            disabled={busy}
            onClick={() => fileInputRef.current?.click()}
          >
            {t('importExport.chooseFile')}
          </button>
          <span className="text-sm text-muted truncate max-w-full">
            {file?.name || t('importExport.noFileChosen')}
          </span>
        </div>
        {error && (
          <div className="rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
            {error}
          </div>
        )}
        {report && (
          <>
            <div className={`rounded border p-3 text-sm ${report.can_import ? 'border-success/30 bg-success/5' : 'border-danger/30 bg-danger/5'}`}>
              {report.can_import
                ? t('importExport.preflightPassed')
                : t('importExport.preflightBlocked')}
              <div className="mt-2 flex flex-wrap gap-2 mono text-xs">
                {Object.entries(report.summary).map(([key, count]) => (
                  <span key={key} className="badge">{key}: {count}</span>
                ))}
              </div>
            </div>
            <div className="max-h-72 overflow-auto rounded border border-line">
              <table className="table min-w-full">
                <thead>
                  <tr>
                    <th>{t('importExport.colAction')}</th>
                    <th>{t('importExport.colObjectType')}</th>
                    <th>{t('importExport.colObjectId')}</th>
                    <th>{t('importExport.colDetail')}</th>
                  </tr>
                </thead>
                <tbody>
                  {report.actions.map((item, index) => (
                    <tr key={`${item.object_type}-${item.object_id}-${index}`}>
                      <td className={item.action === 'conflict' || item.action.startsWith('missing_') ? 'text-danger' : ''}>
                        {item.action}
                      </td>
                      <td>{item.object_type}</td>
                      <td className="mono break-all text-xs">{item.object_id}</td>
                      <td className="text-xs">{item.detail || '—'}</td>
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
