# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Dispatch data models."""

from __future__ import annotations

import time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SessionState(str, Enum):
    INIT = "init"
    RUNNING = "running"
    TTL_WAITING = "ttl_waiting"
    IDLE = "idle"
    ORPHANED = "orphaned"


class PodState(str, Enum):
    INITIALIZING = "initializing"
    SERVING = "serving"
    FULL = "full"
    DRAINING = "draining"


class DispatchHeader(BaseModel):
    sessionID: str = Field(..., min_length=1)
    concurrency: int = Field(default=1, gt=0)
    sessionTTL: int = Field(default=30, gt=0)


class SessionInfo(BaseModel):
    session_id: str
    concurrency: int
    ttl_seconds: int
    bound_pod_id: Optional[str] = None
    state: SessionState = SessionState.INIT
    active_ws_count: int = 0
    expire_at: float = 0.0
    created_at: float = Field(default_factory=time.time)
    last_active_at: float = Field(default_factory=time.time)
    orphaned: bool = False

    @property
    def is_active(self) -> bool:
        return self.state == SessionState.RUNNING and self.active_ws_count > 0


class PodInfo(BaseModel):
    pod_id: str
    pod_ip: str
    port: int
    capacity: int
    allocated: int = 0
    state: PodState = PodState.INITIALIZING
    bound_sessions: list[str] = Field(default_factory=list)
    idle_since: Optional[float] = None
    created_at: float = Field(default_factory=time.time)
    pod_template_hash: Optional[str] = None

    @property
    def available(self) -> int:
        return max(0, self.capacity - self.allocated)

    @property
    def target_url(self) -> str:
        return f"ws://{self.pod_ip}:{self.port}"

    @property
    def is_schedulable(self) -> bool:
        return self.state == PodState.SERVING and self.available > 0

    @property
    def is_idle(self) -> bool:
        return not self.bound_sessions and self.idle_since is not None


class ScaleEvent(BaseModel):
    reason: str
    session_id: Optional[str] = None
    concurrency: Optional[int] = None
    pod_id: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
