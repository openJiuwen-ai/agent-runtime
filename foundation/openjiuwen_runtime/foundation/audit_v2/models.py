# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Audit event types — canonical definitions (UA/EVT)."""

from __future__ import annotations

from enum import Enum


class AuditType(str, Enum):
    """Audit event categories — behavioral audit (UA/EVT)."""

    USER_ACTION = "ua"
    """User/agent behavior audit — successful normal actions."""

    SECURITY_EVENT = "evt"
    """Behavioral audit — failures, violations, exceptions, security alerts."""

    @classmethod
    def for_outcome(cls, success: bool) -> "AuditType":
        """Map a success flag to the UA/EVT category."""
        return cls.USER_ACTION if success else cls.SECURITY_EVENT


# SUBMDL 规范取值
SUBMDL_GATEWAY = "gateway"
SUBMDL_AGENT = "agent"
SUBMDL_API_CLIENT = "api_client"
SUBMDL_FILE = "file"
SUBMDL_ALERT = "alert"
SUBMDL_SANDBOX = "sandbox"
