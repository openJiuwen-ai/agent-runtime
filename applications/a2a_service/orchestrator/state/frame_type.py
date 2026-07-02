from __future__ import annotations

from enum import Enum
from typing import Optional

from ..route.normalized_event import NormalizedEvent


class FrameType(str, Enum):
    DATA = "DATA"
    CONTROL_UNSPECIFIED = "CONTROL_UNSPECIFIED"
    CONTROL_SUBMITTED = "CONTROL_SUBMITTED"
    CONTROL_WORKING = "CONTROL_WORKING"
    CONTROL_COMPLETED = "CONTROL_COMPLETED"
    CONTROL_CANCELED = "CONTROL_CANCELED"
    CONTROL_FAILED = "CONTROL_FAILED"
    CONTROL_INPUT_REQUIRED = "CONTROL_INPUT_REQUIRED"
    CONTROL_REJECTED = "CONTROL_REJECTED"
    CONTROL_AUTH_REQUIRED = "CONTROL_AUTH_REQUIRED"


def classify_frame(event: NormalizedEvent) -> FrameType:
    if "frame_type" in event.metadata:
        ft = event.metadata["frame_type"]
        try:
            return FrameType(ft)
        except ValueError:
            return FrameType.CONTROL_UNSPECIFIED

    event_type = event.type.upper()
    mapping = {
        "ARTIFACT": FrameType.DATA,
        "COMPLETED": FrameType.CONTROL_COMPLETED,
        "FAILED": FrameType.CONTROL_FAILED,
        "INPUT_REQUIRED": FrameType.CONTROL_INPUT_REQUIRED,
        "AUTH_REQUIRED": FrameType.CONTROL_AUTH_REQUIRED,
        "WORKING": FrameType.CONTROL_WORKING,
        "SUBMITTED": FrameType.CONTROL_SUBMITTED,
        "CANCELED": FrameType.CONTROL_CANCELED,
        "REJECTED": FrameType.CONTROL_REJECTED,
    }
    return mapping.get(event_type, FrameType.CONTROL_UNSPECIFIED)
