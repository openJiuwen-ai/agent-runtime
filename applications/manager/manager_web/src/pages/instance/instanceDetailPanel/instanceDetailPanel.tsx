import { useTranslation } from 'react-i18next';
import { formatTime, relativeTime, safeStringify } from '../../../utils/format';
import { StatusBadge } from '../../../components/StatusBadge';
import type { InstanceDetail } from '../../../types';

interface Props {
  instance: { data?: InstanceDetail | null };
  onOpenEdit: () => void;
  onRefresh: () => void;
}

export function InstanceDetailPanel({ instance, onOpenEdit, onRefresh }: Props) {
  const { t } = useTranslation();
  const d = instance.data;

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header justify-end">
        <button className="btn sm" onClick={onRefresh}>
          {t('common.refresh')}
        </button>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="card">
          <div className="card-header">
            <div className="card-title">{t('instanceDetail.connectivity')}</div>
          </div>
          <div className="text-xs grid grid-cols-[7.5em_1fr] gap-y-2 gap-x-2 mono">
            <div className="text-muted">namespace</div>
            <div>{d?.namespace ?? '-'}</div>
            <div className="text-muted">gateway host</div>
            <div className="truncate" title={d?.gateway_config_host ?? ''}>
              {d?.gateway_config_host ?? '-'}
            </div>
            <div className="text-muted">runtime host</div>
            <div className="truncate" title={d?.runtime_config_host ?? ''}>
              {d?.runtime_config_host ?? '-'}
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <div className="card-title">{t('instanceDetail.meta')}</div>
          </div>
          <div className="text-xs grid grid-cols-[7.5em_1fr] gap-y-2 gap-x-2">
            <div className="text-muted">gateway</div>
            <div className="flex items-center gap-2">
              {d?.gateway_status ? <StatusBadge status={d.gateway_status} /> : '-'}
              <span className="mono text-muted">{relativeTime(d?.gateway_last_alive)}</span>
            </div>
            <div className="text-muted">runtime</div>
            <div className="flex items-center gap-2">
              {d?.runtime_status ? <StatusBadge status={d.runtime_status} /> : '-'}
              <span className="mono text-muted">{relativeTime(d?.runtime_last_alive)}</span>
            </div>
            <div className="text-muted">space</div>
            <div className="mono">{d?.space_id ?? '-'}</div>
            <div className="text-muted">created</div>
            <div className="mono">
              {formatTime(d?.created_at)} ({d?.created_by ?? '-'})
            </div>
            <div className="text-muted">updated</div>
            <div className="mono">
              {formatTime(d?.updated_at)} ({d?.updated_by ?? '-'})
            </div>
            <div className="text-muted">description</div>
            <div>{d?.description ?? '-'}</div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <div className="card-title">{t('instanceDetail.extraData')}</div>
            <button className="btn ghost sm" onClick={onOpenEdit}>
              {t('instanceDetail.editData')}
            </button>
          </div>
          <pre className="text-[11px] mono whitespace-pre-wrap break-words text-text max-h-48 overflow-auto">
            {safeStringify(d?.data ?? {}, 2) || '-'}
          </pre>
        </div>
      </div>
    </div>
  );
}
