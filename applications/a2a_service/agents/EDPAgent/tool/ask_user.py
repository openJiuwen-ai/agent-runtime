from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard


async def ask_user(
    question: str = "",
    session: Optional[Any] = None,
) -> Dict[str, Any]:
    """追问用户信息（直接执行，不经过 Rail 拦截）"""
    logger.info(f"[ask_user] question={question!r}")
    return {"status": "success", "question": question}


ask_user_tool = LocalFunction(
    card=ToolCard(
        id="ask_user",
        name="ask_user",
        description="追问用户信息。直接向用户提出问题，用户下一轮消息即为回答。",
        input_params={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "要问用户的问题"},
            },
            "required": ["question"],
        },
    ),
    func=ask_user,
)
