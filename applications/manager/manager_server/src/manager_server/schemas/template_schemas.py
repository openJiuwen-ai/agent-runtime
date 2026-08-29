"""模板 API 请求/响应模型。

涵盖 model_template、extension_config_template、skill_whitelist_template、
service_config_template、agent_template。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import urlparse
import re

from croniter import croniter
from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from manager_server.schemas.safe_text import SafeTextMixin

from manager_server.infrastructure.template_ref import (
    normalize_template_ref,
    normalize_template_ref_optional,
)

ModelTypeLiteral = Literal["default", "video", "audio", "vision"]
ExtensionComponentLiteral = Literal["gateway", "agent_server"]
ExtensionHookTypeLiteral = Literal["pre_request", "post_request", "error", "schedule"]
ImagePullPolicyLiteral = Literal["Always", "IfNotPresent", "Never"]
TemplateIdPath = Annotated[str, Field(min_length=1, max_length=100)]
TemplateRefField = Annotated[dict[str, list[str]], BeforeValidator(normalize_template_ref)]
OptionalTemplateRefField = Annotated[
    dict[str, list[str]] | None,
    BeforeValidator(normalize_template_ref_optional),
]


ModelTypeLiteral = Literal["default", "video", "audio", "vision"]
ExtensionComponentLiteral = Literal["gateway", "agent_server"]
ExtensionHookTypeLiteral = Literal["pre_request", "post_request", "error", "schedule"]
ImagePullPolicyLiteral = Literal["Always", "IfNotPresent", "Never"]
TemplateIdPath = Annotated[str, Field(min_length=1, max_length=100)]
TemplateRefField = Annotated[dict[str, list[str]], BeforeValidator(normalize_template_ref)]
OptionalTemplateRefField = Annotated[
    dict[str, list[str]] | None,
    BeforeValidator(normalize_template_ref_optional),
]

# croniter：5 段标准；6 段末尾为秒；7 段为 分 时 日 月 周 秒 年
_CRON_FIELD_COUNTS = frozenset({5, 6, 7})


def is_valid_hook_schedule(value: str) -> bool:
    """用 croniter 校验 hook_config.schedule（含字段取值范围）。"""
    text = value.strip()
    if not text:
        return False
    if len(text.split()) not in _CRON_FIELD_COUNTS:
        return False
    return croniter.is_valid(text)


def normalize_hook_schedule(schedule: str | None, *, required: bool) -> str | None:
    """规范化 schedule；required 时不可为空，有值时须为合法 cron。"""
    text = (schedule or "").strip()
    if not text:
        if required:
            raise ValueError("hook_config.schedule is required when hook_type=schedule")
        return None
    if not is_valid_hook_schedule(text):
        raise ValueError(
            "hook_config.schedule must be a valid cron expression "
            "(5/6/7 fields via croniter, e.g. '0 */5 * * *' or '0 0 */5 * * *')"
        )
    return text


def _validate_http_url(value: str) -> str:
    """校验为合法 http(s) URL（须含主机）。"""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be a valid http(s) URL")
    return value


ApiBaseUrl = Annotated[
    str,
    Field(min_length=1, max_length=512),
    AfterValidator(_validate_http_url),
]
SkillSourceUrl = Annotated[
    str,
    Field(min_length=1, max_length=2048),
    AfterValidator(_validate_http_url),
]



class AgentTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    agent_tags: list[str] | None = None
    template_ref: TemplateRefField = Field(default_factory=dict)
    enabled: bool = True
    data: dict[str, Any] | None = None


class AgentTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    agent_tags: list[str] | None = None
    template_ref: OptionalTemplateRefField = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class AgentTemplateListQuery(BaseModel):
    """Agent 模板列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    search: str | None = Field(
        default=None,
        max_length=256,
        description="按 template_id、template_name、description、agent_tags 模糊搜索",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、template_id、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")



class ModelTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    model_type: list[ModelTypeLiteral] = Field(default_factory=list)
    model_tags: list[str] | None = None
    api_base: ApiBaseUrl
    api_key: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1, max_length=128)
    model_provider: str = Field(..., min_length=1, max_length=64)
    parameters: dict[str, Any] | None = None
    timeout: int = Field(default=60, ge=1)
    retry_count: int = Field(default=3, ge=0)
    enable_streaming: bool = True
    enable_function_calling: bool = True
    verify_ssl: bool = False
    enabled: bool = True
    data: dict[str, Any] | None = None


class ModelTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    model_type: list[ModelTypeLiteral] | None = None
    model_tags: list[str] | None = None
    api_base: ApiBaseUrl | None = None
    api_key: str | None = Field(default=None, min_length=1)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_provider: str | None = Field(default=None, min_length=1, max_length=64)
    parameters: dict[str, Any] | None = None
    timeout: int | None = Field(default=None, ge=1)
    retry_count: int | None = Field(default=None, ge=0)
    enable_streaming: bool | None = None
    enable_function_calling: bool | None = None
    verify_ssl: bool | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ModelTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    model_type: list[str]
    model_tags: list[str] | None
    api_base: str
    api_key: str
    model_id: str
    model_provider: str
    parameters: dict[str, Any] | None
    timeout: int
    retry_count: int
    enable_streaming: bool
    enable_function_calling: bool
    verify_ssl: bool
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class ModelTemplateListQuery(BaseModel):
    """模型模板列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    model_type: ModelTypeLiteral | None = Field(
        default=None,
        description="按模型类型筛选，如 default / video / audio / vision",
    )
    model_provider: str | None = Field(
        default=None,
        max_length=64,
        description="按 provider 筛选，大小写不敏感",
    )
    search: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "按 template_id、template_name、description、provider、"
            "模型 ID、模型类型、API base 模糊搜索"
        ),
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：template_name、description、model_provider、model_id、"
            "model_type、api_base、updated_at"
        ),
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class EmbeddingTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    embed_tags: list[str] | None = None
    api_base: ApiBaseUrl
    api_key: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1, max_length=128)
    model_provider: str = Field(..., min_length=1, max_length=64)
    parameters: dict[str, Any] | None = None
    client_config: dict[str, Any] | None = Field(
        default_factory=lambda: {
            "timeout": 60,
            "retry_count": 3,
            "verify_ssl": True,
        }
    )
    enabled: bool = True
    data: dict[str, Any] | None = None


class EmbeddingTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    embed_tags: list[str] | None = None
    api_base: ApiBaseUrl | None = None
    api_key: str | None = Field(default=None, min_length=1)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_provider: str | None = Field(default=None, min_length=1, max_length=64)
    parameters: dict[str, Any] | None = None
    client_config: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class EmbeddingTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    embed_tags: list[str] | None
    api_base: str
    api_key: str
    model_id: str
    model_provider: str
    parameters: dict[str, Any] | None
    client_config: dict[str, Any] | None
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class EmbeddingTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    model_provider: str | None = Field(default=None, max_length=64)
    search: str | None = Field(default=None, max_length=256)
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、model_provider、model_id、api_base、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class HookConfig(BaseModel):
    """扩展模板 hook_config 结构（与设计文档一致）。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    handler: str = Field(..., min_length=1, description="钩子实现路径或模块标识")
    params: dict[str, Any] | None = Field(default=None, description="传入钩子函数的静态参数")
    schedule: str | None = Field(
        default=None,
        description="仅 hook_type=schedule 时必填；cron 表达式（5/6/7 段）",
    )
    data: dict[str, Any] | None = Field(default=None, description="单条钩子扩展配置")


class ExtensionConfigTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    component: ExtensionComponentLiteral
    hook_type: ExtensionHookTypeLiteral
    hook_config: HookConfig
    custom_config: dict[str, Any] | None = None
    enabled: bool = True
    data: dict[str, Any] | None = None


class ExtensionConfigTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    component: ExtensionComponentLiteral | None = None
    hook_type: ExtensionHookTypeLiteral | None = None
    hook_config: HookConfig | None = None
    custom_config: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ExtensionConfigTemplateListQuery(BaseModel):
    """扩展配置模板列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    component: ExtensionComponentLiteral | None = Field(
        default=None,
        description="目标组件：gateway / agent_server",
    )
    hook_type: ExtensionHookTypeLiteral | None = Field(
        default=None,
        description="钩子类型：pre_request / post_request / error / schedule",
    )
    search: str | None = Field(
        default=None,
        description="按 template_id、template_name、description、component、hook_type 模糊搜索",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、component、hook_type、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class ExtensionConfigTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    component: str
    hook_type: str
    hook_config: HookConfig
    custom_config: dict[str, Any] | None
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class SkillWhitelistTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str = Field(..., min_length=1, max_length=512)
    skill_version: str = Field(..., min_length=1, max_length=64)
    skill_source: SkillSourceUrl
    enabled: bool = True
    data: dict[str, Any] | None = None


class SkillWhitelistTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str | None = Field(default=None, min_length=1, max_length=512)
    skill_version: str | None = Field(default=None, min_length=1, max_length=64)
    skill_source: SkillSourceUrl | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class SkillWhitelistTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    skill_id: str | None = Field(default=None, max_length=512)
    skill_source: str | None = Field(default=None, max_length=2048)
    search: str | None = Field(
        default=None,
        description="按 template_id、template_name、description、skill_source、skill_id、skill_version 模糊搜索",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、skill_source、skill_id、skill_version、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class SkillWhitelistTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    skill_id: str
    skill_version: str
    skill_source: str
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


# 与库表类型上限一致：integer → 有符号 32 位
__VALID_MCP_TRANSPORTS = frozenset({
    "stdio",
    "sse",
    "http",
    "streamable-http",
    "streamable_http",
})


def validate_mcp_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """校验 MCP 模板内的 ``mcp_entry``（对齐 servers[] 结构，但不保留条目级 enabled）。

    企业模板开关只认模板行 ``enabled``；``mcp_entry.enabled`` 若传入则丢弃，避免双开关。
    """
    if not isinstance(entry, dict):
        raise ValueError("mcp_entry must be a JSON object")
    normalized = dict(entry)
    normalized.pop("enabled", None)
    name = str(normalized.get("name", "")).strip()
    if not name:
        raise ValueError("mcp_entry.name is required")
    transport = str(normalized.get("transport", "")).strip().lower()
    if transport not in _VALID_MCP_TRANSPORTS:
        raise ValueError(
            "mcp_entry.transport must be one of: "
            + ", ".join(sorted(_VALID_MCP_TRANSPORTS))
        )
    if transport == "stdio":
        command = str(normalized.get("command", "")).strip()
        if not command:
            raise ValueError("mcp_entry.command is required for stdio transport")
    else:
        url = str(normalized.get("url", "")).strip()
        if not url:
            raise ValueError("mcp_entry.url is required for remote MCP transport")
    normalized["name"] = name
    normalized["transport"] = transport
    return normalized


class McpTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    mcp_entry: dict[str, Any]
    enabled: bool = True
    data: dict[str, Any] | None = None

    @field_validator("mcp_entry")
    @classmethod
    def _validate_mcp_entry(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_mcp_entry(value)


class McpTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    mcp_entry: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None

    @field_validator("mcp_entry")
    @classmethod
    def _validate_mcp_entry(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return validate_mcp_entry(value)


class McpTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    search: str | None = Field(
        default=None,
        description="按 template_id、template_name、description、mcp_entry.name 模糊搜索",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class McpTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    mcp_entry: dict[str, Any]
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


# 与库表类型上限一致：integer → 有符号 32 位；autoscale_interval → DECIMAL(10,3)

SERVICE_INT_MAX = 2_147_483_647

# K8s resource quantity：CPU 如 500m / 2 / 0.5；内存须带单位 Ki/Mi/Gi/K/M/G
_K8S_CPU_RE = re.compile(r"^(?:(?:0|[1-9]\d*)(?:\.\d+)?|\.\d+)m?$")
_K8S_MEMORY_RE = re.compile(
    r"^(?:(?:0|[1-9]\d*)(?:\.\d+)?|\.\d+)(?:Ki|Mi|Gi|K|M|G)$"
)


def _normalize_resource_quantity(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_k8s_cpu(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > 32:
        raise ValueError("at most 32 characters")
    if not _K8S_CPU_RE.fullmatch(value):
        raise ValueError(
            "must be a valid Kubernetes CPU quantity (e.g. '500m', '2', '0.5')"
        )
    return value


def _validate_k8s_memory(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > 32:
        raise ValueError("at most 32 characters")
    if not _K8S_MEMORY_RE.fullmatch(value):
        raise ValueError(
            "must be a Kubernetes memory quantity with unit "
            "Ki/Mi/Gi/K/M/G (e.g. '512Mi', '2Gi', '128M')"
        )
    return value


K8sCpuQuantity = Annotated[
    str | None,
    BeforeValidator(_normalize_resource_quantity),
    AfterValidator(_validate_k8s_cpu),
]
K8sMemoryQuantity = Annotated[
    str | None,
    BeforeValidator(_normalize_resource_quantity),
    AfterValidator(_validate_k8s_memory),
]


def is_valid_unix_abs_path(value: str) -> bool:
    """校验绝对 Unix 路径：以 / 开头，禁止 \\、空段、. 与 ..。"""
    if not value or len(value) > 512:
        return False
    if "\0" in value or "\\" in value:
        return False
    if not value.startswith("/"):
        return False
    if value == "/":
        return True
    core = value.rstrip("/")
    if not core.startswith("/"):
        return False
    for segment in core[1:].split("/"):
        if not segment or segment in (".", ".."):
            return False
    return True


def _normalize_required_nfs_path(value: Any) -> str:
    if value is None:
        return "/"
    text = str(value).strip()
    return text or "/"


def _validate_required_nfs_path(value: str) -> str:
    if not is_valid_unix_abs_path(value):
        raise ValueError(
            "must be an absolute Unix path (e.g. '/', '/data/nfs')"
        )
    return value


def _normalize_optional_unix_path(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_optional_unix_path(value: str | None) -> str | None:
    if value is None:
        return None
    if not is_valid_unix_abs_path(value):
        raise ValueError(
            "must be an absolute Unix path (e.g. '/mnt/nfs')"
        )
    return value


NfsExportPath = Annotated[
    str,
    BeforeValidator(_normalize_required_nfs_path),
    AfterValidator(_validate_required_nfs_path),
]
OptionalUnixAbsPath = Annotated[
    str | None,
    BeforeValidator(_normalize_optional_unix_path),
    AfterValidator(_validate_optional_unix_path),
]


class ServiceConfigTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    agent_image: str = Field(default="", max_length=512)
    namespace: str = Field(default="default", max_length=128)
    node_name: str | None = Field(default=None, max_length=128)
    run_as_user: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    run_as_group: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    pod_name: str = Field(default="agentserver", max_length=128)
    container_name: str = Field(default="agent", min_length=1, max_length=128)
    container_port: int = Field(default=8080, ge=1, le=65535)
    port_name: str = Field(default="http", max_length=64)
    sse_port: int = Field(default=8080, ge=1, le=65535)
    sse_path: str = Field(default="/sse", max_length=128)
    health_path: str = Field(default="/health", max_length=128)
    agent_env: dict[str, str] | None = None
    image_pull_policy: ImagePullPolicyLiteral = Field(default="IfNotPresent")
    kubeconfig: str | None = Field(default=None, max_length=512)
    readiness_initial_delay: int = Field(default=5, ge=0, le=_SERVICE_INT_MAX)
    readiness_period: int = Field(default=5, ge=1, le=_SERVICE_INT_MAX)
    ready_timeout: int = Field(default=300, ge=1, le=_SERVICE_INT_MAX)
    ready_poll_interval: int = Field(default=2, ge=1, le=_SERVICE_INT_MAX)
    nfs_server: str | None = Field(default=None, max_length=256)
    nfs_path: OptionalUnixAbsPath = None
    nfs_mount_path: OptionalUnixAbsPath = None
    agent_cpu_request: K8sCpuQuantity = None
    agent_memory_request: K8sMemoryQuantity = None
    agent_cpu_limit: K8sCpuQuantity = None
    agent_memory_limit: K8sMemoryQuantity = None
    sidecars: list[dict[str, Any]] | None = None
    agent_host_path_mounts: list[dict[str, Any]] | None = None
    agent_configmap_mounts: list[dict[str, Any]] | None = None
    agent_pvc_mounts: list[dict[str, Any]] | None = None
    main_container_id: str | None = Field(default=None, max_length=100)
    sidecar_container_ids: list[str] | None = None
    volumes: list[dict[str, Any]] | None = None
    min_idle_services: int = Field(default=0, ge=0, le=_SERVICE_INT_MAX)
    service_concurrency: int = Field(default=2, ge=1, le=_SERVICE_INT_MAX)
    service_ttl: int = Field(default=300, ge=1, le=_SERVICE_INT_MAX)
    message_timeout: int = Field(default=600, ge=1, le=_SERVICE_INT_MAX)
    session_concurrency: int = Field(default=3, ge=1, le=_SERVICE_INT_MAX)
    session_ttl: int = Field(default=60, ge=1, le=_SERVICE_INT_MAX)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ServiceConfigTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    agent_image: str | None = Field(default=None, max_length=512)
    namespace: str | None = Field(default=None, max_length=128)
    node_name: str | None = Field(default=None, max_length=128)
    run_as_user: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    run_as_group: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    pod_name: str | None = Field(default=None, max_length=128)
    container_name: str | None = Field(default=None, min_length=1, max_length=128)
    container_port: int | None = Field(default=None, ge=1, le=65535)
    port_name: str | None = Field(default=None, max_length=64)
    sse_port: int | None = Field(default=None, ge=1, le=65535)
    sse_path: str | None = Field(default=None, max_length=128)
    health_path: str | None = Field(default=None, max_length=128)
    agent_env: dict[str, str] | None = None
    image_pull_policy: ImagePullPolicyLiteral | None = None
    kubeconfig: str | None = Field(default=None, max_length=512)
    readiness_initial_delay: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    readiness_period: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    ready_timeout: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    ready_poll_interval: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    nfs_server: str | None = Field(default=None, max_length=256)
    nfs_path: OptionalUnixAbsPath = None
    nfs_mount_path: OptionalUnixAbsPath = None
    agent_cpu_request: K8sCpuQuantity = None
    agent_memory_request: K8sMemoryQuantity = None
    agent_cpu_limit: K8sCpuQuantity = None
    agent_memory_limit: K8sMemoryQuantity = None
    sidecars: list[dict[str, Any]] | None = None
    agent_host_path_mounts: list[dict[str, Any]] | None = None
    agent_configmap_mounts: list[dict[str, Any]] | None = None
    agent_pvc_mounts: list[dict[str, Any]] | None = None
    main_container_id: str | None = Field(default=None, max_length=100)
    sidecar_container_ids: list[str] | None = None
    volumes: list[dict[str, Any]] | None = None
    min_idle_services: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    service_concurrency: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    service_ttl: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    message_timeout: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    session_concurrency: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    session_ttl: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ServiceConfigTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    namespace: str | None = Field(default=None, max_length=128)
    search: str | None = Field(default=None, max_length=256)
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、agent_image、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class ServiceConfigTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    agent_image: str
    namespace: str
    node_name: str | None
    run_as_user: int | None
    run_as_group: int | None
    pod_name: str
    container_name: str
    container_port: int
    port_name: str
    sse_port: int
    sse_path: str
    health_path: str
    agent_env: dict[str, Any] | None
    image_pull_policy: str
    kubeconfig: str | None
    readiness_initial_delay: int
    readiness_period: int
    ready_timeout: int
    ready_poll_interval: int
    nfs_server: str | None
    nfs_path: str | None
    nfs_mount_path: str | None
    agent_cpu_request: str | None
    agent_memory_request: str | None
    agent_cpu_limit: str | None
    agent_memory_limit: str | None
    sidecars: list[dict[str, Any]] | None
    agent_host_path_mounts: list[dict[str, Any]] | None
    agent_configmap_mounts: list[dict[str, Any]] | None
    agent_pvc_mounts: list[dict[str, Any]] | None
    main_container_id: str | None
    sidecar_container_ids: list[str] | None
    volumes: list[dict[str, Any]] | None
    min_idle_services: int
    service_concurrency: int
    service_ttl: int
    message_timeout: int
    session_concurrency: int
    session_ttl: int
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None
