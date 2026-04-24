"""
AgentRule.md 加载与 schema 定义。

对齐需求文档 §4.2 的六项规则 + 话术 + 终止关键词。

YAML frontmatter 示例：
---
scope:
  allowed: "基金理财相关业务（余额查询、转账）"
  out_of_scope_message: "尚在学习中"

planning_steps:
  - 需求解析
  - 目标拆解
  - 方案生成
  - 规则校验
  - 结果输出

limits:
  max_iterations: 30
  max_input_attempts: 3
  interrupt_timeout_seconds: 300
  tasks:
        call_versatile: 10
        ask_user: 5
  termination_keywords:
    - 终止执行
    - 取消
    - stop

summary:
  format: "需求概述→规划过程→任务执行→结果汇总→异常说明"
  max_length: 500
  required_fields:
    - 理财产品名称
    - 购买金额

scripts:
  tool_start: "正在调用：{tool_name}"
  tool_end: "{tool_name} 执行完成"
  interrupt_start: "需要您确认以下信息"
---

# Markdown body 注入到 LLM 系统提示词
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ScopeConfig(BaseModel):
    """业务范围配置（规则 1）。"""
    allowed: str = Field(default="", description="允许处理的业务类型描述")
    out_of_scope_message: str = Field(
        default="尚在学习中",
        description="超范围时返回的默认话术"
    )


class LimitsConfig(BaseModel):
    """执行限制配置（规则 4、5 + HITL 限制）。"""
    # 规则 4
    max_iterations: int = Field(default=30, ge=1, le=500)
    # HITL
    max_input_attempts: int = Field(default=3, ge=1, le=20)
    interrupt_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    # 规则 5：按工具名配置上限
    tasks: dict[str, int] = Field(default_factory=dict)
    # 用户终止关键词
    termination_keywords: list[str] = Field(
        default_factory=lambda: ["终止执行", "取消", "退出", "stop", "cancel"]
    )


class SummaryConfig(BaseModel):
    """执行总结格式配置（规则 6）。"""
    format: str = Field(
        default="需求概述→规划过程→任务执行情况→结果汇总→异常说明"
    )
    max_length: int = Field(default=500, ge=100, le=2000)
    required_fields: list[str] = Field(default_factory=list)


class ScriptsConfig(BaseModel):
    """话术配置（对应需求文档 §6）。可选，未配置时使用默认。"""
    tool_start: str = Field(default="正在调用：{tool_name}")
    tool_end: str = Field(default="{tool_name} 执行完成")
    todo_start: str = Field(default="开始执行：{title}")
    todo_end: str = Field(default="{title} 已完成")
    todolist_start: str = Field(default="已生成任务规划")
    todolist_end: str = Field(default="任务规划完成")
    interrupt_start: str = Field(default="需要您确认以下信息")


class AgentRuleConfig(BaseModel):
    """AgentRule.md 完整配置（六规则 + 话术）。"""

    # 规则 1
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    # 规则 2
    planning_steps: list[str] = Field(default_factory=list)
    # 规则 3（任务依赖，结构暂时简化为 dict）
    task_dependencies: dict[str, list[str]] = Field(
        default_factory=dict,
        description="任务 ID → 前置任务 ID 列表；暂不强制使用"
    )
    # 规则 4、5
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    # 规则 6
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    # 话术
    scripts: ScriptsConfig = Field(default_factory=ScriptsConfig)
    # 原始 frontmatter（保留以供后续扩展）
    raw_frontmatter: dict[str, Any] = Field(default_factory=dict)
    # 注入到 LLM system prompt 的 markdown body
    markdown_body: str = Field(
        default="",
        description="Markdown body，注入到 LLM 系统提示词"
    )


_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def load_agent_rule(rule_path: str | Path) -> AgentRuleConfig:
    """从 AgentRule.md 加载完整规则。"""
    path = Path(rule_path)
    if not path.exists():
        raise FileNotFoundError(f"AgentRule file not found: {path}")

    content = path.read_text(encoding="utf-8")

    match = _FRONTMATTER_PATTERN.match(content)
    if match:
        yaml_content = match.group(1)
        data = yaml.safe_load(yaml_content) or {}
        markdown_body = content[match.end():].strip()
    else:
        data = {}
        markdown_body = content.strip()

    return AgentRuleConfig(
        scope=ScopeConfig(**(data.get("scope") or {})),
        planning_steps=data.get("planning_steps") or [],
        task_dependencies=data.get("task_dependencies") or {},
        limits=LimitsConfig(**(data.get("limits") or {})),
        summary=SummaryConfig(**(data.get("summary") or {})),
        scripts=ScriptsConfig(**(data.get("scripts") or {})),
        raw_frontmatter=data,
        markdown_body=markdown_body,
    )
