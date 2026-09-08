import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { useFormDirty } from '../../hooks/useFormDirty';
import { SkillPrebuiltTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { findUnsafeTextField } from '../../utils/safeText';
import { isValidHttpUrl } from '../../utils/url';
import type {
  SkillPrebuiltTemplate,
  SkillPrebuiltTemplateUpdateBody,
} from '../../types';

interface Props {
  open: boolean;
  template: SkillPrebuiltTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

/** 与 skill_prebuilt_template 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
  skill_id: 512,
  package_url: 2048,
  source_id: 64,
  version_id: 128,
} as const;

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

interface FormState {
  template_name: string;
  description: string;
  skill_id: string;
  package_url: string;
  source_id: string;
  version_id: string;
  sha256: string;
}

const empty: FormState = {
  template_name: '',
  description: '',
  skill_id: '',
  package_url: '',
  source_id: '',
  version_id: '',
  sha256: '',
};

function readSha256(data: Record<string, unknown> | null | undefined): string {
  if (!data || typeof data !== 'object') return '';
  const value = data.sha256;
  return typeof value === 'string' ? value : '';
}

export function SkillPrebuiltTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    const packageUrl = template?.package_url || '';
    const next: FormState = template
      ? {
          template_name: clipField(template.template_name, FIELD_MAX_LENGTH.template_name),
          description: clipField(template.description ?? '', FIELD_MAX_LENGTH.description),
          skill_id: clipField(template.skill_id, FIELD_MAX_LENGTH.skill_id),
          package_url: clipField(packageUrl, FIELD_MAX_LENGTH.package_url),
          source_id: clipField(template.source_id ?? '', FIELD_MAX_LENGTH.source_id),
          version_id: clipField(template.version_id ?? '', FIELD_MAX_LENGTH.version_id),
          sha256: clipField(readSha256(template.data ?? undefined), 64),
        }
      : empty;
    setForm(next);
    markClean(next);
  }, [open, template, markClean]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    if (!form.template_name.trim()) {
      toast('warn', t('skillWhitelistTemplate.fieldRequired', { field: t('skillWhitelistTemplate.templateName') }));
      return;
    }
    if (!form.skill_id.trim()) {
      toast('warn', t('skillWhitelistTemplate.fieldRequired', { field: t('skillWhitelistTemplate.skillId') }));
      return;
    }
    const sourceId = form.source_id.trim();
    const versionId = form.version_id.trim();
    const packageUrl = form.package_url.trim();
    const provider = Boolean(sourceId && versionId);
    if (!provider) {
      if (sourceId || versionId) {
        toast('warn', t('skillWhitelistTemplate.providerIncomplete'));
        return;
      }
      if (!packageUrl) {
        toast('warn', t('skillWhitelistTemplate.installPathRequired'));
        return;
      }
      if (!isValidHttpUrl(packageUrl)) {
        toast('warn', t('skillWhitelistTemplate.skillSourceInvalid'));
        return;
      }
    } else if (packageUrl && !isValidHttpUrl(packageUrl)) {
      toast('warn', t('skillWhitelistTemplate.skillSourceInvalid'));
      return;
    }

    const unsafeField = findUnsafeTextField([
      { label: t('skillWhitelistTemplate.templateName'), value: form.template_name },
      { label: t('skillWhitelistTemplate.templateDescription'), value: form.description },
      { label: t('skillWhitelistTemplate.skillId'), value: form.skill_id },
      { label: t('skillWhitelistTemplate.sourceId'), value: form.source_id },
      { label: t('skillWhitelistTemplate.versionId'), value: form.version_id },
    ]);
    if (unsafeField) {
      toast('warn', t('skillWhitelistTemplate.unsafeText', { field: unsafeField }));
      return;
    }

    const sha = form.sha256.trim().toLowerCase();
    if (sha && !/^[0-9a-f]{64}$/.test(sha)) {
      toast('warn', t('skillWhitelistTemplate.sha256Invalid'));
      return;
    }

    const description = form.description.trim() || undefined;
    const data = sha && !provider ? { sha256: sha } : undefined;

    setSaving(true);
    try {
      if (template) {
        // 空 URL 省略不发：后端会把 "" 收成 None，但未改字段时不应误清已有 URL。
        // 仅在原先有 URL、现在要清空时发 null。
        const patch: SkillPrebuiltTemplateUpdateBody = {
          template_name: form.template_name.trim(),
          description,
          skill_id: form.skill_id.trim(),
          source_id: sourceId || '',
          version_id: versionId || '',
          data,
        };
        if (packageUrl) {
          patch.package_url = packageUrl;
        } else if (template.package_url) {
          patch.package_url = null;
        }
        await SkillPrebuiltTemplateApi.update(template.template_id, patch);
      } else {
        await SkillPrebuiltTemplateApi.create({
          template_name: form.template_name.trim(),
          description,
          skill_id: form.skill_id.trim(),
          package_url: packageUrl || undefined,
          source_id: sourceId || undefined,
          version_id: versionId || undefined,
          enabled: true,
          data,
        });
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={template ? t('skillWhitelistTemplate.edit') : t('skillWhitelistTemplate.new')}
      onClose={onClose}
      dirty={isDirty(form)}
      size="lg"
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" onClick={submit} disabled={saving}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="md:col-span-2">
          <FieldLabel required>{t('skillWhitelistTemplate.templateName')}</FieldLabel>
          <LimitedTextInput
            value={form.template_name}
            maxLength={FIELD_MAX_LENGTH.template_name}
            onChange={(v) => update('template_name', v)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('skillWhitelistTemplate.templateDescription')}</FieldLabel>
          <LimitedTextInput
            value={form.description}
            maxLength={FIELD_MAX_LENGTH.description}
            onChange={(v) => update('description', v)}
          />
        </div>
        <div className="md:col-span-2 text-xs text-muted">{t('skillWhitelistTemplate.modeHint')}</div>
        <div>
          <FieldLabel required>{t('skillWhitelistTemplate.skillId')}</FieldLabel>
          <LimitedTextInput
            value={form.skill_id}
            maxLength={FIELD_MAX_LENGTH.skill_id}
            onChange={(v) => update('skill_id', v)}
          />
        </div>
        <div>
          <FieldLabel>{t('skillWhitelistTemplate.sourceId')}</FieldLabel>
          <LimitedTextInput
            value={form.source_id}
            maxLength={FIELD_MAX_LENGTH.source_id}
            onChange={(v) => update('source_id', v)}
            placeholder={t('skillWhitelistTemplate.sourceIdHint')}
          />
        </div>
        <div>
          <FieldLabel>{t('skillWhitelistTemplate.versionId')}</FieldLabel>
          <LimitedTextInput
            value={form.version_id}
            maxLength={FIELD_MAX_LENGTH.version_id}
            onChange={(v) => update('version_id', v)}
            placeholder={t('skillWhitelistTemplate.versionIdHint')}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('skillWhitelistTemplate.packageUrl')}</FieldLabel>
          <LimitedTextInput
            value={form.package_url}
            maxLength={FIELD_MAX_LENGTH.package_url}
            onChange={(v) => update('package_url', v)}
            placeholder={t('skillWhitelistTemplate.packageUrlHint')}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('skillWhitelistTemplate.sha256')}</FieldLabel>
          <LimitedTextInput
            value={form.sha256}
            maxLength={64}
            onChange={(v) => update('sha256', v)}
            placeholder={t('skillWhitelistTemplate.sha256Hint')}
          />
        </div>
      </div>
    </Modal>
  );
}
