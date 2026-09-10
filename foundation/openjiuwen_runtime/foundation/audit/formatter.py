# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""按 schema 格式化 / 解析审计日志行（FR1）。"""

from __future__ import annotations

import inspect
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Mapping, Protocol

from .constants import (
    CONTENT_OUTPUT_ORDER,
    CONTENT_RECOMMENDED_FIELDS,
    CONTENT_REQUIRED_FIELDS,
    ESCAPE_MODE_ESCAPE,
    ESCAPE_MODE_REPLACE,
    KEYWORD_EVT,
    KEYWORD_UA,
    KEYWORDS,
    LEVELS,
    PLACEHOLDER_DEFAULT,
    PROC_NTP_SYNC,
    RSPCD_KNOWN,
    SUBMDL_AUDIT,
)
from .errors import FormatError
from .models import ClockEvent, ParsedAuditLine, RuntimeIdentity
from .schema import AuditLogSchema, ContentSchema, default_schema


class Clock(Protocol):
    def now(self) -> float: ...


_CALLER_LINE_RE = re.compile(r":\d+$")
_RSPCD_RE = re.compile(r"^(0000|E\d{3})$")
_AUDIT_MODULE_PREFIX = "openjiuwen_runtime.foundation.audit"


def format_timestamp(ts: float, pattern: str = "yyyyMMdd-HH:mm:ss.SSS") -> str:
    """将 Unix 时间戳格式化为 schema 声明的时间格式（默认毫秒精度）。"""
    dt = datetime.fromtimestamp(ts)
    if pattern == "yyyyMMdd-HH:mm:ss.SSS":
        return dt.strftime("%Y%m%d-%H:%M:%S") + f".{dt.microsecond // 1000:03d}"
    # 仅支持默认模式；其它模式未实现时仍输出默认，避免静默丢毫秒。
    raise FormatError(f"unsupported timestamp format: {pattern!r}")


def _stringify(value: Any, placeholder: str) -> str:
    if value is None:
        return placeholder
    text = str(value)
    if text == "":
        return placeholder
    return text


def escape_value(value: str, content: ContentSchema) -> str:
    """按 escape_mode 处理值，保证不残留裸 '|'。"""
    if content.escape_mode == ESCAPE_MODE_REPLACE:
        out = (
            value.replace("|", content.pipe_escape)
            .replace("\r\n", " ")
            .replace("\n", " ")
            .replace("\r", " ")
        )
        if "|" in out:
            raise FormatError("bare '|' remained after replace escape")
        return out
    if content.escape_mode == ESCAPE_MODE_ESCAPE:
        out = (
            value.replace("\\", "\\\\")
            .replace("|", r"\|")
            .replace("=", r"\=")
            .replace("\n", r"\n")
            .replace("\r", r"\r")
        )
        if _has_bare_pipe(out):
            raise FormatError("bare '|' remained after escape mode")
        return out
    raise FormatError(f"unknown escape_mode: {content.escape_mode!r}")


def _has_bare_pipe(text: str) -> bool:
    index = 0
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "|":
            return True
        index += 1
    return False


def capture_caller(skip_prefixes: tuple[str, ...] = (_AUDIT_MODULE_PREFIX,)) -> str:
    """自动采集 `模块.函数:行号`，跳过本包栈帧。"""
    for frame_info in inspect.stack()[1:]:
        module = inspect.getmodule(frame_info.frame)
        name = module.__name__ if module is not None else ""
        if any(name == p or name.startswith(p + ".") for p in skip_prefixes):
            continue
        if name.startswith("inspect") or name == "threading":
            continue
        func = frame_info.function
        lineno = frame_info.lineno
        location = f"{name}.{func}:{lineno}" if name else f"{func}:{lineno}"
        return location
    return PLACEHOLDER_DEFAULT


def _auto_tid() -> str:
    ident = threading.get_ident()
    return f"tid-{ident:x}"


def _normalize_ext_key(key: str) -> str:
    if key.startswith("x_"):
        return key
    return f"x_{key}"


def _validate_level(level: str) -> str:
    upper = level.upper()
    if upper not in LEVELS:
        raise FormatError(f"invalid level {level!r}; expected one of {LEVELS}")
    return upper


def _validate_keyword(keyword: str | None, required: bool) -> str | None:
    if keyword is None or keyword == "":
        if required:
            raise FormatError("content keyword is required by schema")
        return None
    token = keyword[1:] if keyword.startswith("#") else keyword
    token = token.upper()
    if token not in KEYWORDS:
        raise FormatError(f"keyword must be UA or EVT, got {keyword!r}")
    return token


def _validate_rspcd(value: str) -> None:
    if not _RSPCD_RE.match(value):
        raise FormatError(
            f"RSPCD must be 0000 or E + 3 digits (known: {sorted(RSPCD_KNOWN)}), got {value!r}"
        )


def _content_keys(keyword: str | None, elements: Mapping[str, Any]) -> list[str]:
    required = set(CONTENT_REQUIRED_FIELDS)
    if keyword == KEYWORD_UA:
        required.add("UA")
    elif keyword == KEYWORD_EVT:
        required.add("EVT")
    elif "UA" not in elements and "EVT" not in elements:
        required.add("UA")

    keys: list[str] = []
    for name in CONTENT_OUTPUT_ORDER:
        if name in required or name in elements:
            keys.append(name)
    extras = [
        name
        for name in elements
        if name not in CONTENT_OUTPUT_ORDER
        and name not in {"EXT"}
        and not str(name).startswith("x_")
        and name not in {"ACTION", "SANDBOXID", "RESULT"}
    ]
    keys.extend(extras)
    return keys


def format_audit_line(
    *,
    schema: AuditLogSchema | None = None,
    clock: Clock | None = None,
    identity: RuntimeIdentity | None = None,
    level: str = "INFO",
    keyword: str | None = KEYWORD_UA,
    elements: Mapping[str, Any] | None = None,
    header: Mapping[str, Any] | None = None,
    ext: Mapping[str, Any] | None = None,
    now: float | None = None,
    caller: str | None = None,
) -> str:
    """按 schema 产出一行审计日志（头部 + 内容）。

    调用方只需给业务要素；头部 schema_version / timestamp / pid / tid / caller
    可自动补全。空值一律占位，禁止省略已声明的头部字段。
    """
    schema = schema or default_schema()
    identity = identity or RuntimeIdentity()
    header_in = dict(header or {})
    elements_in = dict(elements or {})
    ext_in = dict(ext or {})
    placeholder = schema.header.placeholder
    level_n = _validate_level(level)
    keyword_n = _validate_keyword(keyword, schema.content.keyword_required)

    # 沙箱扩展要素不挤占规范管道位置，转入 EXT。
    for src, dst in (("ACTION", "x_action"), ("SANDBOXID", "x_sandbox_id"), ("RESULT", "x_result")):
        if src in elements_in and dst not in ext_in and src not in ext_in:
            ext_in[dst] = elements_in.pop(src)

    ts_pattern = schema.header.formats.get("timestamp", "yyyyMMdd-HH:mm:ss.SSS")
    if now is None:
        now = clock.now() if clock is not None else time.time()

    auto_caller = caller if caller is not None else capture_caller()
    defaults: dict[str, Any] = {
        "schema_version": schema.schema_version,
        "timestamp": format_timestamp(now, ts_pattern),
        "level": level_n,
        "data_center": identity.data_center,
        "system_code": identity.system_code,
        "node": identity.node,
        "trace_id": placeholder,
        "txn_seq": placeholder,
        "pid": os.getpid(),
        "tid": _auto_tid(),
        "caller": auto_caller,
    }
    header_values: dict[str, str] = {}
    for name in schema.header.fields:
        raw = header_in[name] if name in header_in else defaults.get(name, placeholder)
        header_values[name] = escape_value(
            _stringify(raw, placeholder), schema.content
        )
    final_caller = header_values.get("caller", placeholder)
    if final_caller != placeholder and not _CALLER_LINE_RE.search(final_caller):
        raise FormatError(f"caller must include line number, got {final_caller!r}")

    # required 内容档无值占位；UA/EVT 随关键字。
    content_pairs: list[tuple[str, str]] = []
    for name in _content_keys(keyword_n, elements_in):
        raw = elements_in.get(name, placeholder)
        text = _stringify(raw, placeholder)
        if name == "RSPCD" and text != placeholder:
            _validate_rspcd(text)
        if name in ("UA", "EVT") and text != placeholder:
            text = escape_value(text, schema.content)
        elif name in CONTENT_RECOMMENDED_FIELDS or name in CONTENT_REQUIRED_FIELDS:
            text = escape_value(text, schema.content)
        else:
            text = escape_value(text, schema.content)
        content_pairs.append((name, text))

    if ext_in:
        normalized = [(_normalize_ext_key(k), _stringify(v, placeholder)) for k, v in ext_in.items()]
        first_key, first_val = normalized[0]
        content_pairs.append(
            ("EXT", escape_value(f"{first_key}={first_val}", schema.content))
        )
        for key, val in normalized[1:]:
            content_pairs.append((key, escape_value(val, schema.content)))

    header_line = schema.header.separator.join(
        header_values[name] for name in schema.header.fields
    )
    kv = schema.content.kv_separator
    sep = schema.content.element_separator
    body = sep.join(f"{k}{kv}{v}" for k, v in content_pairs)
    if keyword_n:
        content_text = f"{schema.content.keyword_prefix}{keyword_n}:{body}"
    else:
        content_text = body
    if not content_text:
        return header_line
    # SRS 示例：头部 11 字段之后再跟一个头部分隔符，然后才是 #UA:...
    return header_line + schema.header.separator + content_text


def parse_audit_line(line: str, schema: AuditLogSchema | None = None) -> ParsedAuditLine:
    """按 schema 拆出头部与内容，供单测与接入边界使用。"""
    schema = schema or default_schema()
    field_count = len(schema.header.fields)
    parts = line.split(schema.header.separator)
    if len(parts) < field_count:
        raise FormatError(
            f"header expects {field_count} fields, got {len(parts)} in {line!r}"
        )
    header_parts = parts[:field_count]
    rest = schema.header.separator.join(parts[field_count:])
    header = dict(zip(schema.header.fields, header_parts, strict=True))

    keyword: str | None = None
    body = rest
    prefix_ua = f"{schema.content.keyword_prefix}{KEYWORD_UA}:"
    prefix_evt = f"{schema.content.keyword_prefix}{KEYWORD_EVT}:"
    if rest.startswith(prefix_ua):
        keyword = KEYWORD_UA
        body = rest[len(prefix_ua) :]
    elif rest.startswith(prefix_evt):
        keyword = KEYWORD_EVT
        body = rest[len(prefix_evt) :]

    elements: dict[str, str] = {}
    ext: dict[str, str] = {}
    if body:
        for item in body.split(schema.content.element_separator):
            if schema.content.kv_separator not in item:
                raise FormatError(f"content item missing kv separator: {item!r}")
            key, _, value = item.partition(schema.content.kv_separator)
            if key == "EXT":
                inner_key, _, inner_val = value.partition("=")
                ext[inner_key] = inner_val
            elif key.startswith("x_"):
                ext[key] = value
            else:
                elements[key] = value
    return ParsedAuditLine(header=header, keyword=keyword, elements=elements, ext=ext, raw=line)


def format_clock_event(
    event: ClockEvent,
    *,
    schema: AuditLogSchema | None = None,
    identity: RuntimeIdentity | None = None,
    clock: Clock | None = None,
    header: Mapping[str, Any] | None = None,
) -> str:
    """把 NTP 同步事件格式化为 #EVT 管道行。"""
    elements: dict[str, Any] = {
        "UID": "system",
        "CUSTID": "-",
        "SRCIP": "-",
        "DSTIP": "-",
        "COST": "-",
        "SVRNAM": "ntp_sync",
        "EVT": event.event,
        "MSG": event.message,
        "RSPCD": event.rspcd,
        "SUBMDL": SUBMDL_AUDIT,
        "PROC": PROC_NTP_SYNC,
    }
    return format_audit_line(
        schema=schema,
        clock=clock,
        identity=identity,
        level=event.level,
        keyword=KEYWORD_EVT,
        elements=elements,
        header=header,
    )
