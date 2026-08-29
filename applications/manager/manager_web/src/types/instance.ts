export interface InstanceSummary {
  jiuwenclaw_id: string;
  jiuwenclaw_name: string;
  namespace: string;
  space_id: string;
  gateway_config_host: string;
  gateway_status: string;
  gateway_last_alive?: string | null;
  runtime_config_host: string;
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
  gateway_config_host: string;
  runtime_config_host: string;
  data?: Record<string, unknown>;
}

export interface ManagerWsStatus {
  enabled: boolean;
  running: boolean;
  host?: string;
  port?: number;
  registered_jiuwenclaw_ids: string[];
  pid: number;
}
