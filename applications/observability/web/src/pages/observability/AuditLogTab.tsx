import { Fragment, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  LokiApi,
  parseLokiAuditStreams,
  orderedDesignFields,
  extensionFields,
  DESIGN_HEADER_FIELDS,
  DESIGN_CONTENT_FIELDS,
  DESIGN_BRIDGE_FIELDS,
  AuditLogEntry,
} from '../../services/api';

const AUDIT_TYPE_COLORS: Record<string, string> = {
  ua: '#22c55e',
  evt: '#ef4444',
};

// LogQL 字符串字面量转义：反斜杠与双引号，防止用户输入闭合/注入过滤表达式
function escapeLogqlValue(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function formatTime(ms: number): string {
  return new Date(ms).toLocaleString([], {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/** HTTP / 非安全上下文下 clipboard API 常不可用，回退到 textarea + execCommand。 */
async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    ta.style.top = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function FieldGrid({
  items,
  labelOf,
}: {
  items: { key: string; value: string }[];
  labelOf: (key: string) => string;
}) {
  return (
    <div
      className="grid gap-x-4 gap-y-1"
      style={{ gridTemplateColumns: 'minmax(140px, 200px) 1fr' }}
    >
      {items.map(({ key, value }) => {
        const isPlaceholder = !value || value === '-';
        return (
          <Fragment key={key}>
            <div className="text-muted truncate" title={key}>
              {labelOf(key)}
              <span className="opacity-50 ml-1 mono text-[10px]">{key}</span>
            </div>
            <div
              className={`mono break-all ${isPlaceholder ? 'text-muted opacity-60' : ''}`}
            >
              {value || '-'}
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

function DetailPanel({ entry }: { entry: AuditLogEntry }) {
  const { t } = useTranslation();
  const [showNoise, setShowNoise] = useState(false);
  const [copied, setCopied] = useState(false);

  const labelOf = (key: string) =>
    t(`observability.audit.fields.${key}`, { defaultValue: key });

  const summaryKeys = [
    'event_type',
    'level',
    'SUBMDL',
    'PROC',
    'RSPCD',
    'RESULT',
    'COST',
  ] as const;

  const summary = orderedDesignFields(entry.attributes, summaryKeys);
  const headers = orderedDesignFields(entry.attributes, DESIGN_HEADER_FIELDS);
  const content = orderedDesignFields(entry.attributes, DESIGN_CONTENT_FIELDS);
  const bridge = orderedDesignFields(entry.attributes, DESIGN_BRIDGE_FIELDS).filter(
    (x) => !summaryKeys.includes(x.key as (typeof summaryKeys)[number]),
  );
  const extras = extensionFields(entry.attributes, showNoise);

  const [copyError, setCopyError] = useState(false);

  const copyJson = async () => {
    const text = JSON.stringify(entry.attributes, null, 2);
    setCopyError(false);
    const ok = await copyTextToClipboard(text);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } else {
      setCopyError(true);
      setTimeout(() => setCopyError(false), 2500);
    }
  };

  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center gap-2 flex-wrap">
        <button type="button" className="input" style={{ width: 'auto' }} onClick={copyJson}>
          {copied
            ? t('observability.audit.copied')
            : copyError
              ? t('observability.audit.copyFailed')
              : t('observability.audit.copyJson')}
        </button>
        <label className="flex items-center gap-1 text-muted cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showNoise}
            onChange={(e) => setShowNoise(e.target.checked)}
          />
          {t('observability.audit.showNoise')}
        </label>
      </div>

      <section>
        <div className="font-semibold mb-1">{t('observability.audit.sections.summary')}</div>
        <FieldGrid items={summary} labelOf={labelOf} />
      </section>
      <section>
        <div className="font-semibold mb-1">{t('observability.audit.sections.header')}</div>
        <FieldGrid items={headers} labelOf={labelOf} />
      </section>
      <section>
        <div className="font-semibold mb-1">{t('observability.audit.sections.content')}</div>
        <FieldGrid items={content} labelOf={labelOf} />
      </section>
      <section>
        <div className="font-semibold mb-1">{t('observability.audit.sections.bridge')}</div>
        <FieldGrid items={bridge} labelOf={labelOf} />
      </section>
      {extras.length > 0 && (
        <section>
          <div className="font-semibold mb-1">{t('observability.audit.sections.extension')}</div>
          <FieldGrid items={extras} labelOf={labelOf} />
        </section>
      )}
    </div>
  );
}

export function AuditLogTab() {
  const { t } = useTranslation();
  const defaultEnd = new Date();
  const defaultStart = new Date(defaultEnd.getTime() - 24 * 3600 * 1000);
  const [startDate, setStartDate] = useState(defaultStart.toISOString().slice(0, 10));
  const [endDate, setEndDate] = useState(defaultEnd.toISOString().slice(0, 10));
  const [auditType, setAuditType] = useState('');
  const [userId, setUserId] = useState('');
  const [groupId, setGroupId] = useState('');
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const logql = useMemo(() => {
    let q = '{service_name=~"jiuwenclaw-(agentserver|gateway)"}';
    const filters: string[] = [];
    if (auditType) {
      filters.push(`audit_type="${escapeLogqlValue(auditType)}"`);
    }
    if (userId) filters.push(`user_id="${escapeLogqlValue(userId)}"`);
    if (groupId) filters.push(`group_id="${escapeLogqlValue(groupId)}"`);
    if (filters.length > 0) {
      q += ` | ${filters.join(' | ')}`;
    }
    return q;
  }, [auditType, userId, groupId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const s = Math.floor(new Date(startDate).getTime() / 1000);
        const e = Math.floor(new Date(endDate).getTime() / 1000) + 86399;
        const resp = await LokiApi.queryRange(logql, s, e, 500);
        if (cancelled) return;
        setEntries(parseLokiAuditStreams(resp));
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [logql, startDate, endDate]);

  return (
    <div className="space-y-4">
      <div className="card p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold">{t('observability.audit.filterTitle')}</div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            className="input"
            style={{ width: '140px' }}
            value={auditType}
            onChange={(e) => setAuditType(e.target.value)}
          >
            <option value="">{t('observability.audit.allResults')}</option>
            <option value="ua">{t('observability.audit.ua')}</option>
            <option value="evt">{t('observability.audit.evt')}</option>
          </select>
          <input
            type="date"
            className="input"
            style={{ width: '150px' }}
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
          <input
            type="date"
            className="input"
            style={{ width: '150px' }}
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
          <input
            className="input"
            style={{ width: '140px' }}
            placeholder="UserID"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
          />
          <input
            className="input"
            style={{ width: '140px' }}
            placeholder="GroupID"
            value={groupId}
            onChange={(e) => setGroupId(e.target.value)}
          />
        </div>
      </div>

      {error && <div className="card p-4 text-danger">{error}</div>}

      <div className="card p-0 overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-muted text-sm">{t('common.loading')}</div>
        ) : entries.length === 0 ? (
          <div className="p-8 text-center text-muted text-sm">{t('common.empty')}</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted">
                <th className="px-3 py-2">{t('observability.audit.col.time')}</th>
                <th className="px-3 py-2">{t('observability.audit.col.summary')}</th>
                <th className="px-3 py-2">{t('observability.audit.col.user')}</th>
                <th className="px-3 py-2">{t('observability.audit.col.trace')}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, i) => {
                const key = `${i}-${entry.timestamp}`;
                const isExpanded = expanded === key;
                const color = AUDIT_TYPE_COLORS[entry.auditType] ?? '#6b7280';
                return (
                  <Fragment key={key}>
                    <tr
                      className={`border-b cursor-pointer ${isExpanded ? 'bg-accent-subtle' : 'hover:bg-muted/50'}`}
                      onClick={() => setExpanded(isExpanded ? null : key)}
                    >
                      <td className="px-3 py-2 whitespace-nowrap text-muted num">
                        {formatTime(entry.timestamp)}
                      </td>
                      <td
                        className="px-3 py-2 truncate max-w-md font-medium"
                        style={{ color }}
                        title={entry.auditType === 'evt' ? 'EVT' : 'UA'}
                      >
                        {entry.body}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap mono text-xs">
                        {entry.userId || '-'}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap mono text-xs">
                        {entry.traceId ? (
                          <span className="mono text-xs" title={entry.traceId}>
                            {entry.traceId.slice(0, 8)}…
                          </span>
                        ) : (
                          '-'
                        )}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${key}-detail`}>
                        <td
                          colSpan={4}
                          className="px-3 py-3 bg-white dark:bg-[var(--bg-card)]"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <DetailPanel entry={entry} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
