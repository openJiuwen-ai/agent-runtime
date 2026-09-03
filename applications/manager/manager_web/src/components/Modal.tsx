import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { useTranslation } from 'react-i18next';

const ModalRequestCloseContext = createContext<(() => void) | null>(null);

/** 在 Modal 内请求关闭（有未保存编辑时会先确认） */
export function useModalRequestClose(): () => void {
  const ctx = useContext(ModalRequestCloseContext);
  if (!ctx) {
    throw new Error('useModalRequestClose must be used within Modal');
  }
  return ctx;
}

/** 取消/关闭按钮：走 Modal 的统一关闭逻辑 */
export function ModalCancelButton({
  className = 'btn ghost',
  children,
  type = 'button',
}: {
  className?: string;
  children?: ReactNode;
  type?: 'button' | 'submit' | 'reset';
}) {
  const { t } = useTranslation();
  const requestClose = useModalRequestClose();
  return (
    <button type={type} className={className} onClick={requestClose}>
      {children ?? t('common.cancel')}
    </button>
  );
}

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'md' | 'lg';
  /** 有未保存编辑时，关闭前弹出确认 */
  dirty?: boolean;
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  size = 'md',
  dirty = false,
}: ModalProps) {
  const { t } = useTranslation();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const requestClose = useCallback(() => {
    if (dirty) {
      setConfirmOpen(true);
      return;
    }
    onClose();
  }, [dirty, onClose]);

  const confirmDiscard = useCallback(() => {
    setConfirmOpen(false);
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!open) setConfirmOpen(false);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      if (confirmOpen) {
        setConfirmOpen(false);
        return;
      }
      requestClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, confirmOpen, requestClose]);

  if (!open) return null;

  return (
    <ModalRequestCloseContext.Provider value={requestClose}>
      <div className="modal-mask" onClick={requestClose}>
        <div
          className={`modal-panel ${size === 'lg' ? 'lg' : ''} animate-rise`}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="modal-header">
            <div className="modal-title">{title}</div>
            <button className="btn ghost sm" onClick={requestClose} aria-label="close">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="modal-body">{children}</div>
          {footer && <div className="modal-footer">{footer}</div>}
        </div>
      </div>

      {confirmOpen && (
        <div className="modal-mask modal-confirm-mask" onClick={() => setConfirmOpen(false)}>
          <div
            className="modal-panel animate-rise"
            style={{ maxWidth: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div className="modal-title">{t('common.unsavedTitle')}</div>
              <button
                className="btn ghost sm"
                onClick={() => setConfirmOpen(false)}
                aria-label="close"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="modal-body">
              <div className="text-sm text-text whitespace-pre-wrap">{t('common.unsavedMessage')}</div>
            </div>
            <div className="modal-footer">
              <button type="button" className="btn ghost" onClick={() => setConfirmOpen(false)}>
                {t('common.cancel')}
              </button>
              <button type="button" className="btn danger" onClick={confirmDiscard}>
                {t('common.unsavedDiscard')}
              </button>
            </div>
          </div>
        </div>
      )}
    </ModalRequestCloseContext.Provider>
  );
}
