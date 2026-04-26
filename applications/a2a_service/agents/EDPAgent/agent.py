"""
EDPAgent 唯一公开入口（对齐需求文档 §4.5 的 17 种事件序列）。

公开接口：
  - initialize_dpa()  — 应用启动时调用一次，配置 Runner 和 ReActAgent
  - agent_stream()    — 每次用户请求时调用，流式返回 AgentEvent

零 A2A 依赖。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import AsyncGenerator, Optional

from loguru import logger

from .agent_rule import AgentRuleConfig, load_agent_rule
from .config import get_settings
from .prompt import build_system_prompt
from common.events import (
    AgentEvent,
    ConversationStartEvent, ConversationEndEvent,
    ThinkStartEvent, ThinkChunkEvent, ThinkEndEvent,
    TodoListStartEvent, TodoListItemEvent, TodoListEndEvent,
    TodoStartEvent, TodoStatusEvent,
    ToolStartEvent, ToolStatusEvent, ToolEndEvent,
    InterruptStartEvent,
    FinalAnswerStartEvent, SummaryEvent, FinalAnswerChunkEvent, FinalAnswerEndEvent,
    DelegateRequest,
)


def _log_stream_payload(evt: AgentEvent) -> None:
    """在每次 yield AgentEvent 前打一条日志（对齐抓包的 stream payload 行）。"""
    event_type = getattr(evt, "type", "<unknown>")
    content = getattr(evt, "content", "") or ""
    # 截断过长 content 避免刷屏
    preview = content if len(content) <= 120 else content[:117] + "..."
    logger.info(f"[EDPAgent] stream payload [{event_type}]: {preview}")

# ── 模块级单例 ──────────────────────────────────────────────────────────
_agent = None
_agent_rule: Optional[AgentRuleConfig] = None

_AGENT_RULE_PATH = Path(__file__).parent / "AgentRule.md"


# ════════════════════════════════════════════════════════════════════
# 公开接口
# ════════════════════════════════════════════════════════════════════


async def initialize_dpa() -> None:
    """应用启动时调用一次。"""
    global _agent, _agent_rule
    if _agent is not None:
        logger.debug("[DPA] 已初始化，跳过重复初始化")
        return

    settings = get_settings()

    import openjiuwen.extensions.checkpointer.redis.checkpointer  # noqa: F401

    from openjiuwen.core.runner import Runner
    from openjiuwen.core.runner.runner_config import DEFAULT_RUNNER_CONFIG
    from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig
    from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
    from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard

    from .rail import (
        VersatileInterruptRail,
        IterationLimitRail,
        ExecutionLimitRail,
    )
    from .tool import TOOLS

    # ── 加载 AgentRule.md ────────────────────────────────────────────
    try:
        _agent_rule = load_agent_rule(_AGENT_RULE_PATH)
        system_prompt = _agent_rule.markdown_body
        logger.info(
            f"[DPA] AgentRule 加载成功：body={len(system_prompt)} 字符, "
            f"scope='{_agent_rule.scope.allowed[:30]}...', "
            f"max_iter={_agent_rule.limits.max_iterations}, "
            f"task_limits={_agent_rule.limits.tasks}"
        )
    except FileNotFoundError:
        _agent_rule = AgentRuleConfig()
        system_prompt = "你是一个基金理财智能体。"
        logger.warning("[DPA] AgentRule.md 未找到，使用默认配置")

    system_prompt = f"{system_prompt.strip()}\n\n{build_system_prompt().strip()}"

    # ── 配置 Redis Checkpointer ──────────────────────────────────────
    runner_config = DEFAULT_RUNNER_CONFIG.model_copy(deep=True)
    runner_config.checkpointer_config = CheckpointerConfig(
        type="redis",
        conf={
            "connection": {"url": settings.redis_url},
            "ttl": {
                "default_ttl": settings.redis_checkpointer_ttl_minutes,
                "refresh_on_read": True,
            },
        },
    )
    Runner.set_config(runner_config)
    await Runner.start()
    logger.info("[DPA] Runner 已启动，Checkpointer=redis")

    # ── 注册 SysOperationCard（Skill read_file / 沙箱归一化依赖）──────────
    sysop_card = SysOperationCard(
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=None),
    )
    Runner.resource_mgr.add_sys_operation(sysop_card)
    logger.info(f"[DPA] SysOperationCard 已注册：id={sysop_card.id}")

    # ── 创建 ReActAgent ──────────────────────────────────────────────
    card = AgentCard(id=settings.dpa_agent_id, name=settings.dpa_agent_name)
    agent = ReActAgent(card=card)
    config = ReActAgentConfig()

    # 自定义 header
    if settings.custom_headers:
        if hasattr(config, "configure_custom_headers"):
            config.configure_custom_headers(settings.custom_headers)
            logger.info(f"[DPA] 自定义请求头已配置：{list(settings.custom_headers.keys())}")
        else:
            logger.warning("[DPA] SDK 不支持 configure_custom_headers，header 未生效")

    config = (
        config.configure_model_client(
            provider=settings.llm_provider,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            model_name=settings.llm_model_name,
            verify_ssl=settings.llm_verify_ssl,
        )
        .configure_prompt_template([{"role": "system", "content": system_prompt}])
        .configure_max_iterations(_agent_rule.limits.max_iterations)
    )
    config.sys_operation_id = sysop_card.id
    agent.configure(config)

    if hasattr(config, "model_client_config") and config.model_client_config is not None:
        config.model_client_config.timeout = settings.llm_timeout

    # ── 注册 read_file（Skill 按需读取 SKILL.md）──────────────────────
    read_file_card = Runner.resource_mgr.get_sys_op_tool_cards(
        sys_operation_id=sysop_card.id,
        operation_name="fs",
        tool_name="read_file",
    )
    if read_file_card is not None:
        agent.ability_manager.add(read_file_card)
        logger.info("[DPA] read_file 已加入 Agent 能力集")
    else:
        logger.warning("[DPA] 未获取到 read_file 能力卡，Skill 将无法按需读取 SKILL.md")

    # ── 注册工具 ─────────────────────────────────────────────────────
    for tool in TOOLS:
        Runner.resource_mgr.add_tool(tool)
        agent.ability_manager.add(tool.card)

    # ── 注册 Rails（顺序决定优先级影响）──────────────────────────────
    await agent.register_rail(IterationLimitRail(_agent_rule))
    await agent.register_rail(ExecutionLimitRail(_agent_rule))
    await agent.register_rail(VersatileInterruptRail(sys_operation_id=sysop_card.id))

    # ── 注册 Skill（运行时按需 read_file 渐进披露）──────────────────
    skills_root = Path(__file__).resolve().parent / "skills"
    skill_count = 0
    if skills_root.exists():
        for skill_dir in sorted(skills_root.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                await agent.register_skill(str(skill_dir))
                skill_count += 1
    else:
        logger.warning(f"[DPA] 技能目录不存在：{skills_root}")

    _agent = agent
    logger.info(
        f"[DPA] 初始化完成：agent_id={settings.dpa_agent_id}，"
        f"已注册 3 个 Rail，skills={skill_count}"
    )


async def agent_stream(
    query: str,
    conv_id: str,
    cascade_result: Optional[dict] = None,
    context: Optional[dict] = None,
) -> AsyncGenerator[AgentEvent, None]:
    """
    EDPAgent 唯一请求入口。yield 17 种细粒度事件。

    事件顺序（典型）：
      conversation_start
        → think_start → think_chunk* → [todolist_*] → [todo_status*] → think_end
        → tool_start → tool_end
        → think_start → think_chunk* → think_end
        → ...
        → final_answer_start → final_answer_chunk* → final_answer_end
      conversation_end
    """
    agent = _get_agent()
    original_body = (context or {}).get("body", {})

    from openjiuwen.core.session.agent import create_agent_session

    session = create_agent_session(session_id=conv_id, card=agent.card)

    # 仅在"外部用户请求首轮"发会话开始事件；cascade 续轮不发
    is_external_turn = cascade_result is None
    if is_external_turn:
        yield ConversationStartEvent()

    # ── 首轮 / 续轮路径 ───────────────────────────────────────────────
    if cascade_result is not None:
        logger.info(f"[DPA] Cascade 续轮：conv_id={conv_id}")
        await session.pre_run(inputs={"query": "continue", "conversation_id": conv_id})
        session.update_state({
            "cascade_result": cascade_result,
            "original_body": original_body,
            "pending_delegate": None,
        })
        stream_inputs = {"query": "continue", "conversation_id": conv_id}
    else:
        logger.info(f"[DPA] 首轮：conv_id={conv_id}, query={query!r:.60}")
        await session.pre_run(inputs={"query": query, "conversation_id": conv_id})
        session.update_state({"original_body": original_body})
        stream_inputs = {"query": query, "conversation_id": conv_id}

    # ── 状态机处理 Runner 流 ─────────────────────────────────────────
    processor = _StreamProcessor()
    raw_event_count = 0
    async for raw_event in agent.stream(inputs=stream_inputs, session=session):
        raw_event_count += 1
        logger.debug(
            f"[DPA] raw event #{raw_event_count}: type={getattr(raw_event, 'type', None)}"
        )
        for evt in processor.process(raw_event):
            _log_stream_payload(evt)
            yield evt

    # 流结束：flush 尾部事件（think_end / final_answer_end 等）
    for evt in processor.finalize():
        _log_stream_payload(evt)
        yield evt
    logger.debug(
        f"[DPA] agent.stream() 结束：共处理 {raw_event_count} 个 raw event"
    )

    # ── 中断检测：VA 委托 ─────────────────────────────────────────────
    pending_delegate = session.get_state("pending_delegate")
    if pending_delegate:
        logger.info(
            f"[DPA] 检测到 VA 委托请求：intent={pending_delegate.get('intent')}"
        )
        yield DelegateRequest.model_validate(pending_delegate)
        session.update_state({"pending_delegate": None})
        return

    # 会话正常结束（只在外部请求首轮入口侧发；cascade 续轮是中间态不发）
    if is_external_turn:
        yield ConversationEndEvent()


# ════════════════════════════════════════════════════════════════════
# 内部辅助
# ════════════════════════════════════════════════════════════════════


def _get_agent():
    if _agent is None:
        raise RuntimeError("EDPAgent 未初始化，请先调用 await initialize_dpa()")
    return _agent


class _StreamProcessor:
    """
    把 Runner 原始事件流转换为细粒度 AgentEvent 的状态机。

    原始事件类型：
      llm_reasoning   → think_start / think_chunk / think_end
      llm_output      → final_answer_start / final_answer_chunk
      answer          → final_answer_end（可能跟在 llm_output 后，也可能独立）
      tool_start      → ToolStartEvent
      tool_end        → ToolEndEvent

    Todo 任务通过 todolist_create / todolist_modify 工具管理，
    工具执行结果在 tool_end 时解析为 TodoList/TodoStatus 事件。
    """

    STATE_IDLE = "idle"
    STATE_THINKING = "thinking"
    STATE_ANSWERING = "answering"

    def __init__(self) -> None:
        self.state = self.STATE_IDLE
        self._think_buffer = ""
        self._answer_buffer = ""
        self._emitted_todolist_ids: set = set()
        # id → title 缓存，用于 todo_update 时填充 todo_start/status 的 content
        self._todo_titles: dict[str, str] = {}
        # 已发过 TodoStartEvent 的 id 集合，避免重复 start
        self._started_todo_ids: set = set()

    def process(self, raw_event) -> list[AgentEvent]:
        """把一个原始 event 转为零或多个 AgentEvent。"""
        if raw_event is None:
            return []

        event_type = getattr(raw_event, "type", None)
        payload = getattr(raw_event, "payload", None) or {}
        if not isinstance(payload, dict):
            payload = {}
        content = payload.get("output") or payload.get("content") or ""

        events: list[AgentEvent] = []

        # ── 反思流（llm_reasoning）────────────────────────────────────
        if event_type == "llm_reasoning":
            if self.state != self.STATE_THINKING:
                # 先 flush 其他状态
                events.extend(self._flush_answer_if_needed())
                events.append(ThinkStartEvent())
                self.state = self.STATE_THINKING
                self._think_buffer = ""
            events.append(ThinkChunkEvent(content=content))
            self._think_buffer += content
            return events

        # ── 最终答案流（llm_output）──────────────────────────────────
        # 规范定义（feat-north-api-sse.md §4.5.9）：
        #   流式片段走 SummaryEvent（token by token）
        #   全量一次性帧走 FinalAnswerChunkEvent（由 answer 事件触发补发）
        if event_type == "llm_output":
            # 离开 thinking 状态
            events.extend(self._flush_thinking_if_needed())
            if self.state != self.STATE_ANSWERING:
                events.append(FinalAnswerStartEvent())
                self.state = self.STATE_ANSWERING
                self._answer_buffer = ""
            events.append(SummaryEvent(content=content))
            self._answer_buffer += content
            return events

        # ── 最终答案完成（answer）────────────────────────────────────
        if event_type == "answer":
            events.extend(self._flush_thinking_if_needed())
            if self.state == self.STATE_ANSWERING:
                # 流式已给过 summary × N，这里补一条全量 final_answer_chunk + end
                events.append(FinalAnswerChunkEvent(content=self._answer_buffer))
                events.append(FinalAnswerEndEvent(content=self._answer_buffer))
            else:
                # 没有流式 output，直接 start + chunk(全量) + end
                events.append(FinalAnswerStartEvent())
                events.append(FinalAnswerChunkEvent(content=content))
                events.append(FinalAnswerEndEvent(content=content))
            self.state = self.STATE_IDLE
            self._answer_buffer = ""
            return events

        # ── 工具调用 ─────────────────────────────────────────────────
        if event_type == "tool_start":
            events.extend(self._flush_thinking_if_needed())
            events.extend(self._flush_answer_if_needed())
            plugin = payload.get("plugin", "")
            args = payload.get("args", {}) if isinstance(payload.get("args"), dict) else {}
            events.append(ToolStartEvent(
                content=content,
                plugin=plugin,
                args=args,
            ))
            # 跟一个 tool_status（对齐抓包；前端把它当"运行中"提示）
            # content 与 tool_start 同步，简化实现；如需"正在…"措辞，可在话术层定制
            events.append(ToolStatusEvent(
                plugin=plugin,
                content=content,
            ))
            return events

        if event_type == "tool_end":
            events.extend(self._flush_thinking_if_needed())
            events.extend(self._flush_answer_if_needed())
            plugin = payload.get("plugin", "")
            tool_data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
            events.append(ToolEndEvent(
                content=content,
                plugin=plugin,
                data=tool_data,
            ))
            # 解析 Todo 工具调用结果，转换为 TodoList 事件
            if plugin in ("todolist_create", "todolist_modify", "todolist_query"):
                events.extend(self._parse_todo_tool_result(tool_data, plugin))
            return events

        # 未识别的事件忽略
        return events

    def finalize(self) -> list[AgentEvent]:
        """流结束时 flush 尚未闭合的状态。"""
        events: list[AgentEvent] = []
        events.extend(self._flush_thinking_if_needed())
        events.extend(self._flush_answer_if_needed())
        return events

    # ── 内部 flush 辅助 ─────────────────────────────────────────────

    def _flush_thinking_if_needed(self) -> list[AgentEvent]:
        if self.state != self.STATE_THINKING:
            return []
        events = []
        events.append(ThinkEndEvent())
        self.state = self.STATE_IDLE
        self._think_buffer = ""
        return events

    def _flush_answer_if_needed(self) -> list[AgentEvent]:
        if self.state != self.STATE_ANSWERING:
            return []
        # 被其他事件打断时，补全量 final_answer_chunk + end（保证前端拿到权威文本）
        events: list[AgentEvent] = [
            FinalAnswerChunkEvent(content=self._answer_buffer),
            FinalAnswerEndEvent(content=self._answer_buffer),
        ]
        self.state = self.STATE_IDLE
        self._answer_buffer = ""
        return events

    # ════════════════════════════════════════════════════════════════════
    # Todo 工具结果解析
    # ════════════════════════════════════════════════════════════════════

    def _map_todo_status(self, status: str) -> str:
        """状态映射：TodoStatus 枚举值 → AgentEvent status 字符串"""
        mapping = {
            "PENDING": "pending",
            "IN_PROGRESS": "in_progress",
            "COMPLETED": "done",
            "CANCELLED": "done",  # cancelled 映射为 done
            "FAILED": "failed",
        }
        return mapping.get(status.upper(), status.lower())

    def _parse_todo_tool_result(self, tool_data: dict, tool_name: str) -> list[AgentEvent]:
        """解析 Todo 工具调用结果，转换为 TodoList 事件"""
        events: list[AgentEvent] = []

        if tool_name == "todolist_create":
            # todolist_create 返回 {"tasks": [...], "count": N}
            tasks = tool_data.get("tasks", [])
            if tasks:
                events.append(TodoListStartEvent())
                for task in tasks:
                    # 使用 index 作为 id（用户要求用原有 id）
                    task_id = task.get("index", 0)
                    task_content = task.get("content", "")
                    task_status = self._map_todo_status(task.get("status", "PENDING"))
                    events.append(TodoListItemEvent(
                        id=task_id,
                        title=task_content,
                        status=task_status,
                    ))
                    # 缓存 id → title，供后续状态更新使用
                    self._todo_titles[str(task_id)] = task_content
                    self._emitted_todolist_ids.add(str(task_id))
                events.append(TodoListEndEvent(count=len(tasks)))
                logger.info(
                    f"[DPA] 从 todolist_create 工具发现 {len(tasks)} 个任务"
                )

        elif tool_name == "todolist_query":
            # todolist_query 返回 {"tasks": [...], "count": N}
            tasks = tool_data.get("tasks", [])
            if tasks:
                for task in tasks:
                    # 使用 index 作为 id
                    task_id = task.get("index", 0)
                    task_content = task.get("content", "")
                    task_status = self._map_todo_status(task.get("status", "PENDING"))
                    events.append(TodoListItemEvent(
                        id=task_id,
                        title=task_content,
                        status=task_status,
                    ))
                    # 缓存 id → title，供后续状态更新使用
                    self._todo_titles[str(task_id)] = task_content
                    self._emitted_todolist_ids.add(str(task_id))
                logger.info(
                    f"[DPA] 从 todolist_query 工具发现 {len(tasks)} 个任务"
                )

        elif tool_name == "todolist_modify":
            # todolist_modify 返回 {"task": {...}}
            task = tool_data.get("task", {})
            if task:
                task_id = task.get("index", 0)
                task_status = self._map_todo_status(task.get("status", "PENDING"))
                task_content = task.get("content", "")

                # 如果是刚变为 in_progress，先发 todo_start
                if task_status == "in_progress" and str(task_id) not in self._started_todo_ids:
                    events.append(TodoStartEvent(
                        id=task_id,
                        title=task_content,
                        content=task_content,
                    ))
                    self._started_todo_ids.add(str(task_id))
                    logger.debug(
                        f"[DPA] todolist_modify 触发 todo_start: id={task_id}"
                    )

                # 发出状态更新事件
                events.append(TodoStatusEvent(
                    id=task_id,
                    status=task_status,
                    content=task_content or self._todo_titles.get(str(task_id), ""),
                ))
                logger.debug(
                    f"[DPA] todolist_modify 触发 todo_status: id={task_id}, status={task_status}"
                )

        return events
