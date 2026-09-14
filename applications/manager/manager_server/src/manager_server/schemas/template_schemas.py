"""模板 API 请求/响应模型。

涵盖 model_template、extension_config_template、skill_prebuilt_template、
permissions_template、service_config_template、agent_template。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlparse, urlsplit

from croniter import croniter
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from manager_server.schemas.safe_text import SafeTextMixin

from manager_server.infrastructure.template_ref import (
    normalize_template_ref,
    normalize_template_ref_optional,
)

ModelTypeLiteral = Literal["default", "video", "audio", "vision", "image_gen"]
ExtensionComponentLiteral = Literal["gateway", "agent_server"]
ExtensionHookTypeLiteral = Literal["pre_request", "post_request", "error", "schedule"]
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


def _validate_a2a_card_path(value: str) -> str:
    """Card 地址只能是同源绝对路径，禁止换源和目录回退。"""
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        raise ValueError("must be an absolute same-origin path starting with a single '/'")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("must not contain a scheme, host, query, or fragment")
    decoded_path = unquote(parsed.path)
    if decoded_path.startswith("//") or "\\" in decoded_path:
        raise ValueError("must remain a same-origin path after URL decoding")
    decoded_segments = decoded_path.split("/")
    if ".." in decoded_segments:
        raise ValueError("must not contain parent-directory segments")
    return value


ApiBaseUrl = Annotated[
    str,
    Field(min_length=1, max_length=512),
    AfterValidator(_validate_http_url),
]
A2ASourceUrl = Annotated[
    str,
    Field(min_length=1, max_length=2048),
    AfterValidator(_validate_http_url),
]
A2ACardPath = Annotated[
    str,
    Field(min_length=1, max_length=512),
    AfterValidator(_validate_a2a_card_path),
]

_SKILL_SOURCE_URL_MAX_LEN = 2048

def _optional_skill_source_url(value: Any) -> str | None:
    """空字符串视为未填；有值则按 http(s) URL 校验。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > _SKILL_SOURCE_URL_MAX_LEN:
        raise ValueError("package_url must be at most 2048 characters")
    return _validate_http_url(text)


OptionalSkillSourceUrl = Annotated[
    str | None,
    BeforeValidator(_optional_skill_source_url),
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
        description="按模型类型筛选，如 default / video / audio / vision / image_gen",
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


class SkillPrebuiltTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str = Field(..., min_length=1, max_length=512)
    package_url: OptionalSkillSourceUrl = None
    source_id: str | None = Field(default=None, max_length=64)
    version_id: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _require_install_path(self) -> SkillPrebuiltTemplateCreateBody:
        package_url = (self.package_url or "").strip()
        source_id = (self.source_id or "").strip()
        version_id = (self.version_id or "").strip()
        if source_id and version_id:
            return self
        if source_id or version_id:
            raise ValueError(
                "invalid_template: provider path requires source_id and version_id"
            )
        if package_url:
            return self
        raise ValueError(
            "invalid_template: cannot infer install path from fields"
        )


class SkillPrebuiltTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str | None = Field(default=None, min_length=1, max_length=512)
    package_url: OptionalSkillSourceUrl = None
    source_id: str | None = Field(default=None, max_length=64)
    version_id: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class SkillPrebuiltTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    skill_id: str | None = Field(default=None, max_length=512)
    package_url: str | None = Field(default=None, max_length=2048)
    source_id: str | None = Field(default=None, max_length=64)
    search: str | None = Field(
        default=None,
        description=(
            "按 template_id、template_name、description、package_url、"
            "skill_id、source_id、version_id 模糊搜索"
        ),
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：template_name、description、package_url、skill_id、"
            "source_id、version_id、updated_at"
        ),
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class SkillPrebuiltTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    skill_id: str
    package_url: str | None = None
    source_id: str | None = None
    version_id: str | None = None
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


A2AAccessPolicyMode = Literal["allowlist", "denylist"]


class A2AOutboundTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    discovery_id: str = Field(..., min_length=1, max_length=128)
    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    a2a_tags: list[str] | None = None
    credential: str | None = Field(default=None, max_length=4096)
    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    sync_wait_seconds: float = Field(default=120.0, gt=0)
    enabled: bool = True
    data: dict[str, Any] | None = None


class A2AOutboundDiscoveryBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: A2ASourceUrl
    card_path: A2ACardPath | None = None


class A2ADiscoverySettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_http: bool
    allow_private_network: bool
    allow_public_http: bool


class A2AConfirmRevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accept: bool


class A2AOutboundTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    a2a_tags: list[str] | None = None
    credential: str | None = Field(default=None, max_length=4096)
    clear_credential: bool = False
    connect_timeout_seconds: float | None = Field(default=None, gt=0)
    sync_wait_seconds: float | None = Field(default=None, gt=0)
    enabled: bool | None = None
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_credential_operation(self):
        if self.clear_credential and (self.credential or "").strip():
            raise ValueError("credential and clear_credential cannot be used together")
        return self


class A2AOutboundTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    search: str | None = Field(default=None, max_length=256)
    sort_by: str | None = None
    sort_order: str | None = None


class A2AOutboundTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    a2a_tags: list[str] | None
    source_url: str
    card_path: str
    agent_card: dict[str, Any]
    card_fingerprint: str
    card_revision: int
    selected_interface: dict[str, Any]
    credential_configured: bool
    connect_timeout_seconds: float
    sync_wait_seconds: float
    enabled: bool
    pending_revision: dict[str, Any] | None
    last_checked_at: str | None
    last_error_code: str | None
    last_error_summary: str | None
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class A2AOutboundTemplateEditOut(A2AOutboundTemplateOut):
    credential: str | None


class A2AAccessPolicyTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    policy_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    mode: A2AAccessPolicyMode
    member_template_ids: list[TemplateIdPath] = Field(default_factory=list)
    enabled: bool = True
    data: dict[str, Any] | None = None

    @field_validator("member_template_ids")
    @classmethod
    def deduplicate_members(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class A2AAccessPolicyTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    policy_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    mode: A2AAccessPolicyMode | None = None
    member_template_ids: list[TemplateIdPath] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None

    @field_validator("member_template_ids")
    @classmethod
    def deduplicate_members(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else list(dict.fromkeys(value))


class A2AAccessPolicyTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    mode: A2AAccessPolicyMode | None = None
    search: str | None = Field(default=None, max_length=256)
    sort_by: str | None = None
    sort_order: str | None = None


class A2AAccessPolicyTemplateOut(BaseModel):
    id: int
    policy_id: str
    policy_name: str
    description: str | None
    mode: A2AAccessPolicyMode
    member_template_ids: list[str]
    enabled: bool
    revision: int
    reference_count: int = 0
    data: dict[str, Any] | None = None
    created_at: str | None
    updated_at: str | None


class PermissionsTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    enabled: bool = True
    body: dict[str, Any] = Field(
        ...,
        description=("完整 permissions 段，结构与 config.yaml::permissions 一致"),
    )
    data: dict[str, Any] | None = None


class PermissionsTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    body: dict[str, Any] | None = None
    data: dict[str, Any] | None = None


class PermissionsTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    search: str | None = Field(
        default=None,
        description="按 template_id、template_name、description 模糊搜索",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class PermissionsTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    enabled: bool
    body: dict[str, Any]
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


_VALID_MCP_TRANSPORTS = frozenset(
    {
        "stdio",
        "sse",
        "http",
        "streamable-http",
        "streamable_http",
    }
)


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
            "mcp_entry.transport must be one of: " + ", ".join(sorted(_VALID_MCP_TRANSPORTS))
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


# 与库表类型上限一致：integer → 有符号 32 位

_SERVICE_INT_MAX = 2_147_483_647


class ServiceConfigTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    namespace: str = Field(default="default", max_length=128)
    node_name: str | None = Field(default=None, max_length=128)
    fs_group: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    pod_name: str = Field(default="agentserver", max_length=128)
    sse_path: str = Field(default="/sse", max_length=128)
    kubeconfig: str | None = Field(default=None, max_length=512)
    ready_timeout: int = Field(default=300, ge=1, le=_SERVICE_INT_MAX)
    ready_poll_interval: int = Field(default=2, ge=1, le=_SERVICE_INT_MAX)
    main_container_id: str | None = Field(default=None, max_length=100)
    sidecar_container_ids: list[str] | None = None
    volumes: list[dict[str, Any]] | None = None
    min_idle_pods: int = Field(default=0, ge=0, le=_SERVICE_INT_MAX)
    pod_concurrency: int = Field(default=2, ge=1, le=_SERVICE_INT_MAX)
    pod_ttl: int = Field(default=300, ge=1, le=_SERVICE_INT_MAX)
    message_timeout: int = Field(default=600, ge=1, le=_SERVICE_INT_MAX)
    scope_concurrency: int = Field(default=3, ge=1, le=_SERVICE_INT_MAX)
    session_ttl: int = Field(default=60, ge=1, le=_SERVICE_INT_MAX)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ServiceConfigTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    namespace: str | None = Field(default=None, max_length=128)
    node_name: str | None = Field(default=None, max_length=128)
    fs_group: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    pod_name: str | None = Field(default=None, max_length=128)
    sse_path: str | None = Field(default=None, max_length=128)
    kubeconfig: str | None = Field(default=None, max_length=512)
    ready_timeout: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    ready_poll_interval: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    main_container_id: str | None = Field(default=None, max_length=100)
    sidecar_container_ids: list[str] | None = None
    volumes: list[dict[str, Any]] | None = None
    min_idle_pods: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    pod_concurrency: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    pod_ttl: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    message_timeout: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    scope_concurrency: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
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
        description="排序字段：template_name、description、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class ServiceConfigTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    namespace: str
    node_name: str | None
    fs_group: int | None
    pod_name: str
    sse_path: str
    kubeconfig: str | None
    ready_timeout: int
    ready_poll_interval: int
    main_container_id: str | None
    sidecar_container_ids: list[str] | None
    volumes: list[dict[str, Any]] | None
    main_image: str | None = None
    min_idle_pods: int
    pod_concurrency: int
    pod_ttl: int
    message_timeout: int
    scope_concurrency: int
    session_ttl: int
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None
