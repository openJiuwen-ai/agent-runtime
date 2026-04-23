# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Dispatch runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Mapping


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None else default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None else default


def parse_duration_seconds(value: str | int | float | None, default: float) -> float:
    """Parse values like 30, 30s, 2m or 1h into seconds."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)

    raw = str(value).strip().lower()
    if not raw:
        return default
    if raw.endswith("ms"):
        return float(raw[:-2]) / 1000.0
    if raw.endswith("s"):
        return float(raw[:-1])
    if raw.endswith("m"):
        return float(raw[:-1]) * 60.0
    if raw.endswith("h"):
        return float(raw[:-1]) * 3600.0
    return float(raw)


@dataclass(slots=True)
class DispatchSettings:
    """Static settings shared by the dispatcher and scaler."""

    redis_url: str = "redis://127.0.0.1:6379/0"
    namespace: str = "default"
    deployment_name: str = "ws-agent"
    pod_label_selector: str = "app=ws-agent"
    agent_port: int = 8080
    agent_ws_path: str = "/ws"
    dispatcher_ws_path: str = "/ws"
    min_instance: int = 1
    min_idle: int = 0
    max_instance: int = 10
    concurrent_num: int = 200
    idle_timeout: float = 30.0
    scale_up_debounce: float = 2.0
    queue_max_wait: float = 60.0
    heartbeat_interval: float = 10.0
    sweep_interval: float = 1.0
    prewarm_check_interval: float = 5.0
    pod_ready_channel: str = "ws:pod_ready"
    admin_event_stream: str = "ws:admin_events"
    scale_event_stream: str = "ws:scale_events"
    admin_cursor_key: str = "ws:admin_events:last_id"
    scale_cursor_key: str = "ws:scale_events:last_id"
    config_hash_key: str = "ws:config"

    @classmethod
    def from_env(cls) -> "DispatchSettings":
        return cls(
            redis_url=_env("DISPATCH_REDIS_URL", _env("REDIS_URL", "redis://127.0.0.1:6379/0")),
            namespace=_env("DISPATCH_NAMESPACE", "default"),
            deployment_name=_env("DISPATCH_DEPLOYMENT_NAME", "ws-agent"),
            pod_label_selector=_env("DISPATCH_POD_LABEL_SELECTOR", "app=ws-agent"),
            agent_port=_env_int("DISPATCH_AGENT_PORT", 8080),
            agent_ws_path=_env("DISPATCH_AGENT_WS_PATH", "/ws"),
            dispatcher_ws_path=_env("DISPATCHER_WS_PATH", "/ws"),
            min_instance=_env_int("DISPATCH_MIN_INSTANCE", 1),
            min_idle=_env_int("DISPATCH_MIN_IDLE", 0),
            max_instance=_env_int("DISPATCH_MAX_INSTANCE", 10),
            concurrent_num=_env_int("DISPATCH_CONCURRENT_NUM", 200),
            idle_timeout=_env_float("DISPATCH_IDLE_TIMEOUT", 30.0),
            scale_up_debounce=_env_float("DISPATCH_SCALE_UP_DEBOUNCE", 2.0),
            queue_max_wait=_env_float("DISPATCH_QUEUE_MAX_WAIT", 60.0),
            heartbeat_interval=_env_float("DISPATCH_HEARTBEAT_INTERVAL", 10.0),
            sweep_interval=_env_float("DISPATCH_SWEEP_INTERVAL", 1.0),
            prewarm_check_interval=_env_float("DISPATCH_PREWARM_CHECK_INTERVAL", 5.0),
            pod_ready_channel=_env("DISPATCH_POD_READY_CHANNEL", "ws:pod_ready"),
            admin_event_stream=_env("DISPATCH_ADMIN_EVENT_STREAM", "ws:admin_events"),
            scale_event_stream=_env("DISPATCH_SCALE_EVENT_STREAM", "ws:scale_events"),
            admin_cursor_key=_env("DISPATCH_ADMIN_CURSOR_KEY", "ws:admin_events:last_id"),
            scale_cursor_key=_env("DISPATCH_SCALE_CURSOR_KEY", "ws:scale_events:last_id"),
            config_hash_key=_env("DISPATCH_CONFIG_HASH_KEY", "ws:config"),
        )

    def apply_runtime_overrides(self, overrides: Mapping[str, str] | None) -> "DispatchSettings":
        """Apply Redis-backed runtime config to the current settings."""
        if not overrides:
            return self

        updated = replace(self)
        if "system.minInstance" in overrides:
            updated.min_instance = int(overrides["system.minInstance"])
        if "system.minIdle" in overrides:
            updated.min_idle = int(overrides["system.minIdle"])
        if "system.maxInstance" in overrides:
            updated.max_instance = int(overrides["system.maxInstance"])
        if "system.concurrentNum" in overrides:
            updated.concurrent_num = int(overrides["system.concurrentNum"])
        if "system.idleTimeout" in overrides:
            updated.idle_timeout = parse_duration_seconds(overrides["system.idleTimeout"], updated.idle_timeout)
        if "system.scaleUpDebounce" in overrides:
            updated.scale_up_debounce = parse_duration_seconds(
                overrides["system.scaleUpDebounce"], updated.scale_up_debounce
            )
        if "system.queueMaxWait" in overrides:
            updated.queue_max_wait = parse_duration_seconds(overrides["system.queueMaxWait"], updated.queue_max_wait)
        if "system.heartbeatInterval" in overrides:
            updated.heartbeat_interval = parse_duration_seconds(
                overrides["system.heartbeatInterval"], updated.heartbeat_interval
            )
        if "system.prewarmCheckInterval" in overrides:
            updated.prewarm_check_interval = parse_duration_seconds(
                overrides["system.prewarmCheckInterval"], updated.prewarm_check_interval
            )
        if "runtime.port" in overrides:
            updated.agent_port = int(overrides["runtime.port"])
        return updated
