"""身份服务运行时配置（从 .env / 环境变量加载，前缀 IDENTITY_）。"""

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

    # ---- HTTP ----
    rest_host: str = Field(default="0.0.0.0", validation_alias="IDENTITY_REST_HOST")
    rest_port: int = Field(default=8770, validation_alias="IDENTITY_REST_PORT")

    # ---- 数据库（独立身份库；DBHandler 抽象，sqlite/mysql/postgresql 通用）----
    db_type: str = Field(default="sqlite", validation_alias="IDENTITY_DB_TYPE")
    sqlite_path: str = Field(default="identity.db", validation_alias="IDENTITY_SQLITE_PATH")
    db_host: str = Field(default="127.0.0.1", validation_alias="IDENTITY_DB_HOST")
    db_port: int = Field(default=3306, validation_alias="IDENTITY_DB_PORT")
    db_user: str = Field(default="root", validation_alias="IDENTITY_DB_USER")
    db_password: str = Field(default="root", validation_alias="IDENTITY_DB_PASSWORD")
    db_name: str = Field(default="identity", validation_alias="IDENTITY_DB_NAME")
    pg_schema: str = Field(default="public", validation_alias="IDENTITY_PG_SCHEMA")

    # ---- JWT（RS256：私钥签发，资源服务器用公钥验签）----
    # 重构:统一去旧名 jiuwenclaw→openjiuwen;跨服务契约,manager_server 须用同一 issuer/audience 验签
    jwt_issuer: str = Field(default="openjiuwen-identity", validation_alias="IDENTITY_JWT_ISSUER")
    jwt_audience: str = Field(default="openjiuwen", validation_alias="IDENTITY_JWT_AUDIENCE")
    access_ttl_seconds: int = Field(default=1800, validation_alias="IDENTITY_ACCESS_TTL")
    refresh_ttl_seconds: int = Field(default=7 * 24 * 3600, validation_alias="IDENTITY_REFRESH_TTL")
    # JWT 签名密钥落身份库(表 identity_jwt_signing_key,生成一次→落库→多副本读同一行)。

    # ---- 联合认证（当前仓库仅提供显式开启的本地 Demo Provider）----
    federation_demo_enabled: bool = Field(
        default=False,
        validation_alias="IDENTITY_FEDERATION_DEMO_ENABLED",
    )
    federation_public_path_prefix: str = Field(
        default="/idp",
        validation_alias="IDENTITY_FEDERATION_PUBLIC_PATH_PREFIX",
    )
    federation_request_ttl_seconds: int = Field(
        default=300,
        validation_alias="IDENTITY_FEDERATION_REQUEST_TTL",
    )
    federation_code_ttl_seconds: int = Field(
        default=60,
        validation_alias="IDENTITY_FEDERATION_CODE_TTL",
    )
    federation_demo_admin_group: str = Field(
        default="enterprise-admins",
        validation_alias="IDENTITY_FEDERATION_DEMO_ADMIN_GROUP",
    )

    # ---- 引导播种 ----
    seed_admin: bool = Field(default=True, validation_alias="IDENTITY_SEED_ADMIN")
    seed_user1: bool = Field(default=True, validation_alias="IDENTITY_SEED_USER1")

    @property
    def host(self) -> str:
        return self.rest_host

    @property
    def port(self) -> int:
        return self.rest_port


settings = Settings()
