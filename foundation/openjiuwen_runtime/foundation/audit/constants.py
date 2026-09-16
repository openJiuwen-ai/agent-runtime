# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计日志默认常量（字段清单 / 码表）。"""

from __future__ import annotations

SCHEMA_VERSION_DEFAULT = "1.0.0"
PLACEHOLDER_DEFAULT = "-"
TIMESTAMP_FORMAT_DEFAULT = "yyyyMMdd-HH:mm:ss.SSS"

# 单 attribute 值最大字符数（对齐 telemetry attribute_value_max_length）。
ATTRIBUTE_VALUE_MAX_LENGTH_DEFAULT = 10240

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

DEFAULT_CONTENT_FIELDS: tuple[str, ...] = (
    "UID",
    "CUSTID",
    "SRCIP",
    "DSTIP",
    "COST",
    "UA",
    "EVT",
    "MSG",
    "RSPCD",
    "SUBMDL",
    "PROC",
    "SVRNAM",
    "ACTION",
    "SANDBOXID",
    "RESULT",
)

# emit 前校验的 required（UA|EVT 随 event_type 二选一另判）。
DEFAULT_REQUIRED_FIELDS: tuple[str, ...] = (
    "timestamp",
    "level",
    "UID",
    "CUSTID",
    "SRCIP",
    "DSTIP",
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

EVENT_TYPE_ATTR = "event_type"

NTP_SYNC_INTERVAL_DEFAULT = "300s"
NTP_MAX_OFFSET_MS_DEFAULT = 500
NTP_FAILOVER_DEFAULT = True

OTEL_LOGGER_NAME = "openjiuwen.audit"
OTEL_PROTOCOL_GRPC = "grpc"
OTEL_PROTOCOL_HTTP = "http"
OTEL_PROTOCOLS: tuple[str, ...] = (OTEL_PROTOCOL_GRPC, OTEL_PROTOCOL_HTTP)

SERVICE_NAME_PREFIX = "jiuwenclaw-"
