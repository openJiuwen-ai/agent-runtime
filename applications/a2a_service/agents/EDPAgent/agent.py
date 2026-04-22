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
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional

from loguru import logger

from .agent_rule import AgentRuleConfig, load_agent_rule
from .config import get_settings
from common.events import (
    AgentEvent,
    ConversationStartEvent, ConversationEndEvent,
    ThinkStartEvent, ThinkChunkEvent, ThinkEndEvent,
    TodoListStartEvent, TodoListItemEvent, TodoListEndEvent,
    TodoStatusEvent,
    ToolStartEvent, ToolEndEvent,
    InterruptStartEvent,
    FinalAnswerStartEvent, FinalAnswerChunkEvent, FinalAnswerEndEvent,
    DelegateRequest,
)

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

    from .rail import (
        VersatileInterruptRail,
        IterationLimitRail,
        ExecutionLimitRail,
        AskUserRail,
    )
    from .tool.query_balance import query_balance_tool
    from .tool.transfer import transfer_tool

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
    agent.configure(config)

    if hasattr(config, "model_client_config") and config.model_client_config is not None:
        config.model_client_config.timeout = settings.llm_timeout

    # ── 注册工具 ─────────────────────────────────────────────────────
    Runner.resource_mgr.add_tool(query_balance_tool)
    agent.ability_manager.add(query_balance_tool.card)
    Runner.resource_mgr.add_tool(transfer_tool)
    agent.ability_manager.add(transfer_tool.card)

    # ── 注册 Rails（顺序决定优先级影响）──────────────────────────────
    # AskUserRail 会在 init() 中自动注册 ask_user 工具
    await agent.register_rail(IterationLimitRail(_agent_rule))
    await agent.register_rail(ExecutionLimitRail(_agent_rule))
    await agent.register_rail(VersatileInterruptRail())
    await agent.register_rail(AskUserRail(_agent_rule))

    _agent = agent
    logger.info(f"[DPA] 初始化完成：agent_id={settings.dpa_agent_id}，已注册 4 个 Rail")


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
    async for raw_event in agent.stream(inputs=stream_inputs, session=session):
        for evt in processor.process(raw_event):
            yield evt

    # 流结束：flush 尾部事件（think_end / final_answer_end 等）
    for evt in processor.finalize():
        yield evt

    # ── 中断检测：VA 委托 / HITL ──────────────────────────────────────
    pending_delegate = session.get_state("pending_delegate")
    if pending_delegate:
        logger.info(
            f"[DPA] 检测到 VA 委托请求：intent={pending_delegate.get('intent')}"
        )
        yield DelegateRequest.model_validate(pending_delegate)
        session.update_state({"pending_delegate": None})
        return

    # HITL 中断检测（AskUserRail 写入的 interrupt 信息）
    interrupt_info = session.get_state("interrupt_info")
    if interrupt_info:
        logger.info(f"[DPA] 检测到 HITL 中断：{interrupt_info}")
        yield InterruptStartEvent(
            interrupt_id=interrupt_info.get("interrupt_id", str(uuid.uuid4())),
            content=interrupt_info.get("message", "请确认"),
            context=interrupt_info.get("context", {}),
        )
        session.update_state({"interrupt_info": None})
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


# Todolist 约定代码块正则
_TODOLIST_RE = re.compile(r"```todolist\s*\n(.*?)\n```", re.DOTALL)
_TODO_UPDATE_RE = re.compile(r"```todo_update\s*\n(.*?)\n```", re.DOTALL)


class _StreamProcessor:
    """
    把 Runner 原始事件流转换为细粒度 AgentEvent 的状态机。

    原始事件类型：
      llm_reasoning   → think_start / think_chunk / think_end + todolist 解析
      llm_output      → final_answer_start / final_answer_chunk
      answer          → final_answer_end（可能跟在 llm_output 后，也可能独立）
      tool_start      → ToolStartEvent
      tool_end        → ToolEndEvent

    think_end 触发时，扫描累积文本中的 ```todolist``` 和 ```todo_update``` 代码块。
    """

    STATE_IDLE = "idle"
    STATE_THINKING = "thinking"
    STATE_ANSWERING = "answering"

    def __init__(self) -> None:
        self.state = self.STATE_IDLE
        self._think_buffer = ""
        self._answer_buffer = ""
        self._emitted_todolist_ids: set = set()

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
        if event_type == "llm_output":
            # 离开 thinking 状态
            events.extend(self._flush_thinking_if_needed())
            if self.state != self.STATE_ANSWERING:
                events.append(FinalAnswerStartEvent())
                self.state = self.STATE_ANSWERING
                self._answer_buffer = ""
            events.append(FinalAnswerChunkEvent(content=content))
            self._answer_buffer += content
            return events

        # ── 最终答案完成（answer）────────────────────────────────────
        if event_type == "answer":
            events.extend(self._flush_thinking_if_needed())
            if self.state == self.STATE_ANSWERING:
                # 流式已给过 chunk，这里只补 end
                events.append(FinalAnswerEndEvent(content=self._answer_buffer))
            else:
                # 没有流式 output，直接 start + chunk + end
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
            events.append(ToolStartEvent(
                content=content,
                plugin=payload.get("plugin", ""),
                args=payload.get("args", {}) if isinstance(payload.get("args"), dict) else {},
            ))
            return events

        if event_type == "tool_end":
            events.extend(self._flush_thinking_if_needed())
            events.extend(self._flush_answer_if_needed())
            events.append(ToolEndEvent(
                content=content,
                plugin=payload.get("plugin", ""),
                data=payload.get("data", {}) if isinstance(payload.get("data"), dict) else {},
            ))
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
        # 解析 todolist 代码块
        events.extend(self._parse_todolist_blocks(self._think_buffer))
        events.extend(self._parse_todo_update_blocks(self._think_buffer))
        events.append(ThinkEndEvent())
        self.state = self.STATE_IDLE
        self._think_buffer = ""
        return events

    def _flush_answer_if_needed(self) -> list[AgentEvent]:
        if self.state != self.STATE_ANSWERING:
            return []
        events = [FinalAnswerEndEvent(content=self._answer_buffer)]
        self.state = self.STATE_IDLE
        self._answer_buffer = ""
        return events

    def _parse_todolist_blocks(self, text: str) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        for m in _TODOLIST_RE.finditer(text):
            body = m.group(1).strip()
            try:
                items = json.loads(body)
            except json.JSONDecodeError as e:
                logger.warning(f"[DPA] todolist JSON 解析失败：{e}")
                continue
            if not isinstance(items, list):
                continue
            events.append(TodoListStartEvent())
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    ev = TodoListItemEvent(**item)
                except Exception as e:
                    logger.warning(f"[DPA] todolist item 校验失败：{e}")
                    continue
                events.append(ev)
                self._emitted_todolist_ids.add(str(ev.id))
            events.append(TodoListEndEvent(count=len(items)))
        return events

    def _parse_todo_update_blocks(self, text: str) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        for m in _TODO_UPDATE_RE.finditer(text):
            body = m.group(1).strip()
            try:
                upd = json.loads(body)
            except json.JSONDecodeError as e:
                logger.warning(f"[DPA] todo_update JSON 解析失败：{e}")
                continue
            if not isinstance(upd, dict):
                continue
            try:
                events.append(TodoStatusEvent(**upd))
            except Exception as e:
                logger.warning(f"[DPA] todo_update 校验失败：{e}")
        return events
