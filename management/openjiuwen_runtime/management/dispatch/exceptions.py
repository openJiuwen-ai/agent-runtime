# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Custom dispatch exceptions."""


class DispatchError(Exception):
    """Base error for the dispatch subsystem."""


class QueueTimeoutError(DispatchError):
    """Raised when a request waits too long for capacity."""


class CapacityAllocationError(DispatchError):
    """Raised when a pod cannot reserve concurrency for a session."""


class SessionHeaderError(DispatchError):
    """Raised when X-Instance-Session is missing or invalid."""
