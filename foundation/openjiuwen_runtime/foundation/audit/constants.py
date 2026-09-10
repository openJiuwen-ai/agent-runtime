# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计日志默认常量（FR1 默认 schema 与码表）。

默认配置不含任何环境特定地址（NTP / 外送 endpoint 均须部署注入）。
"""

from __future__ import annotations

SCHEMA_VERSION_DEFAULT = "1.0.0"

HEADER_SEPARATOR_DEFAULT = "|"
PLACEHOLDER_DEFAULT = "-"
TIMESTAMP_FORMAT_DEFAULT = "yyyyMMdd-HH:mm:ss.SSS"

CONTENT_KEYWORD_PREFIX_DEFAULT = "#"
CONTENT_ELEMENT_SEPARATOR_DEFAULT = "|@|"
CONTENT_KV_SEPARATOR_DEFAULT = "="
ESCAPE_MODE_REPLACE = "replace"
ESCAPE_MODE_ESCAPE = "escape"
PIPE_ESCAPE_DEFAULT = "_"

DEFAULT_HEADER_FIELDS: tuple[str, ...] = (
    "schema_version",
    "timestamp",
    "level",
    "data_center",
    "system_code",
    "node",
    "trace_id",
    "txn_seq",
    "pid",
    "tid",
    "caller",
)

# 不可削弱红线字段（逻辑语义标识，跨头部/内容层）。
DEFAULT_REDLINE_FIELDS: tuple[str, ...] = (
    "timestamp",
    "UID",
    "UA",
    "EVT",
    "RSPCD",
    "SRCIP",
    "DSTIP",
    "CUSTID",
    "SUBMDL",
    "PROC",
)

# 内容 required 档（无值必须占位）。UA / EVT 按关键字二选一必填。
CONTENT_REQUIRED_FIELDS: tuple[str, ...] = (
    "UID",
    "CUSTID",
    "SRCIP",
    "DSTIP",
    "RSPCD",
    "SUBMDL",
    "PROC",
)

CONTENT_RECOMMENDED_FIELDS: tuple[str, ...] = (
    "TID",
    "TXNO",
    "COST",
    "SVRNAM",
    "MSG",
)

# 输出顺序：与 SRS 示例对齐；recommended 仅在调用方提供时输出。
CONTENT_OUTPUT_ORDER: tuple[str, ...] = (
    "UID",
    "CUSTID",
    "SRCIP",
    "DSTIP",
    "TID",
    "TXNO",
    "COST",
    "SVRNAM",
    "UA",
    "EVT",
    "MSG",
    "RSPCD",
    "SUBMDL",
    "PROC",
)

LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")

RSPCD_SUCCESS = "0000"
RSPCD_KNOWN: dict[str, str] = {
    "0000": "操作成功",
    "E001": "认证失败",
    "E002": "越权访问",
    "E003": "会话过期",
    "E004": "参数错误",
    "E005": "系统异常",
    "E999": "未知错误",
}

KEYWORD_UA = "UA"
KEYWORD_EVT = "EVT"
KEYWORDS: tuple[str, ...] = (KEYWORD_UA, KEYWORD_EVT)

NTP_SYNC_INTERVAL_DEFAULT = "300s"
NTP_MAX_OFFSET_MS_DEFAULT = 500
NTP_FAILOVER_DEFAULT = True

EVENT_NTP_SOURCE_FAILOVER = "ntp_source_failover"
EVENT_NTP_OFFSET_EXCEEDED = "ntp_offset_exceeded"

SUBMDL_AUDIT = "audit"
PROC_NTP_SYNC = "ntp_sync"
