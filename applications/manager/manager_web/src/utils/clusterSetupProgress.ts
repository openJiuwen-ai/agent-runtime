import type { InstanceDetail } from '../types';
import type { InstanceAgentResource, InstanceGrant, InstanceServiceResource } from '../services/api';
import { allComponentsConnected } from './overview';

export type ClusterSetupSection = 'access' | 'service-resources' | 'agent-resources' | 'status';

type TimedConfiguration = { enabled: boolean; expires_at?: string | null };

/** Manager timestamps without an offset represent UTC, as in the backend grant checks. */
export function isActiveConfiguration(row: TimedConfiguration, now = Date.now()): boolean {
  if (!row.enabled) return false;
  if (!row.expires_at) return true;
  const timestamp = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(row.expires_at)
    ? row.expires_at
    : `${row.expires_at}Z`;
  const expires = Date.parse(timestamp);
  return Number.isFinite(expires) && expires > now;
}

/** Counts saved active allow rules, not a promise that any specific identity can log in. */
export function activeAdmissionRules(rows: InstanceGrant[], now = Date.now()): InstanceGrant[] {
  const active = rows.filter(row => isActiveConfiguration(row, now));
  const denied = new Set(active.filter(row => row.login_policy === 'deny').map(row => `${row.subject_type}:${row.subject_id}`));
  return active.filter(row => row.login_policy === 'allow' && !denied.has(`${row.subject_type}:${row.subject_id}`));
}

export interface ClusterSetupProgress {
  instance: InstanceDetail;
  admission: { users: number; orgs: number; total: number };
  runtime: { total: number };
  agents: { total: number; items: InstanceAgentResource[] };
  /** All three kinds of bindings exist. This does not validate scope intersection or a conversation. */
  configurationComplete: boolean;
  connected: boolean;
  nextSection: ClusterSetupSection;
  completedConfigurations: number;
}

export function deriveClusterSetupProgress(input: {
  instance: InstanceDetail;
  users: InstanceGrant[];
  orgs: InstanceGrant[];
  agents: InstanceAgentResource[];
  runtimes: InstanceServiceResource[];
}, now = Date.now()): ClusterSetupProgress {
  const users = activeAdmissionRules(input.users, now).length;
  const orgs = activeAdmissionRules(input.orgs, now).length;
  const agents = input.agents.filter(row => isActiveConfiguration(row, now));
  const runtimes = input.runtimes.filter(row => isActiveConfiguration(row, now));
  const admission = { users, orgs, total: users + orgs };
  const completedConfigurations = [admission.total, runtimes.length, agents.length].filter(Boolean).length;
  const nextSection: ClusterSetupSection = !admission.total ? 'access'
    : !runtimes.length ? 'service-resources'
      : !agents.length ? 'agent-resources'
        : 'status';
  return {
    instance: input.instance,
    admission,
    runtime: { total: runtimes.length },
    agents: { total: agents.length, items: agents },
    configurationComplete: completedConfigurations === 3,
    connected: allComponentsConnected(input.instance),
    nextSection,
    completedConfigurations,
  };
}
