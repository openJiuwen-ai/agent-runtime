import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../components/Modal';
import {
  AgentClientRegistrationApi, ApiError,
  type AgentClientBinding, type AgentClientRegistrationCapabilities, type AgentDefinition,
} from '../../services/api';

interface Props {
  row: AgentDefinition | null;
  capabilities: AgentClientRegistrationCapabilities | null;
  onClose: () => void;
  onChanged: () => void;
}

function errorCode(error: unknown): string {
  if (!(error instanceof ApiError)) return 'request_failed';
  const detail = (error.raw as { detail?: { code?: string } })?.detail;
  return detail?.code ?? 'request_failed';
}

export function AgentKeycloakModal({ row, capabilities, onClose, onChanged }: Props) {
  const { t } = useTranslation();
  const [state, setState] = useState<AgentClientBinding | null>(null);
  const [pending, setPending] = useState<'register' | 'delete' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [loading, setLoading] = useState(false);
  const agentId = row?.agent_id;

  useEffect(() => {
    if (!agentId) return;
    let active = true;
    let sawPending = false;
    let timer: ReturnType<typeof setTimeout>;
    setState(null);
    setPending(null);
    setError(null);
    setConfirmDelete(false);
    setLoading(true);
    const refresh = async () => {
      try {
        const result = await AgentClientRegistrationApi.get(agentId);
        if (!active) return;
        if (sawPending && !result.operation) onChanged();
        sawPending = !!result.operation;
        setState(result);
        setError(result.error?.code ?? null);
        if (result.operation) timer = setTimeout(() => void refresh(), 1000);
      } catch {
        if (active) setError('load_failed');
      } finally {
        if (active) setLoading(false);
      }
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, [agentId]);

  const bound = (state ?? row?.keycloak_client)?.status === 'bound';
  const operation = pending ?? state?.operation;
  const busy = loading || !!operation;
  const action = async (kind: 'register' | 'delete') => {
    if (!agentId || busy) return;
    setPending(kind);
    setError(null);
    setConfirmDelete(false);
    try {
      const result = await (kind === 'register'
        ? AgentClientRegistrationApi.register(agentId) : AgentClientRegistrationApi.remove(agentId));
      setState(result);
      onChanged();
    } catch (failure) {
      setError(errorCode(failure));
      const latest = await AgentClientRegistrationApi.get(agentId).catch(() => null);
      if (latest) setState(latest);
      onChanged();
    } finally {
      setPending(null);
    }
  };
  const knownErrors = [
    'client_id_in_use', 'template_busy', 'load_failed', 'bound_client_cleanup_unavailable',
  ];
  const message = error && t(`agentDefinition.keycloak.errors.${knownErrors.includes(error) ? error : 'request_failed'}`);

  return (
    <Modal open={!!row} title={t('agentDefinition.keycloak.title')} onClose={onClose} footer={
      <>
        <ModalCancelButton>{t('common.close')}</ModalCancelButton>
        {!loading && !operation && !confirmDelete && error !== 'load_failed' && (
          <button type="button" className={bound ? 'btn danger' : 'btn primary'}
            disabled={!capabilities?.configured}
            onClick={() => bound ? setConfirmDelete(true) : void action('register')}>
            {t(`agentDefinition.keycloak.${bound ? 'remove' : error ? 'retry' : 'register'}`)}
          </button>
        )}
        {confirmDelete && <button type="button" className="btn danger" onClick={() => void action('delete')}>
          {t('agentDefinition.keycloak.confirmRemove')}
        </button>}
      </>
    }>
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <span className="font-medium text-text-strong">{row?.agent_name}</span>
          <span className={bound ? 'pill ok sm' : 'pill muted sm'}>
            {t(`agentDefinition.keycloak.${bound ? 'bound' : 'unbound'}`)}
          </span>
        </div>
        <div>
          <label className="label" htmlFor="agent-keycloak-id">Client ID / Agent ID</label>
          <input id="agent-keycloak-id" className="input font-mono text-xs" readOnly value={agentId ?? ''} />
          <p className="mt-2 text-xs text-muted">{t('agentDefinition.keycloak.hint')}</p>
        </div>
        {loading && <p className="text-sm text-muted">{t('common.loading')}</p>}
        {operation && <p className="text-sm text-primary" role="status">
          {t(`agentDefinition.keycloak.${operation === 'register' ? 'registering' : 'deleting'}`)}
        </p>}
        {message && <div className="rounded-lg bg-danger-subtle p-3 text-sm text-danger" role="alert">{message}</div>}
        {!capabilities?.configured && <p className="text-sm text-danger" role="alert">{t('agentDefinition.keycloak.unconfigured')}</p>}
        {confirmDelete && <div className="rounded-lg border border-danger p-3 text-sm text-danger">
          <p>{t('agentDefinition.keycloak.removeHint')}</p>
          <button type="button" className="btn ghost sm mt-2" onClick={() => setConfirmDelete(false)}>{t('common.cancel')}</button>
        </div>}
      </div>
    </Modal>
  );
}
