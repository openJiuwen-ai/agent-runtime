/** 管理面启动部署（/v1/deployments）：任务状态/进度/日志与请求体类型。 */

export type DeploymentStatus =
  | 'pending'
  | 'running'
  | 'success'
  | 'failed'
  | 'cancelled'
  | 'interrupted';

/** deploy.sh 阶段（phase 字段还会短暂出现 pending / finished）。 */
export type DeploymentPhase =
  | 'pending'
  | 'validate'
  | 'render'
  | 'preflight'
  | 'apply'
  | 'finished';

export type DeploymentDeployMode = 'routing' | 'full';

/** GET /v1/deployments/defaults 的 data。 */
export interface DeploymentDefaults {
  /** 默认参数（密码/密钥类值为 "******" 或缺省） */
  env: Record<string, string>;
  tool_version: string;
  deploy_enabled: boolean;
  available: boolean;
  unavailable_reason: string;
}

/** progress.components 的单项（state: start | ok | fail）。 */
export interface DeploymentComponentProgress {
  state: string;
  index: number;
  total: number;
  message?: string;
}

/** progress 字段（deploy.sh JSON 进度事件聚合；日志截断/自动注册提示由 runner 附加）。 */
export interface DeploymentProgress {
  /** phase → start | ok | fail */
  phases: Record<string, string>;
  components: Record<string, DeploymentComponentProgress>;
  current: string | null;
  log_truncated?: boolean;
  auto_register?: string;
}

export interface DeploymentTaskSummary {
  deployment_id: string;
  namespace: string;
  jiuwenclaw_name: string;
  deploy_mode: string;
  status: DeploymentStatus | string;
  phase: DeploymentPhase | string;
  instance_jid: string | null;
  error_summary: string | null;
  created_by: string;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface DeploymentTaskDetail extends DeploymentTaskSummary {
  progress: DeploymentProgress;
  /** 任务参数快照（env 已掩码） */
  params: { env?: Record<string, string> } & Record<string, unknown>;
  tool_version: string | null;
  exit_code: number | null;
  cancel_requested: boolean;
}

export interface DeploymentLogItem {
  seq: number;
  content: string;
  created_at: string | null;
}

/** GET /v1/deployments/{id}/logs 的 data（增量游标）。 */
export interface DeploymentLogChunk {
  items: DeploymentLogItem[];
  last_seq: number;
  truncated: boolean;
}

export interface CreateDeploymentBody {
  namespace: string;
  jiuwenclaw_name: string;
  deploy_mode: DeploymentDeployMode;
  env?: Record<string, string>;
  wait_timeout_secs?: number;
}

export interface DeploymentCreateResult {
  deployment_id: string;
  status: string;
}
