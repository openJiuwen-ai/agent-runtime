# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from openjiuwen_runtime.foundation.log import get_logger

from ..interfaces import IRequest
from ._base import BaseSessionStrategy

logger = get_logger(__name__)


class PerChatBotStrategy(BaseSessionStrategy):
    """同一 chat + bot 共享一个 session，群内所有用户共享上下文。"""

    def _build_key(self, msg: IRequest) -> str:
        chat_id = msg.chat_id or ""
        bot_id = msg.bot_id or ""
        key = f"{chat_id}::{bot_id}"
        logger.debug("PerChatBot 会话键: %s (chat_id=%s bot_id=%s)", key, chat_id, bot_id)
        return key
