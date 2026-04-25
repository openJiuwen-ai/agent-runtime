# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Session SDK 数据模型"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openjiuwen_runtime.foundation.db import DBHandler


class MessagePriority(str, Enum):
    """消息优先级"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MessageType(str, Enum):
    USER_REQUEST = "user_request"


@dataclass
class SessionConfig:
    """单 session 维度：与 Access 中策略一起写入 ISessionRequest。"""

    concurrency: int = 1
    """同一 session 内最大并行处理中的请求数（会话级限流）。"""
    ttl: int = 300
    """session 亲和与计时滑动窗口（秒；0 表示不启用 session TTL 计时器）。"""


@dataclass
class AccessConfig:
    """入口与 ServiceManager 的共享运行配置。"""

    # 双队列
    user_queue_size: int = 1000
    system_queue_size: int = 100

    # 业务与部署
    image: str = "app:latest"
    db_handler: Optional["DBHandler"] = None
    service_concurrency: int = 200
    min_idle_services: int = 1
    """池中**至少**保持的**空闲**实例数（仅由 autoscale 补位，**不会**因 ``service_ttl`` 从 idle 删 Pod）。
    若 ``len(_idle) < min_idle``，约每 ``autoscale_interval`` 会 deploy 预热实例入 idle。设为 ``0`` 表示不维护热备。"""
    max_services: int = 10
    target_port: int = 8000
    invoke_path: str = ""  # 与 pod_ip:target_port 组成 WS URI；空则仅 ws://host:port
    ws_use_tls: bool = False  # True 为 wss://，否为 ws://

    service_ttl: int = 300
    """① **in_use** 在「无 session、无 inflight」后，等待本秒数再**转入** ``_idle``。②
    当 **len(idle) > min_idle_services** 时，对**多出来**的 idle 删 Pod 前再等待本秒数，直到缩至 ``min``。
    **从 in_use 刚转入** idle 时若已超额，不叠第二次同长度等待（与 ① 合并）。底数 min 台 idle 不按本字段被删。
    设为 ``0`` 时无等待。负值行为与实现相关，勿用。
    """

    message_timeout: int = 600
    max_retries: int = 3
    autoscale_interval: float = 0.2
