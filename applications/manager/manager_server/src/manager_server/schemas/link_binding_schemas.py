"""实例链路绑定 API 模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class LinkBindingCreateBody(BaseModel):
    mtls_binding_id: str = Field(..., min_length=1, max_length=64)
    mtls_binding_epoch: int = Field(..., ge=1)
    mtls_gateway_endpoint: str = Field(..., min_length=1, max_length=128)
    mtls_runtime_endpoint: str = Field(..., min_length=1, max_length=128)
    manager_cert_fingerprint: str = Field(..., pattern=_SHA256_PATTERN)
    gateway_cert_fingerprint: str = Field(..., pattern=_SHA256_PATTERN)
    runtime_cert_fingerprint: str = Field(..., pattern=_SHA256_PATTERN)
    agentserver_cert_fingerprint: str = Field(..., pattern=_SHA256_PATTERN)
    trust_bundle_ref: str = Field(..., min_length=1, max_length=512)
    updated_by: str = Field(default="system", min_length=1, max_length=64)
    data: dict[str, Any] | None = None

    @field_validator(
        "mtls_binding_id",
        "mtls_gateway_endpoint",
        "mtls_runtime_endpoint",
        "trust_bundle_ref",
        "updated_by",
        mode="before",
    )
    @classmethod
    def strip_required_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("value must not be blank")
        return value

    @field_validator(
        "manager_cert_fingerprint",
        "gateway_cert_fingerprint",
        "runtime_cert_fingerprint",
        "agentserver_cert_fingerprint",
        mode="before",
    )
    @classmethod
    def normalize_fingerprint(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_distinct_endpoints(self) -> LinkBindingCreateBody:
        if self.mtls_gateway_endpoint.strip() == self.mtls_runtime_endpoint.strip():
            raise ValueError("mtls_gateway_endpoint and mtls_runtime_endpoint must differ")
        return self


class LinkBindingView(BaseModel):
    jiuwenclaw_id: str
    mtls_binding_id: str
    mtls_binding_epoch: int
    protocol_version: str
    mtls_gateway_endpoint: str
    mtls_runtime_endpoint: str
    manager_cert_fingerprint: str
    gateway_cert_fingerprint: str
    runtime_cert_fingerprint: str
    agentserver_cert_fingerprint: str
    trust_bundle_ref: str
    status: str
    bound_at: str
    unbound_at: str | None = None
    created_at: str
    updated_at: str
    updated_by: str
    data: dict[str, Any] | None = None
