"""模板表定义：model_template、embedding_template、extension_config_template、
skill_whitelist_template、permissions_template、service_config_container、service_config_template、
agent_template（id 自增主键；对外引用 template_id / container_id UUID）。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

MODEL_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="model_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("model_type", "json", nullable=False),
        ColumnDefinition("model_tags", "json", nullable=True),
        ColumnDefinition("api_base", "string", length=512, nullable=False),
        ColumnDefinition("api_key", "string", length=4096, nullable=False),
        ColumnDefinition("model_id", "string", length=128, nullable=False),
        ColumnDefinition("model_provider", "string", length=64, nullable=False),
        ColumnDefinition("parameters", "json", nullable=True),
        ColumnDefinition("timeout", "integer", nullable=False, default=60),
        ColumnDefinition("retry_count", "integer", nullable=False, default=3),
        ColumnDefinition("enable_streaming", "boolean", nullable=False, default=True),
        ColumnDefinition("enable_function_calling", "boolean", nullable=False, default=True),
        ColumnDefinition("verify_ssl", "boolean", nullable=False, default=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
    ],
)

EMBEDDING_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="embedding_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("embed_tags", "json", nullable=True),
        ColumnDefinition("api_base", "string", length=512, nullable=False),
        ColumnDefinition("api_key", "string", length=4096, nullable=False),
        ColumnDefinition("model_id", "string", length=128, nullable=False),
        ColumnDefinition("model_provider", "string", length=64, nullable=False),
        ColumnDefinition("parameters", "json", nullable=True),
        ColumnDefinition("client_config", "json", nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
    ],
)

EXTENSION_CONFIG_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="extension_config_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("component", "string", length=32, nullable=False),
        ColumnDefinition("hook_type", "string", length=32, nullable=False),
        ColumnDefinition("hook_config", "json", nullable=False),
        ColumnDefinition("custom_config", "json", nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
    ],
)

SKILL_WHITELIST_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="skill_whitelist_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("skill_id", "string", length=512, nullable=False),
        ColumnDefinition("skill_version", "string", length=64, nullable=False),
        ColumnDefinition("skill_source", "string", length=2048, nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
    ],
)

PERMISSIONS_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="permissions_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("body", "json", nullable=False),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
    ],
)

# 容器规格目录（与 Runtime service_config_container 对齐；平台全局，不带 jiuwenclaw_id）。
# 段落 JSON 列为内部规范形（snake 键）；模板经 main_container_id / sidecar_container_ids 引用。

MCP_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="mcp_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("mcp_entry", "json", nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
    ],
)

SERVICE_CONFIG_CONTAINER_TABLE_DEF = TableDefinition(
    table_name="service_config_container",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("container_id", "string", length=100, nullable=False),
        ColumnDefinition("name", "string", length=128, nullable=False),
        ColumnDefinition("image", "string", length=512, nullable=False),
        ColumnDefinition(
            "image_pull_policy",
            "string",
            length=64,
            nullable=False,
            default="IfNotPresent",
        ),
        ColumnDefinition("ports", "json", nullable=True),
        ColumnDefinition("env", "json", nullable=True),
        ColumnDefinition("env_from", "json", nullable=True),
        ColumnDefinition("resources", "json", nullable=True),
        ColumnDefinition("volume_mounts", "json", nullable=True),
        ColumnDefinition("security_context", "json", nullable=True),
        ColumnDefinition("readiness_probe", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["container_id"], unique=True),
    ],
)

SERVICE_CONFIG_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="service_config_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False, default=""),
        ColumnDefinition("description", "string", length=512, nullable=True),
        # 以下容器级列与 Runtime 对齐：三段式引用落地前仍保留内联形态（读兼容）；
        # 新写入可同时填 main_container_id / sidecar_container_ids / volumes。
        ColumnDefinition("agent_image", "string", length=512, nullable=False, default=""),
        ColumnDefinition("namespace", "string", length=128, nullable=False, default="default"),
        ColumnDefinition("node_name", "string", length=128, nullable=True),
        ColumnDefinition("run_as_user", "integer", nullable=True),
        ColumnDefinition("run_as_group", "integer", nullable=True),
        ColumnDefinition("pod_name", "string", length=128, nullable=False, default="agentserver"),
        ColumnDefinition("container_name", "string", length=128, nullable=False, default="agent"),
        ColumnDefinition("container_port", "integer", nullable=False, default=8080),
        ColumnDefinition("port_name", "string", length=64, nullable=False, default="http"),
        ColumnDefinition("sse_port", "integer", nullable=False, default=8080),
        ColumnDefinition("sse_path", "string", length=128, nullable=False, default="/sse"),
        ColumnDefinition("health_path", "string", length=128, nullable=False, default="/health"),
        ColumnDefinition("agent_env", "json", nullable=True),
        ColumnDefinition(
            "image_pull_policy",
            "string",
            length=64,
            nullable=False,
            default="IfNotPresent",
        ),
        ColumnDefinition("kubeconfig", "string", length=512, nullable=True),
        ColumnDefinition("readiness_initial_delay", "integer", nullable=False, default=5),
        ColumnDefinition("readiness_period", "integer", nullable=False, default=5),
        ColumnDefinition("ready_timeout", "integer", nullable=False, default=300),
        ColumnDefinition("ready_poll_interval", "integer", nullable=False, default=2),
        ColumnDefinition("nfs_server", "string", length=256, nullable=True),
        ColumnDefinition("nfs_path", "string", length=256, nullable=True),
        ColumnDefinition("nfs_mount_path", "string", length=256, nullable=True),
        ColumnDefinition("agent_cpu_request", "string", length=32, nullable=True),
        ColumnDefinition("agent_memory_request", "string", length=32, nullable=True),
        ColumnDefinition("agent_cpu_limit", "string", length=32, nullable=True),
        ColumnDefinition("agent_memory_limit", "string", length=32, nullable=True),
        ColumnDefinition("sidecars", "json", nullable=True),
        ColumnDefinition("agent_host_path_mounts", "json", nullable=True),
        ColumnDefinition("agent_configmap_mounts", "json", nullable=True),
        ColumnDefinition("agent_pvc_mounts", "json", nullable=True),
        ColumnDefinition("main_container_id", "string", length=100, nullable=True),
        ColumnDefinition("sidecar_container_ids", "json", nullable=True),
        ColumnDefinition("volumes", "json", nullable=True),
        ColumnDefinition("min_idle_services", "integer", nullable=False, default=0),
        ColumnDefinition("service_concurrency", "integer", nullable=False, default=2),
        ColumnDefinition("service_ttl", "integer", nullable=False, default=300),
        ColumnDefinition("session_concurrency", "integer", nullable=False, default=3),
        ColumnDefinition("session_ttl", "integer", nullable=False, default=60),
        ColumnDefinition("message_timeout", "integer", nullable=False, default=600),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
    ],
)

# 原表名 bot。平台全局 Agent 模板目录（不带 jiuwenclaw_id）；对外业务键 template_id。
AGENT_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="agent_template",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("agent_tags", "json", nullable=True),
        ColumnDefinition("template_ref", "json", nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
    ],
)
