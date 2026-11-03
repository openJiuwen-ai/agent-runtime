export interface RuntimeBindingDraft {
  addTemplateId: string;
  addResourceName: string;
  addResourceDesc: string;
  addPriority: number | null;
  addMatchExpr: string;
  addExpiresAt: string;
}

export const emptyRuntimeBindingDraft: RuntimeBindingDraft = {
  addTemplateId: '', addResourceName: '', addResourceDesc: '',
  addPriority: 0, addMatchExpr: '', addExpiresAt: '',
};

/** Session-only handoffs are isolated by both signed-in user and cluster. */
export function runtimeBindingDraftKey(userId: string, clusterId: string): string {
  return `manager:runtime-binding-handoff:${JSON.stringify([userId, clusterId])}`;
}

export function readRuntimeBindingDraft(storage: Pick<Storage, 'getItem'>, key: string): RuntimeBindingDraft | null {
  try {
    const value: unknown = JSON.parse(storage.getItem(key) || 'null');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const candidate = value as Record<string, unknown>;
    const textFields = ['addTemplateId', 'addResourceName', 'addResourceDesc', 'addMatchExpr', 'addExpiresAt'] as const;
    if (!textFields.every(field => typeof candidate[field] === 'string')) return null;
    if (candidate.addPriority !== null && (typeof candidate.addPriority !== 'number' || !Number.isFinite(candidate.addPriority))) return null;
    return {
      addTemplateId: candidate.addTemplateId as string,
      addResourceName: candidate.addResourceName as string,
      addResourceDesc: candidate.addResourceDesc as string,
      addPriority: candidate.addPriority as number | null,
      addMatchExpr: candidate.addMatchExpr as string,
      addExpiresAt: candidate.addExpiresAt as string,
    };
  } catch {
    return null;
  }
}

/** Keep an empty/invalid priority invalid when JSON restores the local draft. */
export function writeRuntimeBindingDraft(storage: Pick<Storage, 'setItem'>, key: string, draft: RuntimeBindingDraft): boolean {
  try {
    storage.setItem(key, JSON.stringify({ ...draft, addPriority: Number.isFinite(draft.addPriority) ? draft.addPriority : null }));
    return true;
  } catch { return false; }
}

type RuntimeTemplateChoice = { template_id: string; template_name: string; enabled: boolean };

/** Prefill a chosen template without replacing the user's name, scope or priority. */
export function applyRuntimeTemplateChoice(
  draft: RuntimeBindingDraft, templateId: string, catalog: RuntimeTemplateChoice[],
): { draft: RuntimeBindingDraft; error: 'missing' | 'disabled' | null } {
  const selected = catalog.find(item => item.template_id === templateId);
  if (!selected || selected.enabled === false) {
    return { draft: { ...draft, addTemplateId: '' }, error: selected ? 'disabled' : 'missing' };
  }
  return {
    draft: { ...draft, addTemplateId: selected.template_id, addResourceName: draft.addResourceName.trim() ? draft.addResourceName : selected.template_name.slice(0, 128) },
    error: null,
  };
}

/** Consume only this form's action; the pending Agent selection remains intact. */
export function consumeRuntimeBindingAction(path: string, params: URLSearchParams): string {
  const query = new URLSearchParams(params);
  query.delete('create');
  query.delete('runtimeTemplate');
  return `${path}${query.size ? `?${query}` : ''}`;
}
