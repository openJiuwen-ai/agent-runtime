import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfigRefreshCostHint } from '../../components/ConfigRefreshCostHint';
import { JsonField, useInvalidJsonChecker } from '../../components/JsonField';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { useFormDirty } from '../../hooks/useFormDirty';
import { ApiError, McpTemplateApi } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type {
  McpEntry,
  McpRemoteTransport,
  McpTemplate,
  McpTemplateCreateBody,
  McpTemplateUpdateBody,
} from '../../types';
import { safeStringify } from '../../utils/format';
import { jsonContentForValidation } from '../../utils/jsonExample';
import { findUnsafeTextField } from '../../utils/safeText';
import { isValidHttpUrl } from '../../utils/url';

interface Props {
  open: boolean;
  template: McpTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
  mcp_name: 128,
  url: 2048,
} as const;

const TRANSPORT_OPTIONS: McpRemoteTransport[] = ['http', 'streamable-http', 'sse'];
const DEFAULT_TRANSPORT: McpRemoteTransport = 'http';

interface FormState {
  template_name: string;
  description: string;
  mcp_name: string;
  transport: McpRemoteTransport;
  url: string;
  headers: string;
  timeout_s: string;
}

const empty: FormState = {
  template_name: '',
  description: '',
  mcp_name: '',
  transport: DEFAULT_TRANSPORT,
  url: '',
  headers: '',
  timeout_s: '',
};

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required ? <span className="text-danger ml-0.5" aria-hidden>*</span> : null}
    </label>
  );
}

function normalizeTransport(raw: unknown): McpRemoteTransport {
  const value = String(raw ?? '').trim().toLowerCase();
  if (value === 'sse') return 'sse';
  if (value === 'streamable_http' || value === 'streamable-http') return 'streamable-http';
  if (value === 'http') return 'http';
  return DEFAULT_TRANSPORT;
}

function headersFromEntry(entry: McpEntry): string {
  const headers =
    (entry.headers && typeof entry.headers === 'object' ? entry.headers : null) ??
    (entry.auth_headers && typeof entry.auth_headers === 'object' ? entry.auth_headers : null);
  if (!headers || Object.keys(headers).length === 0) return '';
  return safeStringify(headers, 2);
}

/** 与 checkJson / JsonField 一致：先剥 // 注释与示例标识，再 parse。 */
function parseHeadersObject(raw: string): Record<string, unknown> | null | undefined {
  const content = jsonContentForValidation(raw);
  if (!content) return null;
  try {
    const parsed: unknown = JSON.parse(content);
    if (parsed == null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return undefined;
    }
    return parsed as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

function buildMcpEntry(form: FormState, headers: Record<string, unknown> | null): McpEntry {
  const entry: McpEntry = {
    name: form.mcp_name.trim(),
    transport: form.transport,
    url: form.url.trim(),
  };
  if (headers && Object.keys(headers).length > 0) {
    entry.headers = Object.fromEntries(
      Object.entries(headers).map(([key, value]) => [key, value == null ? '' : String(value)]),
    );
  }
  const timeoutRaw = form.timeout_s.trim();
  if (timeoutRaw) {
    const timeout = Number(timeoutRaw);
    if (Number.isFinite(timeout) && timeout > 0) {
      entry.timeout_s = timeout;
    }
  }
  return entry;
}

export function McpTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const checkJson = useInvalidJsonChecker();
  const { markClean, isDirty } = useFormDirty(open);
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    const next: FormState = template
      ? {
          template_name: template.template_name,
          description: template.description ?? '',
          mcp_name: String(template.mcp_entry?.name ?? ''),
          transport: normalizeTransport(template.mcp_entry?.transport),
          url: String(template.mcp_entry?.url ?? ''),
          headers: headersFromEntry(template.mcp_entry ?? { name: '', transport: '', url: '' }),
          timeout_s:
            template.mcp_entry?.timeout_s != null && Number(template.mcp_entry.timeout_s) > 0
              ? String(template.mcp_entry.timeout_s)
              : '',
        }
      : empty;
    setForm(next);
    markClean(next);
  }, [open, template, markClean]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const submit = async () => {
    const required = [
      ['templateName', form.template_name],
      ['mcpName', form.mcp_name],
      ['url', form.url],
    ] as const;
    const missing = required.find(([, value]) => !value.trim());
    if (missing) {
      toast(
        'warn',
        t('mcpTemplate.fieldRequired', { field: t(`mcpTemplate.${missing[0]}`) }),
      );
      return;
    }
    if (!isValidHttpUrl(form.url)) {
      toast('warn', t('mcpTemplate.urlInvalid'));
      return;
    }
    if (form.timeout_s.trim()) {
      const timeout = Number(form.timeout_s.trim());
      if (!Number.isFinite(timeout) || timeout <= 0) {
        toast('warn', t('mcpTemplate.timeoutInvalid'));
        return;
      }
    }
    const headersErr = checkJson(form.headers);
    if (headersErr) {
      toast('danger', headersErr);
      return;
    }
    const parsedHeaders = parseHeadersObject(form.headers);
    if (parsedHeaders === undefined) {
      toast('warn', t('mcpTemplate.headersMustBeObject'));
      return;
    }
    const unsafeField = findUnsafeTextField([
      { label: t('mcpTemplate.templateName'), value: form.template_name },
      { label: t('mcpTemplate.templateDescription'), value: form.description },
      { label: t('mcpTemplate.mcpName'), value: form.mcp_name },
    ]);
    if (unsafeField) {
      toast('warn', t('mcpTemplate.unsafeText', { field: unsafeField }));
      return;
    }

    const body: McpTemplateCreateBody | McpTemplateUpdateBody = {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      mcp_entry: buildMcpEntry(form, parsedHeaders),
    };

    setSaving(true);
    try {
      if (template) {
        await McpTemplateApi.update(template.template_id, body);
      } else {
        await McpTemplateApi.create({ ...body, enabled: true } as McpTemplateCreateBody);
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (error) {
      toast(
        'danger',
        t('errors.saveFailed', {
          detail: error instanceof ApiError ? error.detail : (error as Error).message,
        }),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={template ? t('mcpTemplate.edit') : t('mcpTemplate.new')}
      onClose={onClose}
      dirty={isDirty(form)}
      size="lg"
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" onClick={() => void submit()} disabled={saving}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <FieldLabel required>{t('mcpTemplate.templateName')}</FieldLabel>
          <LimitedTextInput
            value={form.template_name}
            maxLength={FIELD_MAX_LENGTH.template_name}
            onChange={(value) => update('template_name', value)}
          />
        </div>
        <div>
          <FieldLabel required>{t('mcpTemplate.mcpName')}</FieldLabel>
          <LimitedTextInput
            value={form.mcp_name}
            maxLength={FIELD_MAX_LENGTH.mcp_name}
            onChange={(value) => update('mcp_name', value)}
            placeholder={t('mcpTemplate.mcpNameHint')}
          />
        </div>
        <div>
          <FieldLabel required>{t('mcpTemplate.transport')}</FieldLabel>
          <select
            className="select"
            value={form.transport}
            onChange={(event) => update('transport', event.target.value as McpRemoteTransport)}
          >
            {TRANSPORT_OPTIONS.map((transport) => (
              <option key={transport} value={transport}>
                {transport}
              </option>
            ))}
          </select>
        </div>
        <div>
          <FieldLabel>{t('mcpTemplate.timeoutS')}</FieldLabel>
          <input
            className="input"
            type="number"
            min={0}
            step="any"
            value={form.timeout_s}
            placeholder={t('mcpTemplate.timeoutSHint')}
            onChange={(event) => update('timeout_s', event.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel required>{t('mcpTemplate.url')}</FieldLabel>
          <LimitedTextInput
            value={form.url}
            maxLength={FIELD_MAX_LENGTH.url}
            onChange={(value) => update('url', value)}
            placeholder={t('mcpTemplate.urlHint')}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('mcpTemplate.templateDescription')}</FieldLabel>
          <LimitedTextInput
            value={form.description}
            maxLength={FIELD_MAX_LENGTH.description}
            onChange={(value) => update('description', value)}
          />
        </div>
        <div className="md:col-span-2">
          <JsonField
            label={t('mcpTemplate.headers')}
            hint={t('mcpTemplate.headersHint')}
            value={form.headers}
            onChange={(value) => update('headers', value)}
            placeholder={t('mcpTemplate.headersHint')}
            rows={5}
          />
        </div>
      </div>
      {template ? <ConfigRefreshCostHint scope="template" /> : null}
    </Modal>
  );
}
