from __future__ import annotations

from typing import Any, Dict, Optional
from loguru import logger

from openjiuwen.core.foundation.tool import LocalFunction, ToolCard


async def transfer(task_description: str, session: Optional[Any] = None) -> Dict[str, Any]:
    """发起转账
    
    使用说明：
    - 入参是自然语言描述的任务，例如："帮我转500块钱给张三"、"从我的账户转1000到李四"
    - 只要意图匹配（转账）就直接调用，不需要追问用户的具体账户和金额信息
    - 工具内部会能够追问用户的具体必要的转账信息
    - 如果无法提取完整信息，使用默认账户和金额进行转账即可
    """
    logger.info("====================")
    logger.info(f"进入工具: transfer, 任务描述: {task_description}")
    logger.info("（外部接口调用和返回值判断已在 Rail 中处理）")
    logger.info("离开工具: transfer, 返回空字典占位符")
    logger.info("====================")
    return {}


transfer_tool = LocalFunction(
    card=ToolCard(
        id="transfer",
        name="transfer",
        description="发起转账。入参是自然语言描述的任务，只要意图匹配就直接调用，不需要追问用户的具体账户和金额信息。",
        input_params={
            "type": "object",
            "properties": {
                "task_description": {"type": "string", "description": "自然语言描述的任务，例如：'帮我转500块钱给张三'"},
            },
            "required": ["task_description"],
        },
    ),
    func=transfer,
)
