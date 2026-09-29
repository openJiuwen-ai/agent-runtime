import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, ImportExportApi } from '../services/api';
import { toast } from '../stores/uiStore';
import { ImportWorkbookModal } from './ImportWorkbookModal';

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function ResourceImportButton({
  resourceType,
  onImported,
  className = 'btn sm',
}: {
  resourceType: string;
  onImported: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className={className} onClick={() => setOpen(true)}>
        {t('importExport.import')}
      </button>
      <ImportWorkbookModal
        open={open}
        resourceType={resourceType}
        onClose={() => setOpen(false)}
        onImported={() => {
          setOpen(false);
          onImported();
        }}
      />
    </>
  );
}

export function ResourceExportButton({
  resourceType,
  resourceIds,
  disabled = false,
  className = 'btn sm ghost',
}: {
  resourceType: string;
  resourceIds: string[];
  disabled?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);

  async function runExport() {
    if (!resourceIds.length || busy) return;
    setBusy(true);
    try {
      const result = await ImportExportApi.exportResources(resourceType, resourceIds);
      download(result.blob, result.filename);
      toast('success', t('importExport.exportSuccess'));
    } catch (error) {
      toast('danger', t('importExport.exportFailed', {
        detail: error instanceof ApiError ? error.detail : (error as Error).message,
      }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      className={className}
      disabled={disabled || busy || resourceIds.length === 0}
      onClick={() => void runExport()}
    >
      {busy ? t('common.loading') : t('importExport.export')}
    </button>
  );
}
