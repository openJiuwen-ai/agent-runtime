from __future__ import annotations

from collections import Counter
from threading import Lock
from typing import Any

from common.logger import Extra, Level, Tag, mask_sensitive_fields, to_logger


_COUNTERS: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
_LOCK = Lock()


def increment_counter(name: str, **labels: Any) -> None:
    normalized_labels = tuple(
        sorted((str(key), str(value)) for key, value in labels.items())
    )
    with _LOCK:
        _COUNTERS[(name, normalized_labels)] += 1


def get_counter_value(name: str, **labels: Any) -> int:
    normalized_labels = tuple(
        sorted((str(key), str(value)) for key, value in labels.items())
    )
    with _LOCK:
        return _COUNTERS[(name, normalized_labels)]


def reset_counters() -> None:
    with _LOCK:
        _COUNTERS.clear()


def log_channel_event(
    *,
    level: str = Level.INFO,
    action: str,
    channel: str = "",
    event_type: str = "",
    task_id: str = "",
    conversation_id: str = "",
    source_agent: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    message = {
        "action": action,
        "channel": channel,
        "event_type": event_type,
        "task_id": task_id,
        "conversation_id": conversation_id,
        "source_agent": source_agent,
        "payload": mask_sensitive_fields(payload or {}),
    }
    to_logger(
        level=level,
        message=message,
        extra=Extra(tag=Tag.TAG_CUSTOM, cost=0),
    )


def record_channel_format_error(
    *,
    channel: str,
    event_type: str,
    task_id: str = "",
    conversation_id: str = "",
    source_agent: str = "",
    error: Exception | str,
) -> None:
    increment_counter(
        "a2a_channel_format_errors_total",
        channel=channel,
        event_type=event_type,
    )
    log_channel_event(
        level=Level.WARNING,
        action="A2A_WARNING:CHANNEL_FORMAT_ERROR",
        channel=channel,
        event_type=event_type,
        task_id=task_id,
        conversation_id=conversation_id,
        source_agent=source_agent,
        payload={"error": str(error)},
    )


def record_dict_to_a2a_validation_error(
    *,
    event_type: str,
    task_id: str = "",
    conversation_id: str = "",
    source_agent: str = "",
    error: Exception | str,
) -> None:
    increment_counter(
        "a2a_dict_to_a2a_validation_errors_total",
        event_type=event_type,
    )
    log_channel_event(
        level=Level.WARNING,
        action="A2A_WARNING:DICT_TO_A2A_VALIDATION_ERROR",
        event_type=event_type,
        task_id=task_id,
        conversation_id=conversation_id,
        source_agent=source_agent,
        payload={"error": str(error)},
    )


def record_dict_to_a2a_downgrade(
    *,
    event_type: str,
    task_id: str = "",
    conversation_id: str = "",
    source_agent: str = "",
    reason: str,
) -> None:
    log_channel_event(
        level=Level.WARNING,
        action="A2A_WARNING:DICT_TO_A2A_DOWNGRADE",
        event_type=event_type,
        task_id=task_id,
        conversation_id=conversation_id,
        source_agent=source_agent,
        payload={"reason": reason},
    )
