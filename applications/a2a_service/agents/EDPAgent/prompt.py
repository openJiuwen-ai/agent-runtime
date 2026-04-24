"""
DPA Agent 系统提示词补充。

Skill 正文不直接注入系统提示词；技能通过 agent.register_skill 注册，
由框架引导模型在运行时使用 read_file 按需读取对应的 SKILL.md。
"""
from __future__ import annotations

_BASE_PROMPT = """\
## 六、技能与工具补充

### 6.1 可用工具

- call_versatile：通用业务工作流调用，适用于理财推荐、选品、购买筹划等 Skill 场景
- ask_user：在关键信息缺失或敏感操作确认时向用户追问

### 6.2 Skill 使用规则

- 需要执行某个 Skill 前，先用 read_file 读取对应目录下的 SKILL.md，再严格按照文档填写工具参数。
- 理财推荐优先使用 rebuild_product_recommend_skill，并通过 call_versatile 执行。
- 用户从推荐结果中选择产品时，优先使用 rebuild_product_select_skill，并通过 call_versatile 执行。
- 用户确认购买或需要资金筹划时，优先使用 model_driven_fund_planning_skill，并通过 call_versatile 执行。
- 余额查询、转账、购买筹划等业务统一通过 call_versatile 执行；若 Skill 文档提供了参数模板，优先遵循 Skill 文档。
- 每次只执行一个工具调用，等结果返回后再继续规划。
"""


def build_system_prompt() -> str:
    return _BASE_PROMPT
