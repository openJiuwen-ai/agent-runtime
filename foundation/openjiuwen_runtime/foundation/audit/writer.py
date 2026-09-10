# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR4 异步输出（尚未实现）。"""

from __future__ import annotations

from .config import WriterConfig
from .errors import AuditNotImplementedError


class AuditWriter:
    """强制异步落盘：入队立即返回，后台格式化/脱敏/写文件。"""

    def __init__(self, config: WriterConfig | None = None) -> None:
        self.config = config or WriterConfig()

    def enqueue(self, line: str) -> None:
        raise AuditNotImplementedError("FR4 async writer is not implemented")

    def close(self) -> None:
        raise AuditNotImplementedError("FR4 async writer is not implemented")
