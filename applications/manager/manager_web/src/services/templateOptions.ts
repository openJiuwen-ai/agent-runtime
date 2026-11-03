import { collectPages } from '../utils/overview';
import {
  EmbeddingTemplateApi,
  ExtensionTemplateApi,
  McpTemplateApi,
  ModelTemplateApi,
  PermissionsTemplateApi,
  A2AAccessPolicyTemplateApi,
  SkillPrebuiltTemplateApi,
} from './api';
export interface TemplateOption {
  template_id: string;
  label: string;
}

export async function loadTemplateOptions(): Promise<Record<string, TemplateOption[]>> {
  const [models, embeddings, skills, extensions, mcps, permissions, a2aPolicies] = await Promise.all([
    collectPages((page, page_size) => ModelTemplateApi.list({ page, page_size, enabled: true })),
    collectPages((page, page_size) => EmbeddingTemplateApi.list({ page, page_size, enabled: true })),
    collectPages((page, page_size) => SkillPrebuiltTemplateApi.list({ page, page_size, enabled: true })),
    collectPages((page, page_size) => ExtensionTemplateApi.list({ page, page_size, enabled: true })),
    collectPages((page, page_size) => McpTemplateApi.list({ page, page_size, enabled: true })),
    collectPages((page, page_size) => PermissionsTemplateApi.list({ page, page_size, enabled: true })),
    collectPages((page, page_size) => A2AAccessPolicyTemplateApi.list({ page, page_size, enabled: true })),
  ]);

  const toOpt = (id: string, name: string): TemplateOption => ({
    template_id: id,
    label: name ? `${name} (${id})` : id,
  });

  const modelOptions = models.map((m) => toOpt(m.template_id, m.template_name));
  const bySlot: Record<string, TemplateOption[]> = {};
  for (const slot of [
    'default_model',
    'video_model',
    'audio_model',
    'vision_model',
    'image_gen_model',
  ] as const) {
    bySlot[slot] = modelOptions;
  }

  bySlot.embedding_model = embeddings.map((t) =>
    toOpt(t.template_id, t.template_name),
  );
  bySlot.skill_prebuilt = skills.map((t) => toOpt(t.template_id, t.template_name));
  bySlot.extension_config = extensions.map((t) => toOpt(t.template_id, t.template_name));
  bySlot.mcp = mcps.map((t) => toOpt(t.template_id, t.template_name));
  bySlot.permissions = permissions.map((t) => toOpt(t.template_id, t.template_name));
  bySlot.a2a_access_policy = a2aPolicies.map((t) =>
    toOpt(t.policy_id, `${t.policy_name} · ${t.mode}`),
  );
  return bySlot;
}

