import type { InstanceBindOptions, InstanceGrant, Org } from '../services/api';

export type AdmissionKind = 'user' | 'org';
export interface AdmissionDraft extends Required<InstanceBindOptions> {
  kind: AdmissionKind;
  subjectId: string;
}

export const admissionKey = (row: Pick<AdmissionDraft, 'kind' | 'subjectId'>) => JSON.stringify([row.kind, row.subjectId]);

export function grantDraft(grant: InstanceGrant, kind: AdmissionKind): AdmissionDraft {
  return { kind, subjectId: grant.subject_id, login_policy: grant.login_policy === 'deny' ? 'deny' : 'allow', enabled: grant.enabled, expires_at: grant.expires_at };
}

export function sameAdmission(a: AdmissionDraft, b: AdmissionDraft): boolean {
  const time = (value: string | null) => value ? admissionTimestamp(value) : null;
  return a.login_policy === b.login_policy && a.enabled === b.enabled && time(a.expires_at) === time(b.expires_at);
}

export function hasAdmissionChanges(baseline: AdmissionDraft[], draft: AdmissionDraft[]): boolean {
  if (baseline.length !== draft.length) return true;
  const previous = new Map(baseline.map(row => [admissionKey(row), row]));
  return draft.some(row => {
    const before = previous.get(admissionKey(row));
    return !before || !sameAdmission(before, row);
  });
}

export function hasActiveAdmission(draft: AdmissionDraft[], activeUserIds: Set<string>, activeOrgIds: Set<string>, now = Date.now()): boolean {
  return draft.some(row => row.login_policy === 'allow' && row.enabled
    && (!row.expires_at || admissionTimestamp(row.expires_at) > now)
    && (row.kind === 'user' ? activeUserIds : activeOrgIds).has(row.subjectId));
}

export interface AdmissionWriter {
  bind: (kind: AdmissionKind, id: string, options: InstanceBindOptions) => Promise<unknown>;
  update: (kind: AdmissionKind, id: string, options: InstanceBindOptions & { clear_expires_at?: boolean }) => Promise<unknown>;
  remove: (kind: AdmissionKind, id: string) => Promise<unknown>;
}

/** Keep each successful write as the new baseline so a partial failure can be retried safely.
 * Only grants present in the original baseline are removed; unrelated concurrent grants are untouched.
 */
export async function writeAdmissionChanges(baseline: AdmissionDraft[], draft: AdmissionDraft[], writer: AdmissionWriter) {
  const saved = new Map(baseline.map(row => [admissionKey(row), row]));
  const errors: Array<{ row: AdmissionDraft; error: unknown }> = [];
  for (const row of draft) {
    const key = admissionKey(row);
    const before = saved.get(key);
    if (before && sameAdmission(before, row)) continue;
    try {
      const options = { login_policy: row.login_policy, enabled: row.enabled, expires_at: row.expires_at };
      if (before) await writer.update(row.kind, row.subjectId, { ...options, clear_expires_at: !row.expires_at });
      else {
        await writer.bind(row.kind, row.subjectId, options);
        // Bind may skip a pre-existing row. Patch it to make the requested settings explicit.
        await writer.update(row.kind, row.subjectId, { ...options, clear_expires_at: !row.expires_at });
      }
      saved.set(key, { ...row });
    } catch (error) { errors.push({ row, error }); }
  }
  // Avoid revoking access when an intended replacement could not be saved.
  if (errors.length === 0) {
    const wanted = new Set(draft.map(admissionKey));
    for (const row of baseline) {
      if (wanted.has(admissionKey(row))) continue;
      try { await writer.remove(row.kind, row.subjectId); saved.delete(admissionKey(row)); }
      catch (error) { errors.push({ row, error }); }
    }
  }
  return { saved: [...saved.values()], errors };
}

export function localAdmissionTime(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(admissionTimestamp(iso));
  if (Number.isNaN(date.getTime())) return '';
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

/** Offset-free API timestamps represent UTC, matching the server's grant expiry checks. */
export function admissionTimestamp(value: string): number {
  return Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`);
}

export interface OrganizationWriter {
  create: (body: { group_id?: string; display_name: string }) => Promise<Org>;
  update: (id: string, body: { display_name: string }) => Promise<Org>;
  addMember: (id: string, userId: string) => Promise<unknown>;
  removeMember: (id: string, userId: string) => Promise<unknown>;
}

/** Save the global identity before membership edits. Return successful writes even on failure,
 * so retrying after a nested user was created never recreates the parent organization.
 */
export async function writeOrganizationChanges(
  original: Org | undefined,
  body: { group_id?: string; display_name: string },
  baselineMembers: string[],
  desiredMembers: string[],
  writer: OrganizationWriter,
) {
  let organization = original;
  const memberIds = new Set(baselineMembers);
  let error: unknown = null;
  try {
    if (!organization) organization = await writer.create(body);
    else if (body.display_name !== organization.display_name) organization = await writer.update(organization.group_id, { display_name: body.display_name });
    for (const id of desiredMembers) {
      if (memberIds.has(id)) continue;
      await writer.addMember(organization.group_id, id);
      memberIds.add(id);
    }
    const desired = new Set(desiredMembers);
    for (const id of [...memberIds]) {
      if (desired.has(id)) continue;
      await writer.removeMember(organization.group_id, id);
      memberIds.delete(id);
    }
  } catch (cause) { error = cause; }
  return { organization, memberIds: [...memberIds], error };
}
