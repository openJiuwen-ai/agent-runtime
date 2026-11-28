import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from './Modal';
import { EditorPresentationProvider, useEditorPresentation } from './InlineEditorContext';
import {
  ApiError, ModelTemplateApi, EmbeddingTemplateApi, SkillPrebuiltTemplateApi,
  ExpertPackageTemplateApi, ExtensionTemplateApi, McpTemplateApi,
  PermissionsTemplateApi, A2AAccessPolicyTemplateApi, ExecutionGuardTemplateApi,
} from '../services/api';
import type {
  ModelTemplate, EmbeddingTemplate, SkillPrebuiltTemplate, ExpertPackageTemplate,
  ExtensionConfigTemplate, McpTemplate, PermissionsTemplate, A2AAccessPolicyTemplate, ExecutionGuardTemplate,
} from '../types';
import type { TemplateRefEditorSlot } from '../utils/templateRef';
import { ModelTemplateModal } from '../pages/templates/ModelTemplateModal';
import { EmbeddingTemplateModal } from '../pages/templates/EmbeddingTemplateModal';
import { SkillPrebuiltTemplateModal } from '../pages/templates/SkillPrebuiltTemplateModal';
import { ExpertPackageTemplateModal } from '../pages/templates/ExpertPackageTemplateModal';
import { ExtensionTemplateModal } from '../pages/templates/ExtensionTemplateModal';
import { McpTemplateModal } from '../pages/templates/McpTemplateModal';
import { ExecutionGuardrailsModal } from '../pages/templates/ExecutionGuardrailsModal';
import { SafetyGuardrailsModal } from '../pages/templates/SafetyGuardrailsModal';
import { A2AAccessPolicyModal } from '../pages/templates/A2AAccessPolicyModal';

type Resource =
  | { kind: 'model'; value: ModelTemplate }
  | { kind: 'embedding'; value: EmbeddingTemplate }
  | { kind: 'skill'; value: SkillPrebuiltTemplate }
  | { kind: 'expert'; value: ExpertPackageTemplate }
  | { kind: 'extension'; value: ExtensionConfigTemplate }
  | { kind: 'mcp'; value: McpTemplate }
  | { kind: 'execution'; value: ExecutionGuardTemplate }
  | { kind: 'permissions'; value: PermissionsTemplate }
  | { kind: 'a2a'; value: A2AAccessPolicyTemplate };

function resourceKind(slot: TemplateRefEditorSlot): Resource['kind'] {
  switch (slot) {
    case 'embedding_model': return 'embedding';
    case 'skill_prebuilt': return 'skill';
    case 'expert_package': return 'expert';
    case 'extension_config': return 'extension';
    case 'mcp': return 'mcp';
    case 'execution_guard': return 'execution';
    case 'permissions': return 'permissions';
    case 'a2a_access_policy': return 'a2a';
    default: return 'model';
  }
}

async function loadResource(kind: Resource['kind'], id: string): Promise<Resource> {
  switch (kind) {
    case 'model': return { kind, value: await ModelTemplateApi.get(id) };
    case 'embedding': return { kind, value: await EmbeddingTemplateApi.get(id) };
    case 'skill': return { kind, value: await SkillPrebuiltTemplateApi.get(id) };
    case 'expert': return { kind, value: await ExpertPackageTemplateApi.get(id) };
    case 'extension': return { kind, value: await ExtensionTemplateApi.get(id) };
    case 'mcp': return { kind, value: await McpTemplateApi.get(id) };
    case 'execution': return { kind, value: await ExecutionGuardTemplateApi.get(id) };
    case 'permissions': return { kind, value: await PermissionsTemplateApi.get(id) };
    case 'a2a': return { kind, value: await A2AAccessPolicyTemplateApi.get(id) };
  }
}

/** Uses the real catalog forms and APIs while keeping the Agent draft in the wizard. */
export function TemplateResourceEditor({ slot, templateId, onClose, onSaved }: {
  slot: TemplateRefEditorSlot;
  templateId?: string;
  onClose: () => void;
  onSaved: (id: string, label: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const parentPresentation = useEditorPresentation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const kind = resourceKind(slot);
  const [resource, setResource] = useState<Resource | null>(null);
  const [mode, setMode] = useState<'create' | 'view' | 'copy'>(templateId ? 'view' : 'create');
  const [loading, setLoading] = useState(!!templateId);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!templateId) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    void loadResource(kind, templateId).then(saved => { if (!cancelled) setResource(saved); })
      .catch(failure => { if (!cancelled) setError(failure instanceof ApiError ? failure.detail : (failure as Error).message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [kind, templateId, revision]);

  if (loading || error) return <EditorPresentationProvider><Modal open title={tr('资源详情', 'Resource details')} onClose={onClose}
    footer={<><ModalCancelButton />{error && <button type="button" className="btn" onClick={() => setRevision(value => value + 1)}>{t('common.refresh')}</button>}</>}>
    <p className={error ? 'text-danger' : 'text-muted'} role={error ? 'alert' : 'status'}>{error || t('common.loading')}</p>
  </Modal></EditorPresentationProvider>;

  const footer = mode === 'view' ? <>
    <ModalCancelButton>{tr('返回 Agent 模板', 'Back to Agent template')}</ModalCancelButton>
    {!parentPresentation.readOnly && <button type="button" className="btn" onClick={() => setMode('copy')}>{tr('复制并自定义', 'Copy and customize')}</button>}
    {!parentPresentation.readOnly && <button type="button" className="btn primary" onClick={() => {
      if (!resource) return;
      if (resource.kind === 'a2a') onSaved(resource.value.policy_id, resource.value.policy_name);
      else onSaved(resource.value.template_id, resource.value.template_name);
    }}>{tr('使用此配置', 'Use this configuration')}</button>}
  </> : undefined;
  const saved = (value: { template_id: string; template_name: string }) => onSaved(value.template_id, value.template_name);
  const props = { open: true, onClose, onSaved: saved };
  let editor;
  switch (kind) {
    case 'model': {
      const source = resource?.kind === kind ? resource.value : null;
      editor = <ModelTemplateModal {...props} template={mode === 'view' ? source : null} copyFrom={mode === 'copy' ? source ?? undefined : undefined} />;
      break;
    }
    case 'embedding': {
      const source = resource?.kind === kind ? resource.value : null;
      editor = <EmbeddingTemplateModal {...props} template={mode === 'view' ? source : null} copyFrom={mode === 'copy' ? source ?? undefined : undefined} />;
      break;
    }
    case 'skill': {
      const source = resource?.kind === kind ? resource.value : null;
      editor = <SkillPrebuiltTemplateModal {...props} template={mode === 'view' ? source : null} copyFrom={mode === 'copy' ? source ?? undefined : undefined} />;
      break;
    }
    case 'expert': {
      const source = resource?.kind === kind ? resource.value : null;
      editor = <ExpertPackageTemplateModal {...props} template={mode === 'view' ? source : null} copyFrom={mode === 'copy' ? source ?? undefined : undefined} onConflict={() => setRevision(value => value + 1)} />;
      break;
    }
    case 'extension': {
      const source = resource?.kind === kind ? resource.value : null;
      editor = <ExtensionTemplateModal {...props} template={mode === 'view' ? source : null} copyFrom={mode === 'copy' ? source ?? undefined : undefined} />;
      break;
    }
    case 'mcp': {
      const source = resource?.kind === kind ? resource.value : null;
      editor = <McpTemplateModal {...props} template={mode === 'view' ? source : null} copyFrom={mode === 'copy' ? source ?? undefined : undefined} />;
      break;
    }
    case 'execution': {
      const source = resource?.kind === kind ? resource.value : null;
      editor = <ExecutionGuardrailsModal {...props} template={mode === 'view' ? source : null} copyFrom={mode === 'copy' ? source ?? undefined : undefined} />;
      break;
    }
    case 'permissions': {
      const source = resource?.kind === kind ? resource.value : null;
      editor = <SafetyGuardrailsModal {...props} template={mode === 'view' ? source : null} copyFrom={mode === 'copy' ? source ?? undefined : undefined} />;
      break;
    }
    case 'a2a': {
      const source = resource?.kind === kind ? resource.value : null;
      editor = <A2AAccessPolicyModal open onClose={onClose} policy={mode === 'view' ? source : null} copyFrom={mode === 'copy' ? source ?? undefined : undefined}
        onSaved={value => onSaved(value.policy_id, value.policy_name)} />;
      break;
    }
  }
  return <EditorPresentationProvider readOnly={mode === 'view'} footer={footer}>{editor}</EditorPresentationProvider>;
}
