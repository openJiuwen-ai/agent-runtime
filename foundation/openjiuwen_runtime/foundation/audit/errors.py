# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计日志模块异常。"""

from __future__ import annotations


class AuditError(Exception):
    """审计模块基类异常。"""


class SchemaValidationError(AuditError):
    """自定义 schema 未通过合规校验门（FR1.2.3），schema 不得生效。"""


class FormatError(AuditError):
    """按 schema 格式化日志行失败（缺红线字段、残留裸分隔符等）。"""


class ClockSyncError(AuditError):
    """NTP 同步过程中的可恢复错误（全部源不可达时不抛，仅降级）。"""


class AuditNotImplementedError(AuditError, NotImplementedError):
    """框架已预留、对应 FR 尚未实现。"""
