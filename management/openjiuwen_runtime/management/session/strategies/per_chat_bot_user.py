# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from ..interfaces import IRequest
from ._base import BaseSessionStrategy


class PerChatBotUserStrategy(BaseSessionStrategy):
    """同一 chat + bot + user 独立一个 session，每个用户隔离上下文"""

    def _build_key(self, msg: IRequest) -> str:
        chat_id = msg.chat_id or ""
        bot_id = msg.bot_id or ""
        user_id = msg.user_id or ""
        return f"{chat_id}::{bot_id}::{user_id}"
