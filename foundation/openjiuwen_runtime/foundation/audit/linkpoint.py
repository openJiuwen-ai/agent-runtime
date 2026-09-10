# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR2 全链路打点规划（尚未实现强制打点，仅提供对照表骨架）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .config import LinkpointConfig


@dataclass(frozen=True)
class Linkpoint:
    location: str
    keyword: str
    submdl: str
    proc: str
    required_elements: tuple[str, ...]


DEFAULT_LINKPOINTS: tuple[Linkpoint, ...] = (
    Linkpoint("用户接入认证", "#EVT", "gateway", "authenticate", ("失败类型", "累计次数")),
    Linkpoint("技能调用执行", "#UA", "agent", "call_skill", ("技能名", "参数", "结果", "耗时")),
    Linkpoint("外部系统 API 调用", "#UA", "api_client", "{函数名}", ("接口名", "参数", "响应码")),
    Linkpoint("文件发送/接收", "#UA", "file", "transfer", ("文件名", "大小", "目标")),
    Linkpoint("认证失败/越权", "#EVT", "gateway", "authenticate", ("失败类型", "累计次数")),
    Linkpoint("异常/错误处理", "#EVT", "{对应模块}", "{对应函数}", ("异常类型", "堆栈摘要")),
    Linkpoint("告警事件", "#EVT", "alert", "send_alert", ("告警类型", "处置动作")),
    Linkpoint("沙箱创建", "#UA", "sandbox", "create_sandbox", ("沙箱 ID", "策略名", "结果")),
    Linkpoint("沙箱启动/停止", "#UA", "sandbox", "start_sandbox/stop_sandbox", ("沙箱 ID", "结果")),
    Linkpoint("沙箱删除", "#UA", "sandbox", "delete_sandbox", ("沙箱 ID", "结果")),
    Linkpoint("安全策略下发", "#UA", "sandbox", "apply_policy", ("策略名", "结果")),
    Linkpoint("沙箱内命令执行", "#UA", "sandbox", "exec_command", ("命令", "退出码", "耗时")),
    Linkpoint("沙箱文件传输", "#UA", "sandbox", "file_transfer", ("方向", "路径", "大小")),
)


def linkpoints(config: LinkpointConfig | None = None) -> Sequence[Linkpoint]:
    """返回打点对照表。强制校验各点是否打点属于 FR2，尚未实现。"""
    _ = config
    return DEFAULT_LINKPOINTS
