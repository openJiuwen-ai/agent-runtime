# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR5 日志存储 / 轮转 / 分级留存（尚未实现）。"""

from __future__ import annotations

from .config import StorageConfig, RuntimeIdentityConfig
from .errors import AuditNotImplementedError


class AuditStorage:
    """SEC- 前缀独立目录、大小+时间双重轮转、分级留存。"""

    def __init__(
        self,
        config: StorageConfig | None = None,
        identity: RuntimeIdentityConfig | None = None,
    ) -> None:
        self.config = config or StorageConfig()
        self.identity = identity or RuntimeIdentityConfig()

    def current_path(self) -> str:
        raise AuditNotImplementedError("FR5 storage is not implemented")

    def append_line(self, line: str) -> None:
        raise AuditNotImplementedError("FR5 storage is not implemented")
