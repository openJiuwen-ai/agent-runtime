# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR8 配置下发与滚动升级（尚未实现）。"""

from __future__ import annotations

from typing import Any, Mapping

from .config import DeployConfig
from .errors import AuditNotImplementedError


class AuditConfigDeployer:
    """管理页预检 → 分服务落库 → AgentServer 池滚动 → 共库拉取生效。"""

    def __init__(self, config: DeployConfig | None = None) -> None:
        self.config = config or DeployConfig()

    def deploy(self, payload: Mapping[str, Any]) -> None:
        raise AuditNotImplementedError("FR8 config deploy is not implemented")
