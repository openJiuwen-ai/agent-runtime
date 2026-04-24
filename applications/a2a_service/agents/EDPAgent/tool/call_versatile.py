"""
通用 VersatileAdapter 调用工具。

替代 query_balance / transfer / buy_wealth 等独立工具，
将工作流调用所需的三个维度统一为通用参数：

  - query_description  → 自然语言任务描述，传给 VersatileAdapter
  - query_intent       → 业务意图分类，用于 Executor 路由
  - query_response_analysis_scripts → 归一化脚本运行命令，Cascade 续轮时在沙箱中执行

Rail 拦截后：
  首次拦截 → intent + task_description 写入 pending_delegate → interrupt()
  Cascade 续轮 → 读 query_response_analysis_scripts → sys_op.shell().execute_cmd() → reject(tool_result)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard


async def call_versatile(
    query_description: str = "",
    query_intent: str = "",
    query_response_analysis_scripts: str = "",
    session: Optional[Any] = None,
) -> Dict[str, Any]:
    """通用 VersatileAdapter 调用（Rail 拦截并委托给 Executor → VA 完成）"""
    logger.info(
        f"[call_versatile] intent={query_intent}, "
        f"desc={query_description!r:.80}, script={query_response_analysis_scripts}"
    )
    return {}


call_versatile_tool = LocalFunction(
    card=ToolCard(
        id="call_versatile",
        name="call_versatile",
        description=(
            "通用业务工作流调用工具。"
            "将任务描述、业务意图和归一化脚本路径传入，由系统自动调用 VersatileAdapter 完成工作流执行并返回结构化结果。"
            "余额查询、转账、购买理财等操作均通过此工具完成。"
        ),
        input_params={
            "type": "object",
            "properties": {
                "query_description": {
                    "type": "string",
                    "description": (
                        "自然语言任务描述，传给工作流引擎。"
                        "示例：'查询尾号为6605的卡的余额'、"
                        "'从尾号3344的卡转账30000元到尾号为6605的卡'、"
                            "'购买理财产品：产品名称：添利宝，产品代码：XLT1801，金额：50000元'"
                    ),
                },
                "query_intent": {
                    "type": "string",
                    "description": (
                        "业务意图分类，用于工作流路由。"
                        "可选值：'查询账户余额'、'快速转账'、'理财选品购买'、'理财推荐'"
                    ),
                },
                "query_response_analysis_scripts": {
                    "type": "string",
                    "description": (
                        "归一化脚本运行命令（工作目录为 skills/），用于对工作流返回数据做结构化处理。"
                        "示例：'python model_driven_fund_planning_skill/scripts/run_fund_planning.py'。"
                        "留空则透传原始工作流返回数据。"
                    ),
                },
            },
            "required": ["query_description", "query_intent"],
        },
    ),
    func=call_versatile,
)
