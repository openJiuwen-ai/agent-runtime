# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""统一打点门面。当前实现 FR1 格式化；落盘 / 脱敏 / 外送见后续 FR。"""

from __future__ import annotations

from typing import Any, Mapping

from .clock import NtpConfig, SyncedClock
from .config import AuditLogConfig, default_config
from .errors import AuditNotImplementedError
from .formatter import format_audit_line, format_clock_event
from .models import RuntimeIdentity
from .schema import AuditLogSchema


class AuditFacade:
    """SRS §5.1 `log_audit` 的载体。

    FR1：`format_line` / 加载配置时过合规校验门 / NTP 时钟。
    FR4 起的持久化尚未实现，`log_audit` 明确拒绝静默丢弃。
    """

    def __init__(
        self,
        config: AuditLogConfig | None = None,
        *,
        clock: SyncedClock | None = None,
    ) -> None:
        self.config = config or default_config()
        self.config.validate()
        self.clock = clock or SyncedClock(self.config.ntp)
        ident = self.config.identity
        self.identity = RuntimeIdentity(
            data_center=ident.data_center,
            system_code=ident.system_code,
            node=ident.node,
        )

    @property
    def schema(self) -> AuditLogSchema:
        return self.config.format

    @property
    def ntp(self) -> NtpConfig:
        return self.config.ntp

    def format_line(
        self,
        *,
        level: str = "INFO",
        keyword: str | None = "UA",
        elements: Mapping[str, Any] | None = None,
        header: Mapping[str, Any] | None = None,
        ext: Mapping[str, Any] | None = None,
        caller: str | None = None,
    ) -> str:
        return format_audit_line(
            schema=self.schema,
            clock=self.clock,
            identity=self.identity,
            level=level,
            keyword=keyword,
            elements=elements,
            header=header,
            ext=ext,
            caller=caller,
        )

    def format_pending_clock_events(self) -> list[str]:
        return [
            format_clock_event(
                event, schema=self.schema, identity=self.identity, clock=self.clock
            )
            for event in self.clock.drain_events()
        ]

    def log_audit(self, event_type: str, **fields: Any) -> None:
        """SRS 对内接口。持久化依赖 FR4，当前禁止假装已落盘。"""
        raise AuditNotImplementedError(
            "log_audit persistence requires FR4; use format_line() for FR1"
        )


def log_audit(event_type: str, **fields: Any) -> None:
    """模块级入口，语义同 AuditFacade.log_audit。"""
    raise AuditNotImplementedError(
        "log_audit persistence requires FR4; use format_audit_line() for FR1"
    )
