# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Redis key 约定（与 Session Manager 运行场景演示一致）。"""

from __future__ import annotations

SESSION_EXPIRY = "session_expiry"
PODS_REGISTERED = "pods:registered"


def session_key(session_id: str) -> str:
    return f"session:{session_id}"


def scope_sessions_key(service_id: str) -> str:
    return f"scope:{service_id}:sessions"


def scope_free_channel(service_id: str) -> str:
    return f"scope:{service_id}:free"


def pod_sessions_key(service_id: str, endpoint_id: str) -> str:
    return f"pod:{service_id}:{endpoint_id}:sessions"


def pod_idle_notified_key(service_id: str, endpoint_id: str) -> str:
    return f"pod:{service_id}:{endpoint_id}:idle_notified"


def registered_member(service_id: str, endpoint_id: str) -> str:
    return f"{service_id}:{endpoint_id}"
