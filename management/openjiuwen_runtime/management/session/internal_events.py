# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""走系统（高优先级）队列的内部事件。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceReclaimEvent:
    """空闲实例超过 service_ttl 后触发的缩容。"""

    service_id: str
