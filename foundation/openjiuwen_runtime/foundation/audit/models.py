# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计日志运行时模型。"""

from __future__ import annotations

from dataclasses import dataclass

from .config import AuditLogConfig, OtelConfig
from .schema import FormatSpec


@dataclass(frozen=True)
class RuntimeIdentity:
    """部署身份，写入头部 data_center / system_code / node。"""

    data_center: str = "-"
    system_code: str = "-"
    node: str = "-"


@dataclass(frozen=True)
class AuditSnapshot:
    """一次 log_audit 调用时捕获的配置快照（与在途 emit 隔离）。"""

    format: FormatSpec
    identity: RuntimeIdentity
    otel: OtelConfig
    service: str | None
    attribute_value_max_length: int

    @classmethod
    def from_config(cls, config: AuditLogConfig) -> AuditSnapshot:
        ident = config.identity
        return cls(
            format=config.format,
            identity=RuntimeIdentity(
                data_center=ident.data_center,
                system_code=ident.system_code,
                node=ident.node,
            ),
            otel=config.otel,
            service=config.service,
            attribute_value_max_length=config.attribute_value_max_length,
        )
