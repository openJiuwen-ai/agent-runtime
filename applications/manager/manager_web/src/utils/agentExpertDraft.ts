export interface AgentExpertSelection {
  kind: 'agent_template' | 'agent_group';
  id: string;
  display_name?: string;
  version?: string;
  demo?: boolean;
}

const storageKey = (accountId: string, templateId: string) =>
  `manager:agent-expert-preview:v1:${encodeURIComponent(accountId)}:${encodeURIComponent(templateId)}`;

/** Whitelist preview fields so runtime configuration or credentials never enter this store. */
export function normalizeAgentExpertSelections(value: unknown): AgentExpertSelection[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value.flatMap(raw => {
    if (!raw || typeof raw !== 'object' || !['agent_template', 'agent_group'].includes(raw.kind)
      || typeof raw.id !== 'string' || !raw.id.trim() || raw.id.length > 128 || /[/\\\r\n]/.test(raw.id) || ['.', '..'].includes(raw.id.trim())) return [];
    const item: AgentExpertSelection = { kind: raw.kind, id: raw.id.trim() };
    if (typeof raw.display_name === 'string') item.display_name = raw.display_name.slice(0, 512);
    if (typeof raw.version === 'string') item.version = raw.version.slice(0, 128);
    if (raw.demo === true) item.demo = true;
    const key = `${item.demo ? 'demo' : 'hub'}:${item.kind}:${item.id}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [item];
  });
}

export function readAgentExpertDraft(storage: Pick<Storage, 'getItem'>, accountId: string, templateId: string): AgentExpertSelection[] {
  if (!accountId.trim() || !templateId.trim()) return [];
  try {
    const saved = JSON.parse(storage.getItem(storageKey(accountId, templateId)) || 'null');
    return saved?.schema_version === 1 ? normalizeAgentExpertSelections(saved.selections) : [];
  } catch { return []; }
}

export function writeAgentExpertDraft(storage: Pick<Storage, 'setItem'>, accountId: string, templateId: string, selections: AgentExpertSelection[]): boolean {
  if (!accountId.trim() || !templateId.trim()) return false;
  try {
    storage.setItem(storageKey(accountId, templateId), JSON.stringify({ schema_version: 1, selections: normalizeAgentExpertSelections(selections) }));
    return true;
  } catch { return false; }
}
