"""管理面启动部署：部署任务 / 任务日志 / 默认部署参数表。

任务由 manager-server 以子进程代跑 deploy.sh 执行（工具树随镜像分发），
状态机 pending → running → success | failed | cancelled | interrupted；
running 心跳超时（manager 重启/副本死亡）置 interrupted，deploy.sh 幂等可重发。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

# 部署任务状态机（写入前由 service 层校验，此处仅存储）
DEPLOYMENT_STATUS_VALUES = (
    "pending",
    "running",
    "success",
    "failed",
    "cancelled",
    "interrupted",
)

DEPLOYMENT_TASK_TABLE_DEF = TableDefinition(
    table_name="deployment_task",
    columns=[
        ColumnDefinition(
            "deployment_id", "string", length=64, primary_key=True, nullable=False
        ),
        # 目标命名空间（同 ns 同时只允许一个活跃任务，service 层校验）
        ColumnDefinition("namespace", "string", length=64, nullable=False),
        # 部署成功后自动注册实例用的名称
        ColumnDefinition("jiuwenclaw_name", "string", length=128, nullable=False),
        # AGENT_RUNTIME_DEPLOY_MODE（v1 仅 routing | full）
        ColumnDefinition("deploy_mode", "string", length=16, nullable=False),
        ColumnDefinition("status", "string", length=16, nullable=False),
        # 当前阶段：pending | validate | render | preflight | apply | finished
        ColumnDefinition(
            "phase", "string", length=32, nullable=False, default="pending"
        ),
        # 组件时间线：{components: {name: {state,index,total,message}}, current, total}
        ColumnDefinition("progress", "json", nullable=True),
        # 创建请求快照（namespace/deploy_mode/镜像覆盖/副本/外置 DB 等）
        ColumnDefinition("params", "json", nullable=True),
        # 执行时实际使用的默认 env 快照（defaults 表当时的值）
        ColumnDefinition("defaults_snapshot", "json", nullable=True),
        # 工具树版本戳（deploy/openshift/TOOL_VERSION）
        ColumnDefinition("tool_version", "string", length=64, nullable=True),
        # 成功后自动注册出的实例 ID（注册失败为空，见 progress.auto_register）
        ColumnDefinition("instance_jid", "string", length=64, nullable=True),
        ColumnDefinition("exit_code", "int", nullable=True),
        ColumnDefinition("error_summary", "string", length=2048, nullable=True),
        # 取消请求标记（runner 轮询，SIGTERM 进程组）
        ColumnDefinition(
            "cancel_requested", "bool", nullable=False, default=False
        ),
        ColumnDefinition("created_by", "string", length=64, nullable=False),
        ColumnDefinition("created_at", "datetime3", nullable=False),
        ColumnDefinition("updated_at", "datetime3", nullable=False),
        ColumnDefinition("started_at", "datetime3", nullable=True),
        ColumnDefinition("finished_at", "datetime3", nullable=True),
        # runner 心跳（reconcile 据此判定孤儿 running → interrupted）
        ColumnDefinition("heartbeat_at", "datetime3", nullable=True),
    ],
    indexes=[
        IndexDefinition(["status"], unique=False),
        IndexDefinition(["namespace", "status"], unique=False),
        IndexDefinition(["created_at"], unique=False),
    ],
)

DEPLOYMENT_LOG_TABLE_DEF = TableDefinition(
    table_name="deployment_log",
    columns=[
        ColumnDefinition(
            "id", "bigint", primary_key=True, autoincrement=True, nullable=False
        ),
        ColumnDefinition("deployment_id", "string", length=64, nullable=False),
        # 行号（单调递增，前端 after_seq 增量拉取游标）
        ColumnDefinition("seq", "int", nullable=False),
        # 单行截断到 2048 字符；总量超限后停止落库并写 truncated 标记行
        ColumnDefinition("content", "string", length=2048, nullable=False),
        ColumnDefinition("created_at", "datetime3", nullable=False),
    ],
    indexes=[
        IndexDefinition(["deployment_id", "seq"], unique=True),
    ],
)

# 默认部署参数（单行表，id 固定 default）：镜像清单/外置 DB/Redis 等公共默认值，
# 首次读取时从工具树 .env.example 播种
DEPLOYMENT_DEFAULT_ENV_TABLE_DEF = TableDefinition(
    table_name="deployment_default_env",
    columns=[
        ColumnDefinition("id", "string", length=32, primary_key=True, nullable=False),
        ColumnDefinition("env_json", "json", nullable=False),
        ColumnDefinition("updated_at", "datetime3", nullable=False),
        ColumnDefinition("updated_by", "string", length=64, nullable=True),
    ],
)
