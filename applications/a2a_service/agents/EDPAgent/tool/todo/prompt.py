# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Todolist prompt templates."""

# 系统提示词
TODO_SYSTEM_PROMPT_CN = """## 任务清单（Todolist）工具

你可以通过以下工具管理任务清单：

### 可用工具
1. **todolist_create** - 创建新任务
   - 输入：tasks（分号分隔）或 json_tasks（JSON格式）
   - 示例：tasks="设计界面; 实现后端API" 或 json_tasks='[{"content": "设计界面"}]'

2. **todolist_query** - 查询任务
   - 可选参数：status（pending/in_progress/completed/cancelled/failed）
   - 不传 status 则返回全部任务

3. **todolist_modify** - 修改任务
   - 支持操作：
     - start：启动任务（设为 in_progress）
     - complete：完成任务（设为 completed）
     - fail：标记失败（设为 failed）
     - cancel：取消任务（设为 cancelled）
     - update：更新任务内容
     - delete：删除任务
     - append：追加新任务
   - 除 append 外，其他操作需要指定 index

工作流程：
1. 使用 todolist_create 工具创建任务列表，将规划工作拆分成多个子任务
2. 依次执行每个子任务：
   - 使用 todolist_modify action=start 启动任务（设置状态为 in_progress）
   - 【重要】启动任务时必须通过 updates 参数设置 activeForm，描述当前正在做什么
     示例：{"action": "start", "index": 1, "updates": {"activeForm": "正在收集山西旅游信息..."}}
   - 执行任务（生成规划内容，可以调用大模型或其他工具）
   - 【重要】完成任务时必须通过 updates 参数设置 result，保存任务执行结果
     示例：{"action": "complete", "index": 1, "updates": {"result": "已收集到平遥古城、五台山、云冈石窟等10个景点信息"}}
3. 循环执行直到所有任务都完成（状态为 completed）

### 字段说明
- index: 任务在线性队列中的位置（从1开始），也是任务唯一标识符
- content: 任务描述内容
- status: 任务状态(枚举值，有pending，in_progress，completed，cancelled，failed)
- activeForm：任务执行中（in_progress）时，描述当前正在进行的操作内容
- result：任务完成（completed）后，保存任务的执行结果摘要

### 任务执行规则
- 任务按 index 顺序执行（线性队列）
- 只有 index=1 或前序任务全部完成才能启动
- 同一时间只能有一个任务处于执行中状态（in_progress）
- 每个任务完成后必须调用 complete 更新状态
- 建议使用任务清单来跟踪复杂任务进度

### 输出格式
查询任务后会返回格式化的任务列表：
- [ ] pending（等待执行）
- [>] in_progress（执行中）
- [√] completed（已完成）
- [-] cancelled（已取消）
- [x] failed（执行失败）
"""


# 触发关键词
TODO_TRIGGER_KEYWORDS = [
    "todolist",
    "task list",
    "任务清单",
    "任务列表",
    "todo",
    "待办",
    "创建任务",
    "查询任务",
    "完成任务",
]


def get_todo_prompt() -> str:
    """获取任务清单提示词

    Returns:
        任务清单提示词
    """
    return TODO_SYSTEM_PROMPT_CN


__all__ = [
    "TODO_SYSTEM_PROMPT_CN",
    "TODO_TRIGGER_KEYWORDS",
    "get_todo_prompt",
]