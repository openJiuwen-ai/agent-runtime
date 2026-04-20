"""
DPA Agent 适配工具。

inject_query: 将 task_description 注入到 Versatile 请求体的 query 字段，
              供 WorkflowAgent 使用。

extract_user_query: 从 Versatile 格式 body 中提取用户输入文本
                    （与 agent_runtime 侧的同名函数功能相同，DPA 侧独立维护以避免耦合）。
"""
from __future__ import annotations

import copy


def inject_query(original_body: dict, task_description: str) -> dict:
    """
    将 task_description 注入到 Versatile 请求体的 query 字段。

    Versatile 请求体格式示例：
      {
        "input": {"query": "...", ...},
        "custom_data": {...},
        ...
      }

    注入策略：
      1. 如果 body 有 input.query，替换为 task_description
      2. 否则在顶层设置 query 字段（兼容简单格式）

    返回深拷贝，不修改原始 body。
    """
    modified = copy.deepcopy(original_body)

    if isinstance(modified.get("input"), dict):
        modified["input"]["query"] = task_description
    else:
        modified["query"] = task_description

    return modified


def extract_user_query(body: dict) -> str:
    """
    从 Versatile 格式 body 中提取用户输入文本。

    支持格式：
      - {"input": {"query": "..."}}
      - {"query": "..."}
      - {"message": "..."}
    """
    if isinstance(body.get("input"), dict):
        return body["input"].get("query", "")

    return body.get("query", body.get("message", ""))
