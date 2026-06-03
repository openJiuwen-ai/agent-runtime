# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileAdapter A2A AgentCard（protobuf 类型，a2a-sdk 1.0.0-alpha.1）。
"""
from __future__ import annotations

from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol

_skill = AgentSkill(
    id="execute_workflow",
    name="执行工作流",
    description="调用 Versatile 低代码平台，执行指定工作流并返回结构化结果",
)
_skill.input_modes.extend(["data"])
_skill.output_modes.extend(["data"])

VERSATILE_ADAPTER_CARD = AgentCard(
    name="VersatileAdapter",
    description="Versatile 低代码平台工作流执行适配器，接收结构化任务描述并驱动平台执行工作流",
    version="1.0.0",
)
VERSATILE_ADAPTER_CARD.supported_interfaces.append(
    AgentInterface(
        protocol_binding=TransportProtocol.JSONRPC,
        url="",
        protocol_version=PROTOCOL_VERSION_1_0,
    )
)
VERSATILE_ADAPTER_CARD.capabilities.CopyFrom(AgentCapabilities(streaming=True))
VERSATILE_ADAPTER_CARD.skills.append(_skill)
VERSATILE_ADAPTER_CARD.default_input_modes.extend(["data"])
VERSATILE_ADAPTER_CARD.default_output_modes.extend(["data"])
