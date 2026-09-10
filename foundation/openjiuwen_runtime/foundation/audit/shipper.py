# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR6 采集与外送（尚未实现）。"""

from __future__ import annotations

from .config import ShipperConfig
from .errors import AuditNotImplementedError


class AuditShipper:
    """读取 SEC-*.log，转换管道格式、再脱敏后外送到可配置消费方。"""

    def __init__(self, config: ShipperConfig | None = None) -> None:
        self.config = config or ShipperConfig()

    def ship_line(self, line: str) -> None:
        raise AuditNotImplementedError("FR6 shipper is not implemented")
