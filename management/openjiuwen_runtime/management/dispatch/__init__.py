# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""K8s dispatch and dynamic scaling primitives."""

from .bus import EventBus, InMemoryEventBus, RedisEventBus
from .config import DispatchSettings
from .exceptions import CapacityAllocationError, DispatchError, QueueTimeoutError, SessionHeaderError
from .models import DispatchHeader, PodInfo, PodState, ScaleEvent, SessionInfo, SessionState
from .scaler import ScalerController
from .scheduler import Scheduler
from .store import RedisDispatchStore


def create_dispatcher_app(*args, **kwargs):
    from .dispatcher import create_dispatcher_app as _create_dispatcher_app

    return _create_dispatcher_app(*args, **kwargs)


__all__ = [
    "EventBus",
    "RedisEventBus",
    "InMemoryEventBus",
    "DispatchSettings",
    "DispatchError",
    "QueueTimeoutError",
    "CapacityAllocationError",
    "SessionHeaderError",
    "DispatchHeader",
    "SessionInfo",
    "SessionState",
    "PodInfo",
    "PodState",
    "ScaleEvent",
    "RedisDispatchStore",
    "Scheduler",
    "ScalerController",
    "create_dispatcher_app",
]
