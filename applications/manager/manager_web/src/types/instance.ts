export interface InstanceSummary {
  jiuwenclaw_id: string;
  jiuwenclaw_name: string;
  namespace: string;
  space_id: string;
  gateway_host: string;
  gateway_status: string;
  gateway_last_alive?: string | null;
  runtime_host: string;
  runtime_status: string;
  runtime_last_alive?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface InstanceDetail extends InstanceSummary {
  description?: string | null;
  data?: Record<string, unknown> | null;
  created_by: string;
  updated_by?: string | null;
}

export interface CreateInstanceBody {
  jiuwenclaw_name: string;
  description?: string;
  namespace?: string;
  space_id?: string;
  created_by?: string;
  gateway_host: string;
  runtime_host: string;
  data?: Record<string, unknown>;
}

export interface UpdateInstanceBody {
  jiuwenclaw_name?: string;
  description?: string;
  namespace?: string;
  space_id?: string;
  gateway_host?: string;
  runtime_host?: string;
  data?: Record<string, unknown>;
  updated_by?: string;
}

export interface ManagerWsStatus {
  enabled: boolean;
  running: boolean;
  host?: string;
  port?: number;
  registered_jiuwenclaw_ids: string[];
  pid: number;
}

export type InstanceComponentType = 'gateway' | 'runtime' | 'agentserver';

export interface InstanceComponentPod {
  name: string;
  phase: string;
  ready: boolean;
  restarts: number;
  started_at?: string | null;
  cpu?: string | null;
  memory?: string | null;
  cpu_limit?: string | null;
  memory_limit?: string | null;
}

export interface InstanceComponentBlock {
  type: InstanceComponentType;
  status: string;
  last_alive?: string | null;
  pods: InstanceComponentPod[];
  pods_error?: string | null;
  metrics_error?: string | null;
}

export interface InstanceComponentStatus {
  jiuwenclaw_id: string;
  namespace: string;
  k8s_error?: string | null;
  components: InstanceComponentBlock[];
  gateway_host?: string;
  runtime_host?: string;
}
