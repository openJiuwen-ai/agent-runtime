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
    )

    rest_host: str = Field(default="0.0.0.0", validation_alias="MANAGER_REST_HOST")
    rest_port: int = Field(default=8765, validation_alias="MANAGER_REST_PORT")

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

    # ---- 本实例标识：仅用户态 /user-console/agents 用它把可见 agent 限定到"当前 gateway"。
    # 每命名空间部署时与本命名空间 gateway 同值注入；管理端接口不用它(实例由路径显式指定)。
    jiuwenclaw_id: str = Field(default="", validation_alias="JIUWENCLAW_ID")
    agent_runtime_endpoint: str = Field(
        default="", validation_alias="AGENT_RUNTIME_ENDPOINT"
    )
    agent_runtime_sync_timeout: float = Field(
        default=10.0, validation_alias="AGENT_RUNTIME_SYNC_TIMEOUT"
    )

    @property
    def host(self) -> str:
        return self.rest_host

    @property
    def port(self) -> int:
        return self.rest_port


settings = Settings()
