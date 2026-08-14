# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""协调器协议。"""

from __future__ import annotations

from typing import Optional, Protocol


class Coordinator(Protocol):
    async def try_claim(
        self,
        *,
        now: float,
        instance_id: str,
        planned_fire: float | None = None,
    ) -> Optional[str]:
        """试着领取本轮执行权；成功返回锁 token，失败返回 None。

        ``planned_fire``：本拍语义上的开火整点（如 10.000）。
        提前醒来时 ``now`` 可能是 T-窗口，epoch / 等到点应以 ``planned_fire`` 为准。
        """
        ...

    async def release(self, token: str) -> None:
        """交回执行权（按 token 校验后放锁）。"""
        ...
