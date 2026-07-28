from dataclasses import dataclass
from enum import Enum
from typing import Any


class MsgType(str, Enum):
    # 电信/运营商场景
    MO_TEXT = "mo_text"
    MT_REPORT = "mt_report"
    STATUS = "status"
    DELIVERY = "delivery"
    # Agent 网关场景（jiuwenswarm 等）
    USER_REQUEST = "user_request"
    AGENT_RESPONSE = "agent_response"
    AGENT_EVENT = "agent_event"
    CONTROL = "control"


@dataclass
class UnifiedMessage:
    msg_id: str
    msg_type: MsgType
    carrier: str
    src: str
    dst: str
    payload: dict[str, Any]
    raw: Any = None
