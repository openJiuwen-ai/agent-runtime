# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""安全审计日志 SDK（行为审计 UA/EVT，OTEL Logs 通道）。
"""

from .context import (
    bind_request_context,
    bind_routing,
    lookup_routing,
    reset_request_context,
    snapshot as audit_context_snapshot,
)
from .emitter import (
    audit_enabled,
    audit_error,
    audit_info,
    audit_warn,
    current_agent_name,
    log_audit,
    log_event,
)
from .http import install_http_audit_middleware
from .models import AuditType, SUBMDL_AGENT, SUBMDL_ALERT, SUBMDL_API_CLIENT
from .models import SUBMDL_FILE, SUBMDL_GATEWAY, SUBMDL_SANDBOX

__all__ = [
    "log_audit",
    "audit_info",
    "audit_warn",
    "audit_error",
    "log_event",
    "install_http_audit_middleware",
    "AuditType",
    "bind_request_context",
    "reset_request_context",
    "bind_routing",
    "lookup_routing",
    "audit_context_snapshot",
    "audit_enabled",
    "current_agent_name",
    "SUBMDL_GATEWAY",
    "SUBMDL_AGENT",
    "SUBMDL_API_CLIENT",
    "SUBMDL_FILE",
    "SUBMDL_ALERT",
    "SUBMDL_SANDBOX",
]
