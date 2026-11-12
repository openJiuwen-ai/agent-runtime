"""部署参数 → deploy.sh 的 .env.custom 生成与派生。

白名单 + 校验规则与 deploy/openshift 的 var_handler.sh 对齐（命名空间 DNS label、
镜像字符集、副本正整数、部署模式枚举）；CLUSTER_TYPE/MODE 由服务端强制写死，
不接受请求传入。值不得包含引号/反斜杠/换行（bash env 读取器与 YAML 渲染的安全边界）。
"""

from __future__ import annotations

import re

# K8s DNS label：小写字母/数字/连字符，不以连字符首尾，最长 63（与前端一致）
K8S_NS_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
# 与 var_handler.sh check_image_vars 同源
ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9._:@/+,-]+$")
# 值内禁止出现的环境（引号/反斜杠/换行会破坏 env 读取器或 YAML 渲染）
UNSAFE_VALUE_CHARS_RE = re.compile(r'["\\\r\n]')

VALID_DEPLOY_MODES = ("routing", "full")
VALID_DB_TYPES = ("mariadb", "mysql", "postgresql")

# 镜像变量（global_vars.sh IMAGE_VARS + 模式/沙箱分支校验的两个）
IMAGE_ENV_KEYS = (
    "AGENTSERVER_IMAGE",
    "GATEWAY_IMAGE",
    "WEB_IMAGE",
    "IDENTITY_IMAGE",
    "MANAGER_SERVER_IMAGE",
    "MANAGER_WEB_IMAGE",
    "MARIADB_IMAGE",
    "MYSQL_IMAGE",
    "POSTGRESQL_IMAGE",
    "REDIS_IMAGE",
    "MINIO_IMAGE",
    "AGENT_RUNTIME_IMAGE",
    "JIUWENBOX_IMAGE",
)

REPLICA_ENV_KEYS = (
    "AGENT_SERVER_REPLICAS",
    "GATEWAY_REPLICAS",
    "WEB_REPLICAS",
    "IDENTITY_REPLICAS",
    "MANAGER_SERVER_REPLICAS",
    "MANAGER_WEB_REPLICAS",
    "AGENT_RUNTIME_REPLICAS",
)

# 请求/默认表允许出现的全部 env 键（拼 .env.custom 时白名单过滤）
ALLOWED_ENV_KEYS = frozenset(
    {
        *IMAGE_ENV_KEYS,
        *REPLICA_ENV_KEYS,
        # 形态
        "AGENT_RUNTIME_DEPLOY_MODE",
        "SANDBOX_TYPE",
        "JIUWENBOX_PORT",
        # 数据库（外置/内置凭证与选型）
        "DB_TYPE",
        "DB_HOST",
        "DB_PORT",
        "DB_USER",
        "DB_PASSWORD",
        "MARIADB_ROOT_PASSWORD",
        "MYSQL_ROOT_PASSWORD",
        "POSTGRES_PASSWORD",
        "GATEWAY_DB_NAME",
        "WEB_DB_NAME",
        "GATEWAY_PG_SCHEMA",
        "WEB_PG_SCHEMA",
        # Redis（外置）
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_USER",
        "REDIS_PASSWORD",
        "REDIS_MODE",
        # 对象存储（外置 OBS/S3）
        "OBS_URL",
        "OBS_BUCKET",
        "OBS_ACCESS_KEY",
        "OBS_SECRET_KEY",
        "OBS_SECURE",
        "OBS_REGION",
        # 存储
        "DATA_STORAGE_CLASS",
        "WORKSPACE_STORAGE_CLASS",
        # NodePort
        "MANAGER_WEB_NODE_PORT",
    }
)

# 密码/密钥类键（API 响应须掩码）
SENSITIVE_KEY_MARKERS = ("PASSWORD", "SECRET", "ACCESS_KEY")

# 管理面组件键（镜像 + 副本）：部署功能只部署产品面（manager 组不经其部署），
# 这些键对使用方无意义，defaults 响应不展示；但任务生成 .env.custom 时仍需取值
# （deploy.sh 的 check_image_vars 无条件校验全量 IMAGE_VARS），故仅展示层过滤、
# 存储保持完整
MANAGER_PLANE_ENV_KEYS = (
    "IDENTITY_IMAGE",
    "MANAGER_SERVER_IMAGE",
    "MANAGER_WEB_IMAGE",
    "IDENTITY_REPLICAS",
    "MANAGER_SERVER_REPLICAS",
    "MANAGER_WEB_REPLICAS",
)

# 服务端强制写死、不接受请求/默认表覆盖的键
FORCED_ENV = {
    "CLUSTER_TYPE": "k8s",   # 管理面启动部署仅支持 k8s 形态
    "MODE": "product",       # 代码挂载 dev 模式不经管理面
}


def validate_env_value(key: str, value: str) -> str:
    """校验单个 env 值，返回归一化后的值；非法抛 ``ValueError``。"""
    text = str(value).strip()
    if UNSAFE_VALUE_CHARS_RE.search(text):
        raise ValueError(f"{key} 含非法字符（引号/反斜杠/换行）")
    if key in IMAGE_ENV_KEYS and not ENV_VALUE_RE.match(text):
        raise ValueError(f"{key} 镜像名非法: {text}")
    if key in REPLICA_ENV_KEYS:
        if not text.isdigit() or int(text) < 1:
            raise ValueError(f"{key} 必须为正整数")
    if key == "AGENT_RUNTIME_DEPLOY_MODE" and text not in VALID_DEPLOY_MODES:
        raise ValueError(f"AGENT_RUNTIME_DEPLOY_MODE 仅支持 {'/'.join(VALID_DEPLOY_MODES)}")
    if key == "DB_TYPE" and text not in VALID_DB_TYPES:
        raise ValueError(f"DB_TYPE 仅支持 {'/'.join(VALID_DB_TYPES)}")
    return text


def merge_env(defaults: dict, overrides: dict) -> dict:
    """默认 env 与请求覆盖合并（白名单过滤 + 逐值校验）。

    输入源里的强制键（FORCED_ENV，如历史误存的 CLUSTER_TYPE/MODE）静默忽略——
    它们由服务端统一写死，不应因存量脏数据让后续任务创建失败。
    """
    merged: dict[str, str] = {}
    for source in (defaults or {}, overrides or {}):
        for raw_key, raw_value in source.items():
            key = str(raw_key).strip().upper()
            if key in FORCED_ENV:
                continue
            if key not in ALLOWED_ENV_KEYS:
                raise ValueError(f"不支持的配置键: {key}")
            if raw_value is None:
                continue
            merged[key] = validate_env_value(key, str(raw_value))
    merged.update(FORCED_ENV)
    return merged


def validate_merged_env(env: dict, *, namespace: str) -> None:
    """跨字段一致性校验（对齐 var_handler.sh 语义）。"""
    if not namespace or not K8S_NS_RE.match(namespace) or len(namespace) > 63:
        raise ValueError(f"namespace 非法（K8s DNS label）: {namespace}")
    deploy_mode = env.get("AGENT_RUNTIME_DEPLOY_MODE", "routing")
    if deploy_mode not in VALID_DEPLOY_MODES:
        raise ValueError(f"AGENT_RUNTIME_DEPLOY_MODE 仅支持 {'/'.join(VALID_DEPLOY_MODES)}")
    # full/routing 均要求 AGENT_RUNTIME_IMAGE（resolve_deploy_mode_vars 语义）
    if not env.get("AGENT_RUNTIME_IMAGE"):
        raise ValueError("AGENT_RUNTIME_IMAGE 未配置（默认参数缺失，请先补全默认镜像清单）")
    # 外置 DB 必须有密码（resolve_db_vars：DB_PASSWORD > 类型对应 ROOT_PASSWORD）
    if env.get("DB_HOST"):
        db_type = env.get("DB_TYPE", "mariadb")
        password_keys = {
            "mariadb": ("DB_PASSWORD", "MARIADB_ROOT_PASSWORD"),
            "mysql": ("DB_PASSWORD", "MYSQL_ROOT_PASSWORD"),
            "postgresql": ("DB_PASSWORD", "POSTGRES_PASSWORD"),
        }
        candidates = password_keys.get(db_type, ("DB_PASSWORD",))
        if not any(env.get(k) for k in candidates):
            raise ValueError("外置数据库需配置 DB_PASSWORD（或对应类型的 root 密码）")
    # SANDBOX_TYPE=jiuwenbox 需要沙箱镜像
    if env.get("SANDBOX_TYPE") == "jiuwenbox" and not env.get("JIUWENBOX_IMAGE"):
        raise ValueError("SANDBOX_TYPE=jiuwenbox 需要配置 JIUWENBOX_IMAGE")


def build_env_custom(env: dict, *, namespace: str) -> str:
    """生成 deploy.sh 的 .env.custom 内容（KEY="value" 逐行）。"""
    lines = [f'NAMESPACE="{namespace}"']
    lines += [f'{key}="{value}"' for key, value in sorted(env.items())]
    return "\n".join(lines) + "\n"


def derive_db_connection(env: dict, *, tool_defaults: dict | None = None) -> dict:
    """从合并 env 派生共享数据库连接信息。

    未显式配置的键回落 deploy 工具内置默认（global_vars.sh 的 DEPLOY_VARS，经
    ``tool_defaults`` 动态传入——与 deploy.sh 实际执行保持同一真源，避免硬编码
    副本漂移）；最终兜底为与 global_vars 当前值一致的常量，防 tool_defaults 缺失。
    """
    defaults = tool_defaults or {}
    db_type = env.get("DB_TYPE") or defaults.get("DB_TYPE") or "mariadb"
    default_host = f"{db_type}-headless.default"   # k8s 形态内置 = default 共享实例
    default_port = defaults.get("DB_PORT") or ("5432" if db_type == "postgresql" else "3306")
    default_user = defaults.get("DB_USER") or ("postgres" if db_type == "postgresql" else "root")
    root_password_keys = {
        "mariadb": "MARIADB_ROOT_PASSWORD",
        "mysql": "MYSQL_ROOT_PASSWORD",
        "postgresql": "POSTGRES_PASSWORD",
    }
    fallback_passwords = {"mariadb": "Root@123456", "mysql": "Root@123456", "postgresql": "Root@123456"}
    return {
        "db_type": db_type,
        "host": env.get("DB_HOST") or default_host,
        "port": int(env.get("DB_PORT") or default_port),
        "user": env.get("DB_USER") or default_user,
        "password": env.get("DB_PASSWORD")
        or env.get(root_password_keys[db_type], "")
        or defaults.get(root_password_keys[db_type], "")
        or fallback_passwords[db_type],
    }


def derive_product_databases(env: dict, *, namespace: str) -> list[dict]:
    """派生产品面所需预建库（对齐 resolve_module_db_vars：mysql/mariadb 按库隔离、
    postgresql 按 schema 隔离；用户显式设置的 *_DB_NAME 原样尊重）。"""
    db_type = env.get("DB_TYPE", "mariadb")
    if db_type == "postgresql":
        items = []
        for module in ("gateway", "web"):
            items.append(
                {
                    "database": env.get(f"{module.upper()}_DB_NAME") or module,
                    "schema": env.get(f"{module.upper()}_PG_SCHEMA") or namespace,
                }
            )
        return items
    return [
        {
            "database": env.get("GATEWAY_DB_NAME") or f"gateway_{namespace}",
            "schema": None,
        },
        {
            "database": env.get("WEB_DB_NAME") or f"web_{namespace}",
            "schema": None,
        },
    ]


def derive_instance_hosts(namespace: str) -> dict:
    """按命名空间推导实例注册 host（与前端 CreateInstanceModal 常量一致）。"""
    return {
        "gateway_host": f"http://jiuwenswarm-gateway.{namespace}:19000",
        "runtime_host": f"http://jiuwenswarm-agent-runtime.{namespace}:8091",
        "user_web_host": f"http://jiuwenswarm-web.{namespace}:5173",
        "gateway_web_http_host": f"http://jiuwenswarm-gateway.{namespace}:19000",
        "gateway_web_ws_host": f"http://jiuwenswarm-gateway.{namespace}:19000",
    }


def is_sensitive_key(key: str) -> bool:
    """键是否为密码/密钥类（注入侧剔除与掩码共用的判定）。"""
    return any(marker in key.upper() for marker in SENSITIVE_KEY_MARKERS)


def mask_sensitive_env(env: dict) -> dict:
    """API 响应用：密码/密钥类值替换为掩码（键含标记即掩码）。"""
    masked: dict[str, str] = {}
    for key, value in env.items():
        if is_sensitive_key(key) and value:
            masked[key] = "******"
        else:
            masked[key] = value
    return masked
