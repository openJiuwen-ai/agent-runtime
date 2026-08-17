"""身份服务表定义：用户 / 认证身份 / 刷新会话 / 组织 / 成员。

权威的"人 + 凭据 + 目录(组织/成员)"数据源,独立于 claw_manager 管理库。
认证与身份解耦：``app_user`` 存身份/角色，``auth_identity`` 存本地口令等可直接
认证的身份。企业联合身份使用独立的受信连接与外部身份映射表，避免把 issuer 和
connection_id 编码进 provider 字符串。bot / 可见性 / 模板等平台配置留在管理库。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

# 无组织的保留 group_id（避免 NULL 特判，可见性/查询全程统一）。
NO_ORG_GROUP_ID = "__none__"

APP_USER_TABLE_DEF = TableDefinition(
    table_name="app_user",
    columns=[
        ColumnDefinition("user_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("display_name", "string", length=128, nullable=False),
        ColumnDefinition("is_admin", "boolean", nullable=False, default=False),
        ColumnDefinition("status", "string", length=16, nullable=False, default="active"),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

# 凭据型认证身份：当前保存 local 用户名/口令；企业联合身份使用下方独立映射表。
# 一个 user 可挂多种由身份中心直接校验的凭据，换凭据不改业务主体。
AUTH_IDENTITY_TABLE_DEF = TableDefinition(
    table_name="auth_identity",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("provider", "string", length=32, nullable=False),
        ColumnDefinition("external_subject", "string", length=256, nullable=False),
        ColumnDefinition("credential", "string", length=512, nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["provider", "external_subject"], unique=True),
        IndexDefinition(["user_id"], unique=False),
    ],
)

# 刷新会话：refresh token 落地，可撤销/轮换（access JWT 自包含、不落库）。
AUTH_SESSION_TABLE_DEF = TableDefinition(
    table_name="auth_session",
    columns=[
        ColumnDefinition("refresh_token", "string", length=128, primary_key=True, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("expires_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["user_id"], unique=False),
        IndexDefinition(["expires_at"], unique=False),
    ],
)

ORG_TABLE_DEF = TableDefinition(
    table_name="org",
    columns=[
        ColumnDefinition("group_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("name", "string", length=128, nullable=False),
        ColumnDefinition("status", "string", length=16, nullable=False, default="active"),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

# 用户↔组织 多对多。默认无组织 = 不存在任何真实成员关系（自动归类）。
USER_ORG_MEMBERSHIP_TABLE_DEF = TableDefinition(
    table_name="user_org_membership",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("group_id", "string", length=64, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["user_id", "group_id"], unique=True),
        IndexDefinition(["group_id"], unique=False),
    ],
)

# JWT 签名密钥（RS256,单例固定主键 id="default"）。生成一次→落库→所有副本读同一行。
# 参考 claw_manager `manager_identity` 的落库范式,但本表/库/算法独立(RSA PEM 文本)。
IDENTITY_JWT_SIGNING_KEY_TABLE_DEF = TableDefinition(
    table_name="identity_jwt_signing_key",
    columns=[
        ColumnDefinition("id", "string", length=32, primary_key=True, nullable=False),
        ColumnDefinition("sign_alg", "string", length=32, nullable=False),         # "RS256"
        ColumnDefinition("private_key", "string", length=4096, nullable=False),     # PKCS8 PEM
        ColumnDefinition("public_key", "string", length=1024, nullable=False),      # SPKI PEM
        ColumnDefinition("key_version", "string", length=32, nullable=False),       # "v1"
        ColumnDefinition("fingerprint", "string", length=128, nullable=False),      # SHA-256 hex(public PEM)
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

# 联合认证连接：可信 issuer 与一个本地组织(group_id)的稳定绑定。
FEDERATION_CONNECTION_TABLE_DEF = TableDefinition(
    table_name="federation_connection",
    columns=[
        ColumnDefinition("connection_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("provider_type", "string", length=32, nullable=False),
        ColumnDefinition("issuer", "string", length=512, nullable=False),
        ColumnDefinition("group_id", "string", length=64, nullable=False),
        ColumnDefinition("name", "string", length=128, nullable=False),
        ColumnDefinition("default_role", "string", length=32, nullable=False, default="member"),
        ColumnDefinition("status", "string", length=16, nullable=False, default="active"),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["issuer"], unique=False),
        IndexDefinition(["group_id"], unique=False),
    ],
)

# 稳定外部身份键。一个外部主体只映射一个本地用户；同一用户可绑定多个外部身份。
FEDERATED_IDENTITY_TABLE_DEF = TableDefinition(
    table_name="federated_identity",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("connection_id", "string", length=64, nullable=False),
        ColumnDefinition("issuer", "string", length=512, nullable=False),
        ColumnDefinition("external_subject", "string", length=256, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("attributes", "json", nullable=False),
        ColumnDefinition("first_login_at", "datetime", nullable=False),
        ColumnDefinition("last_login_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(
            ["connection_id", "issuer", "external_subject"],
            unique=True,
        ),
        IndexDefinition(["user_id"], unique=False),
    ],
)

# 可审计的受信授权映射：Provider 验证后的 Claim 精确值 -> 本地角色。
# 回调携带的任意 role/is_admin 不会被直接信任。
FEDERATION_ROLE_MAPPING_TABLE_DEF = TableDefinition(
    table_name="federation_role_mapping",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("connection_id", "string", length=64, nullable=False),
        ColumnDefinition("claim_name", "string", length=128, nullable=False),
        ColumnDefinition("claim_value", "string", length=256, nullable=False),
        ColumnDefinition("local_role", "string", length=32, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(
            ["connection_id", "claim_name", "claim_value", "local_role"],
            unique=True,
        ),
        IndexDefinition(["connection_id"], unique=False),
    ],
)

# 浏览器联合登录状态及一次性换码。只保存 code 的 SHA-256，不保存明文。
FEDERATION_LOGIN_STATE_TABLE_DEF = TableDefinition(
    table_name="federation_login_state",
    columns=[
        ColumnDefinition("request_id", "string", length=128, primary_key=True, nullable=False),
        ColumnDefinition("connection_id", "string", length=64, nullable=False),
        ColumnDefinition("return_to", "string", length=512, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("expires_at", "datetime", nullable=False),
    ],
    indexes=[IndexDefinition(["expires_at"], unique=False)],
)

FEDERATION_LOGIN_CODE_TABLE_DEF = TableDefinition(
    table_name="federation_login_code",
    columns=[
        ColumnDefinition("code_hash", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("expires_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["user_id"], unique=False),
        IndexDefinition(["expires_at"], unique=False),
    ],
)

IDENTITY_TABLE_DEFINITIONS = (
    APP_USER_TABLE_DEF,
    AUTH_IDENTITY_TABLE_DEF,
    AUTH_SESSION_TABLE_DEF,
    ORG_TABLE_DEF,
    USER_ORG_MEMBERSHIP_TABLE_DEF,
    IDENTITY_JWT_SIGNING_KEY_TABLE_DEF,
    FEDERATION_CONNECTION_TABLE_DEF,
    FEDERATED_IDENTITY_TABLE_DEF,
    FEDERATION_ROLE_MAPPING_TABLE_DEF,
    FEDERATION_LOGIN_STATE_TABLE_DEF,
    FEDERATION_LOGIN_CODE_TABLE_DEF,
)
