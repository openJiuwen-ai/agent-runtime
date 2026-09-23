# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""配额业务包（workspace-quota 等）。"""

from .workspace_quota_policy import (
    APPROVAL_POLICY_PRIORITY,
    SOURCE_APPROVAL,
    SOURCE_MANUAL,
    WorkspaceQuotaPolicyService,
    select_effective_policy,
)

__all__ = (
    "APPROVAL_POLICY_PRIORITY",
    "SOURCE_APPROVAL",
    "SOURCE_MANUAL",
    "WorkspaceQuotaPolicyService",
    "select_effective_policy",
)
