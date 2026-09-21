import { useTranslation } from 'react-i18next';
import { Modal } from './Modal';
import { ConfigRefreshCostHint } from './ConfigRefreshCostHint';

interface ConfirmDialogProps {
  open: boolean;
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  /** 确认后将下发网关并触发运行时 config_refresh（Pod 日落重建）时展示代价提示 */
  costHint?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmText,
  cancelText,
  danger,
  costHint = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  return (
    <Modal
      open={open}
      title={title ?? t('common.confirm')}
      onClose={onClose}
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>
            {cancelText ?? t('common.cancel')}
          </button>
          <button
            className={danger ? 'btn danger' : 'btn primary'}
            onClick={() => {
              onConfirm();
              onClose();
            }}
          >
            {confirmText ?? t('common.confirm')}
          </button>
        </>
      }
    >
      <div className="text-sm text-text whitespace-pre-wrap">{message}</div>
      {costHint && <ConfigRefreshCostHint />}
    </Modal>
  );
}
