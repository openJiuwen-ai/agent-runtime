# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计日志模块异常。"""

from __future__ import annotations


class AuditError(Exception):
    """审计模块基类异常。"""


class SchemaValidationError(AuditError):
    """自定义 format / otel 未通过预检，配置不得生效。"""


class FormatError(AuditError):
    """打点参数非法（如缺 level / 非法 event_type）。"""

