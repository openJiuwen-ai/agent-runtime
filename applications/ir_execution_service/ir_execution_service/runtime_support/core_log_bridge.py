# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

_MOD_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CoreLogEvent:
    kind: str
    logger_name: str
    levelno: int
    request_id: str
    interface_name: str
    ok: bool | None
    return_info: str
    payload: dict[str, Any] | None
    raw_message: str


_SINKS: list[Callable[[CoreLogEvent], None]] = []
_BRIDGE_INSTALLED = False


def register_core_log_sink(sink: Callable[[CoreLogEvent], None]) -> None:
    for cb in _SINKS:
        if cb is sink:
            return
    _SINKS.append(sink)


def _emit_to_sinks(event: CoreLogEvent) -> None:
    for cb in list(_SINKS):
        try:
            cb(event)
        except Exception:
            _MOD_LOG.warning("core log sink callback raised", exc_info=True)
            continue


def _is_upstream_llm_http_completion_end(payload: dict[str, Any]) -> bool:
    msg = str(payload.get("message") or "")
    if "API response received." in msg:
        return True
    md = payload.get("metadata")
    if not isinstance(md, dict):
        return False
    resp = md.get("response")
    if isinstance(resp, dict) and isinstance(resp.get("choices"), list):
        return True
    if isinstance(resp, str) and "choices=" in resp and "ChatCompletion(" in resp:
        return True
    return False


def _is_upstream_llm_hard_failure(payload: dict[str, Any]) -> bool:
    msg = str(payload.get("message") or payload.get("error_message") or "")
    lower = msg.lower()
    if "failed to decode json from llm output" in lower:
        return False
    if "unsupported llm_output type for parse" in lower:
        return False
    if "stream parser attempt error" in lower:
        return False
    if "api async invoke error" in lower:
        return True
    if "api async stream error" in lower:
        return True
    if "api invoke error" in lower:
        return True
    if "invoke error" in lower and "parser" not in lower:
        return True
    if "kv cache release failed" in lower:
        return True
    if "kv cache release error" in lower:
        return True
    return False


class CoreLogBridge(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        logger_name = str(record.name or "")
        if logger_name not in {"llm", "tool"}:
            return
        try:
            msg = record.getMessage()
        except Exception:
            _MOD_LOG.warning("failed to format core bridge log record", exc_info=True)
            return

        payload: dict[str, Any] | None = None
        if msg and msg[0] == "{":
            try:
                parsed = json.loads(msg)
                payload = parsed if isinstance(parsed, dict) else None
            except Exception:
                payload = None

        if logger_name == "llm" and payload is not None:
            event_type = payload.get("event_type")
            if event_type == "llm_call_end" and _is_upstream_llm_http_completion_end(payload):
                rid = str(payload.get("trace_id") or payload.get("session_id") or "")
                _emit_to_sinks(
                    CoreLogEvent(
                        kind="llm_upstream",
                        logger_name=logger_name,
                        levelno=record.levelno,
                        request_id=rid,
                        interface_name="llm.call",
                        ok=True,
                        return_info=str(payload.get("message") or ""),
                        payload=payload,
                        raw_message=msg,
                    )
                )
                return
            if event_type == "llm_call_error" and _is_upstream_llm_hard_failure(payload):
                rid = str(payload.get("trace_id") or payload.get("session_id") or "")
                _emit_to_sinks(
                    CoreLogEvent(
                        kind="llm_upstream",
                        logger_name=logger_name,
                        levelno=record.levelno,
                        request_id=rid,
                        interface_name="llm.call",
                        ok=False,
                        return_info=str(payload.get("message") or payload.get("error_message") or ""),
                        payload=payload,
                        raw_message=msg,
                    )
                )
                return

        if record.levelno >= logging.WARNING:
            _emit_to_sinks(
                CoreLogEvent(
                    kind="core_warning",
                    logger_name=logger_name,
                    levelno=record.levelno,
                    request_id="",
                    interface_name="",
                    ok=None,
                    return_info=msg,
                    payload=payload,
                    raw_message=msg,
                )
            )


def install_core_log_bridge() -> None:
    global _BRIDGE_INSTALLED
    if _BRIDGE_INSTALLED:
        return
    bridge = CoreLogBridge()
    # 只挂 root：子 logger（llm/tool）默认 propagate=True，记录会冒泡一次，避免与再挂子 logger 导致 emit 双份。
    # CoreLogBridge.emit 已按 record.name 过滤非 llm/tool。
    root = logging.getLogger()
    root.addHandler(bridge)
    _BRIDGE_INSTALLED = True

