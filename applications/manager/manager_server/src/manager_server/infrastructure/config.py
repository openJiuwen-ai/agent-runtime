"""运行时配置（从 .env / 环境变量加载）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_files() -> tuple[str | Path, ...]:
    """解析 applications/manager/.env（管理面统一配置文件）。"""
    manager_dir = Path(__file__).resolve().parents[4]
    env_file = manager_dir / ".env"
    return (env_file,) if env_file.is_file() else ()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_resolve_env_files() or None,
        env_file_encoding="utf-8",
        extra="ignore",
        # 部署模板未填时常见 ``ENV=""``；忽略空串以使用字段 default
        env_ignore_empty=True,
    )

    rest_host: str = Field(default="0.0.0.0", validation_alias="MANAGER_REST_HOST")
    rest_port: int = Field(default=8765, validation_alias="MANAGER_REST_PORT")
    # CORS 允许的浏览器来源（逗号分隔）。空 = 只允许同源（受支持拓扑经 manager-web 单源反代）
    cors_allow_origins: str = Field(default="", validation_alias="MANAGER_CORS_ALLOW_ORIGINS")

    db_type: str = Field(default="sqlite", validation_alias="MANAGER_DB_TYPE")
    sqlite_path: str = Field(default="manager.db", validation_alias="MANAGER_SQLITE_PATH")
    db_host: str = Field(default="127.0.0.1", validation_alias="MANAGER_DB_HOST")
    db_port: int = Field(default=3306, validation_alias="MANAGER_DB_PORT")
    db_user: str = Field(default="root", validation_alias="MANAGER_DB_USER")
    db_password: str = Field(default="root", validation_alias="MANAGER_DB_PASSWORD")
    db_name: str = Field(default="manager", validation_alias="MANAGER_DB_NAME")
    pg_schema: str = Field(default="public", validation_alias="MANAGER_PG_SCHEMA")

    # Manager 周期探活 Gateway/Runtime 健康检查的间隔（秒）
    MANAGER_HEARTBEAT_SCAN_INTERVAL_SECONDS: int = Field(
        default=60, validation_alias="MANAGER_HEARTBEAT_SCAN_INTERVAL_SECONDS"
    )

    # ---- Redis（心跳巡检选主的分布式锁；URL 留空时，走无锁分支）----
    redis_url: str = Field(default="", validation_alias="REDIS_URL")
    redis_username: str = Field(default="", validation_alias="REDIS_USER")
    redis_password: str = Field(default="", validation_alias="REDIS_PASSWORD")
    # Redis 键前缀（选配；共享 Redis 多业务隔离用）。空 = 键名与历史版本一致
    redis_key_prefix: str = Field(default="", validation_alias="REDIS_KEY_PREFIX")
    # 巡检锁 TTL（毫秒）：< 扫描间隔，扫描结束自动过期，无需释放
    scan_lock_ttl_ms: int = Field(default=30000, validation_alias="MANAGER_SCAN_LOCK_TTL_MS")

    # ---- OBS（只读读取技能上传审批包；凭据复用 AgentServer 的 AK/SK）----
    obs_url: str = Field(default="", validation_alias="OBS_URL")
    obs_bucket: str = Field(default="", validation_alias="OBS_BUCKET")
    obs_access_key: str = Field(default="", validation_alias="OBS_ACCESS_KEY")
    obs_secret_key: str = Field(default="", validation_alias="OBS_SECRET_KEY")
    obs_region: str = Field(default="default", validation_alias="OBS_REGION")
    # 用字符串接收，避免非法布尔值在 import 阶段让管理面起不来
    obs_secure: str = Field(default="false", validation_alias="OBS_SECURE")

    # ========== 配置下发字段级加密（信封加密，密钥握手分发） ==========
    config_enc_enabled: bool = Field(
        default=False, validation_alias="CLAWMANAGER_CONFIG_ENC_ENABLED"
    )
    config_enc_required: bool = Field(
        default=False, validation_alias="CLAWMANAGER_CONFIG_ENC_REQUIRED"
    )

    # ========== 配置下发加签（Ed25519，公钥握手分发） ==========
    config_sign_enabled: bool = Field(
        default=False, validation_alias="CLAWMANAGER_CONFIG_SIGN_ENABLED"
    )
    config_sign_alg: str = Field(
        default="Ed25519", validation_alias="CLAWMANAGER_CONFIG_SIGN_ALG"
    )

    # ---- 统一 Web 入口（manager-web）----
    manager_web_host: str = Field(default="localhost", validation_alias="MANAGER_WEB_HOST")
    manager_web_port: int = Field(default=5273, validation_alias="MANAGER_WEB_PORT")
    manager_web_proxy_target: str = Field(
        default="http://127.0.0.1:8765",
        validation_alias="MANAGER_WEB_PROXY_TARGET",
    )
    manager_web_idp_target: str = Field(
        default="http://127.0.0.1:8770",
        validation_alias="MANAGER_WEB_IDP_TARGET",
    )
    manager_web_user_web_target: str = Field(
        default="http://127.0.0.1:5173",
        validation_alias="MANAGER_WEB_USER_WEB_TARGET",
    )
    manager_web_gateway_http_target: str = Field(
        default="http://127.0.0.1:19002",
        validation_alias="MANAGER_WEB_GATEWAY_HTTP_TARGET",
    )
    manager_web_gateway_ws_target: str = Field(
        default="http://127.0.0.1:19000",
        validation_alias="MANAGER_WEB_GATEWAY_WS_TARGET",
    )
    manager_web_log_level: str = Field(default="info", validation_alias="MANAGER_WEB_LOG_LEVEL")

    # ---- 资源服务器：验签认证服务(jiuwenclaw_identity)签发的 RS256 JWT ----
    identity_public_key_url: str = Field(
        default="http://127.0.0.1:8770/v1/auth/public_key",
        validation_alias="IDENTITY_PUBLIC_KEY_URL",
    )
    jwt_issuer: str = Field(default="openjiuwen-identity", validation_alias="IDENTITY_JWT_ISSUER")
    jwt_audience: str = Field(default="openjiuwen", validation_alias="IDENTITY_JWT_AUDIENCE")

    agent_runtime_sync_timeout: float = Field(
        default=10.0, validation_alias="AGENT_RUNTIME_SYNC_TIMEOUT"
    )

    # 用户空间配额（策略/用量/扩容）。默认关闭；与 Gateway/AgentServer 共用 WORKSPACE_QUOTA_ENABLED。
    workspace_quota_enabled: bool = Field(
        default=False,
        validation_alias="WORKSPACE_QUOTA_ENABLED",
    )

    agent_client_registration_enabled: bool = Field(
        default=False, validation_alias="MANAGER_AGENT_CLIENT_REGISTRATION_ENABLED"
    )
    agent_client_registration_config_file: str = Field(
        default="", validation_alias="MANAGER_AGENT_CLIENT_REGISTRATION_CONFIG_FILE"
    )

    # Hub 平台包清单。与用户面同一变量，不跟某个集群走。
    team_skills_hub_base_url: str = Field(
        default="https://swarmskills.openjiuwen.com",
        validation_alias="TEAM_SKILLS_HUB_BASE_URL",
    )
    team_skills_hub_timeout: float = Field(
        default=30.0, validation_alias="TEAM_SKILLS_HUB_TIMEOUT"
    )

    # ---- 管理面启动部署（manager-server 以子进程代跑 deploy.sh）----
    # 总开关（默认开；工具树/kubeconfig 不可用时创建任务返回 503）
    deploy_enabled: bool = Field(
        default=True, validation_alias="MANAGER_DEPLOY_ENABLED"
    )
    # deploy/openshift 工具树：留空自动探测（镜像内 /app/agent-runtime/deploy/openshift
    # → 源码仓 deploy/openshift）
    deploy_tool_dir: str = Field(default="", validation_alias="MANAGER_DEPLOY_TOOL_DIR")
    # 部署子进程用的 kubeconfig（k8s 形态 kubectl 隐式解析；随 Secret 卷注入）
    deploy_kubeconfig: str = Field(
        default="", validation_alias="MANAGER_DEPLOY_KUBECONFIG"
    )
    # manager up 注入的 .env.custom 快照（JSON，非敏感键）；启动时据此刷新部署默认参数
    deploy_env_snapshot: str = Field(
        default="", validation_alias="MANAGER_DEPLOY_ENV_JSON"
    )
    # 全局并发部署任务数
    deploy_max_concurrent: int = Field(
        default=2, validation_alias="MANAGER_DEPLOY_MAX_CONCURRENT"
    )
    # 单任务预算（秒）：超时 SIGTERM 进程组，任务置 failed
    deploy_task_timeout_seconds: int = Field(
        default=3600, validation_alias="MANAGER_DEPLOY_TASK_TIMEOUT_SECONDS"
    )
    # 任务日志落库上限（行数 / 字节数，超限截断并写标记行）
    deploy_log_max_lines: int = Field(
        default=4000, validation_alias="MANAGER_DEPLOY_LOG_MAX_LINES"
    )
    deploy_log_max_bytes: int = Field(
        default=524288, validation_alias="MANAGER_DEPLOY_LOG_MAX_BYTES"
    )
    # running 任务心跳超时（秒）：超过判定孤儿（owner 副本死亡）置 interrupted
    deploy_heartbeat_stale_seconds: int = Field(
        default=180, validation_alias="MANAGER_DEPLOY_HEARTBEAT_STALE_SECONDS"
    )

    @property
    def host(self) -> str:
        return self.rest_host

    @property
    def port(self) -> int:
        return self.rest_port


settings = Settings()
