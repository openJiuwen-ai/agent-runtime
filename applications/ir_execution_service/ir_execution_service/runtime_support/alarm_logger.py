# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from openjiuwen_runtime.foundation.log import get_logger
from .core_log_bridge import CoreLogEvent, register_core_log_sink

_LOG = get_logger(__name__)


class AlarmServerName(str, Enum):
    IR_EXECUTION_SERVICE = "ir_execution_service"
    OBS = "obs"
    REDIS = "redis"
    LLM = "llm"
    TOOL = "tool"


class AlarmSeverity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    WARNING = "warning"
    INDETERMINATE = "indeterminate"
    CLEARED = "cleared"


def _now_alarm_time_str() -> str:
    # Follow sample in dfx.md: "20221108 10:23:13"
    return datetime.now(timezone.utc).strftime("%Y%m%d %H:%M:%S")


def _is_loopback(ip: str) -> bool:
    ip = (ip or "").strip()
    return ip in {"127.0.0.1", "::1"} or ip.startswith("127.")


def resolve_local_ip() -> str:
    """Resolve a non-loopback local ip, best-effort."""
    env_ip = (os.environ.get("IP") or "").strip()
    if env_ip and not _is_loopback(env_ip):
        return env_ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            return ip if ip and not _is_loopback(ip) else ""
        finally:
            s.close()
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
            return ip if ip and not _is_loopback(ip) else ""
        except Exception:
            return ""


def map_level_from_python(levelno: int) -> AlarmSeverity:
    # Required mapping:
    # CRITICAL -> critical
    # ERROR -> major
    # WARNING -> minor
    if levelno >= logging.CRITICAL:
        return AlarmSeverity.CRITICAL
    if levelno >= logging.ERROR:
        return AlarmSeverity.MAJOR
    return AlarmSeverity.MINOR


@dataclass(frozen=True, slots=True)
class AlarmLogRecord:
    timestamp: str
    server_name: str
    ip: str
    level: str
    module: str
    message: str

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "server_name": self.server_name,
                "ip": self.ip,
                "level": self.level,
                "module": self.module,
                "message": self.message,
            },
            ensure_ascii=False,
            default=str,
        )


class AlarmLogger:
    def __init__(self, *, log_file: Path) -> None:
        self._logger = logging.getLogger("ir_execution_service.alarm")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        for h in list(self._logger.handlers):
            self._logger.removeHandler(h)
            try:
                h.close()
            except Exception as exc:
                _LOG.warning("failed to close logging handler: %s", exc)

        log_file.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = _env_int("LOWCODE_ALARM_LOG_MAX_BYTES", 20 * 1024 * 1024)
        backup_count = _env_int("LOWCODE_ALARM_LOG_BACKUP_COUNT", 20)
        handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)

        _LOG.info("Alarm logger ready: %s", log_file)

    def write(self, record: AlarmLogRecord) -> None:
        self._logger.info(record.to_json_line())


_ALARM: AlarmLogger | None = None
_ALARM_BRIDGE_INSTALLED = False
_ALARM_TOOL_CALLBACK_INSTALLED = False


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except Exception:
        return default


def init_alarm_logger_from_env() -> AlarmLogger | None:
    """Initialize alarm logger if path env is set. Env must include filename."""
    global _ALARM
    if _ALARM is not None:
        return _ALARM

    raw = (os.environ.get("LOWCODE_ALARM_LOG_PATH") or "").strip()
    if not raw:
        _LOG.info("Alarm logger disabled (LOWCODE_ALARM_LOG_PATH not set).")
        return None
    p = Path(raw).expanduser().resolve()
    _ALARM = AlarmLogger(log_file=p)
    return _ALARM


def log_alarm(
    *,
    server_name: AlarmServerName,
    level: AlarmSeverity,
    module: str,
    message: str,
    ip: str = "",
) -> None:
    if _ALARM is None:
        return
    _ALARM.write(
        AlarmLogRecord(
            timestamp=_now_alarm_time_str(),
            server_name=server_name.value,
            ip=str(ip or "").strip(),
            level=level.value,
            module=str(module or "").strip(),
            message=str(message or "").strip(),
        )
    )


def install_alarm_log_bridge() -> None:
    install_core_alarm_sink()


def install_core_alarm_sink() -> None:
    global _ALARM_BRIDGE_INSTALLED
    if _ALARM is None or _ALARM_BRIDGE_INSTALLED:
        return

    def _sink(event: CoreLogEvent) -> None:
        if event.kind == "llm_upstream":
            if event.ok:
                return
            payload = event.payload or {}
            msg = str(payload.get("error_message") or payload.get("exception") or event.return_info)
            log_alarm(
                server_name=AlarmServerName.LLM,
                level=AlarmSeverity.MAJOR,
                module="llm.call",
                message=msg,
                ip="",
            )
            return

        if event.kind != "core_warning":
            return

        if event.logger_name == "llm":
            server = AlarmServerName.LLM
        elif event.logger_name == "tool":
            server = AlarmServerName.TOOL
        else:
            return

        payload = event.payload or {}
        msg = event.return_info
        if server == AlarmServerName.LLM:
            msg = str(payload.get("error_message") or payload.get("exception") or payload.get("message") or msg)
        elif server == AlarmServerName.TOOL:
            msg = str(payload.get("error_message") or payload.get("message") or msg)

        log_alarm(
            server_name=server,
            level=map_level_from_python(event.levelno),
            module=event.logger_name,
            message=msg,
            ip="",
        )

    register_core_log_sink(_sink)
    _ALARM_BRIDGE_INSTALLED = True


def install_runner_tool_alarm_callbacks() -> None:
    global _ALARM_TOOL_CALLBACK_INSTALLED
    if _ALARM is None or _ALARM_TOOL_CALLBACK_INSTALLED:
        return

    from openjiuwen.core.runner import Runner
    from openjiuwen.core.runner.callback.events import ToolCallEvents

    fw = Runner.callback_framework

    async def _on_tool_error(*, tool_name: str = "", error: BaseException | None = None, **_: Any) -> None:
        err_text = str(error) if error is not None else ""
        label = str(tool_name or "").strip()
        if label and err_text:
            msg = f"{label} failed: {err_text}"
        elif label:
            msg = f"{label} failed"
        else:
            msg = err_text or "tool failed"
        log_alarm(
            server_name=AlarmServerName.TOOL,
            level=AlarmSeverity.MAJOR,
            module="tool.call",
            message=msg,
            ip="",
        )

    fw.register_sync(ToolCallEvents.TOOL_CALL_ERROR, _on_tool_error, priority=-1100)
    _ALARM_TOOL_CALLBACK_INSTALLED = True

