# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR7 访问控制与完整性（尚未实现）。"""

from __future__ import annotations

from .config import AccessConfig
from .errors import AuditNotImplementedError


class AuditAccessControl:
    """最小化权限：业务无读、管理员只读、writer 仅追加。"""

    def __init__(self, config: AccessConfig | None = None) -> None:
        self.config = config or AccessConfig()

    def assert_readable(self, role: str) -> None:
        raise AuditNotImplementedError("FR7 access control is not implemented")

    def record_management_action(self, action: str, **fields: object) -> None:
        raise AuditNotImplementedError("FR7 access control is not implemented")
