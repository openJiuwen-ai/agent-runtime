# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""foundation.audit 模块设计（本阶段）。

需求基线：《安全审计日志软件需求规格说明书》；落地裁剪见《安全审计日志-本阶段反串讲设计》。

本阶段交付：字段清单 format + ContextVar 合并 + ``log_audit`` → OTEL Logs emit。
不做：管道行、本地 audit 文件、NTP 同步实现、脱敏、SIEM、Manager Web。
"""

from __future__ import annotations

# 包结构
#
#   schema.py       FormatSpec（header/content/required 字段清单）
#   validator.py    预检门
#   config.py       AuditLogConfig / OtelConfig / NtpConfig（ntp 仅解析）
#   context.py      session_id/request_id/user_id/src_ip/dst_ip
#   attributes.py   build_audit_attributes
#   emitter.py      Noop / Memory / OTEL Logs（optional audit-otel）
#   facade.py       AuditManager / log_audit / apply_config
#   clock.py        LocalClock + format_timestamp
#
# 调用链：
#
#   apply_config(payload) → validate → 替换 snapshot → rebind emitter
#   log_audit(event_type, level=..., **fields)
#        → 捕获 AuditSnapshot
#        → build_audit_attributes（显式 > ContextVar > 自动/配置）
#        → emitter.emit（Batch；失败/未就绪 → warning，不抛业务异常）
#
# 字段约定：
#   event_type 必填（UA|EVT），写入 attributes["event_type"]
#   level 必填；audit_info/warn/error 自带
#   CUSTID 恒 placeholder；UID=system 仅显式传入
#   service 进程本地；Resource service.name = jiuwenclaw-{service}
