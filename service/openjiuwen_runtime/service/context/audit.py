# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Request audit events and the default structured logging sink."""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class AuditEvent:
    """A transport-independent audit record."""

    action: str
    outcome: str = "success"
    actor: str | None = None
    user_id: str | None = None
    resource: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    session_id: str | None = None
    msg_type: str | None = None
    instance_id: str | None = None
    replica_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class AuditLogger(Protocol):
    """Protocol implemented by an audit event sink."""

    async def write(self, event: AuditEvent) -> None:
        ...


class LoggingAuditLogger:
    """Write structured audit records through a standard-library logger."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        level: int | str = logging.INFO,
    ) -> None:
        self.logger = logger or logging.getLogger("openjiuwen_runtime.service.audit")
        if isinstance(level, str):
            resolved_level = getattr(logging, level.upper(), None)
            if not isinstance(resolved_level, int):
                raise ValueError(f"unknown audit logging level: {level!r}")
            level = resolved_level
        if not isinstance(level, int):
            raise TypeError("audit logging level must be an integer or level name")
        self.level = level

    async def write(self, event: AuditEvent) -> None:
        self.logger.log(
            self.level,
            "audit action=%s outcome=%s actor=%s resource=%s request_id=%s "
            "trace_id=%s user_id=%s session_id=%s msg_type=%s instance_id=%s details=%s",
            event.action,
            event.outcome,
            event.actor,
            event.resource,
            event.request_id,
            event.trace_id,
            event.user_id,
            event.session_id,
            event.msg_type,
            event.instance_id,
            event.details,
            extra={"audit": event.to_dict()},
        )


class NoopAuditLogger:
    """An explicit no-op sink for applications that disable audit logging."""

    async def write(self, event: AuditEvent) -> None:
        return None


__all__ = ["AuditEvent", "AuditLogger", "LoggingAuditLogger", "NoopAuditLogger"]
