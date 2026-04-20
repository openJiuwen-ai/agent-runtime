from __future__ import annotations

from typing import Any, Dict, Optional
from loguru import logger

from openjiuwen.core.foundation.tool import LocalFunction, ToolCard


async def query_balance(task_description: str, session: Optional[Any] = None) -> Dict[str, Any]:
    """查询账户余额
    
    使用说明：
    - 入参是自然语言描述的任务，例如："查一下我的账户余额"
    - 只要意图匹配（查询余额）就直接调用，不需要追问用户的具体账户信息
    - 工具内部会能够追问用户的具体账户信息
    - 如果无法提取账户信息，使用默认账户查询即可
    """
    logger.info("====================")
    logger.info(f"进入工具: query_balance, 任务描述: {task_description}")
    logger.info("（外部接口调用和返回值判断已在 Rail 中处理）")
    logger.info("离开工具: query_balance, 返回空字典占位符")
    logger.info("====================")
    return {} 


query_balance_tool = LocalFunction(
    card=ToolCard(
        id="query_balance",
        name="query_balance",
        description="查询账户余额。入参是自然语言描述的任务，只要意图匹配就直接调用，不需要追问用户的具体账户信息。",
        input_params={
            "type": "object",
            "properties": {
                "task_description": {"type": "string", "description": "自然语言描述的任务，例如：'查一下我的账户余额'"},
            },
            "required": ["task_description"],
        },
    ),
    func=query_balance,
)
