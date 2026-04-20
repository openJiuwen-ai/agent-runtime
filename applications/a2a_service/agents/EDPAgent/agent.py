"""
DPA Agent 唯一公开入口。

公开接口：
  - initialize_dpa()  — 应用启动时调用一次，配置 Runner 和 ReActAgent
  - agent_stream()    — 每次用户请求时调用，流式返回 AgentEvent

零 A2A 依赖：本模块不引用任何 a2a.* 模块。
"""
from __future__ import annotations

from typing import AsyncGenerator, Optional

from loguru import logger

from .config import get_settings
from common.events import AgentEvent, AnswerEvent, DelegateRequest, ThoughtEvent

# ── 模块级单例（DPA 初始化一次）─────────────────────────────────────────────
_agent = None


# ─────────────────────────────────────────────────────────────────────────────
# 公开接口
# ─────────────────────────────────────────────────────────────────────────────


async def initialize_dpa() -> None:
    """
    应用启动时调用一次。

    执行内容：
      1. 配置 Redis Checkpointer（断点续轮核心）
      2. 启动 Runner
      3. 创建 ReActAgent 并注册工具和 Rail
    """
    global _agent
    if _agent is not None:
        logger.debug("[DPA] 已初始化，跳过重复初始化")
        return

    settings = get_settings()

    # ── 延迟 import，保持 events.py / adapter.py 等轻量模块的快速加载 ──────
    import openjiuwen.extensions.checkpointer.redis.checkpointer  # noqa: F401  确保 Checkpointer 已注册

    from openjiuwen.core.runner import Runner
    from openjiuwen.core.runner.runner_config import DEFAULT_RUNNER_CONFIG
    from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig
    from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig

    from .prompt import build_system_prompt
    from .rail.versatile_interrupt_rail import VersatileInterruptRail
    from .tool.query_balance import query_balance_tool
    from .tool.transfer import transfer_tool

    # ── 配置 Redis Checkpointer ──────────────────────────────────────────────
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

    # ── 创建 ReActAgent ──────────────────────────────────────────────────────
    card = AgentCard(id=settings.dpa_agent_id, name=settings.dpa_agent_name)
    agent = ReActAgent(card=card)

    config = (
        ReActAgentConfig()
        .configure_model_client(
            provider=settings.llm_provider,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            model_name=settings.llm_model_name,
            verify_ssl=settings.llm_verify_ssl,
        )
        .configure_prompt_template(
            [{"role": "system", "content": build_system_prompt()}]
        )
        .configure_max_iterations(settings.dpa_max_iterations)
    )
    agent.configure(config)

    # ── 注册工具 ─────────────────────────────────────────────────────────────
    Runner.resource_mgr.add_tool(query_balance_tool)
    agent.ability_manager.add(query_balance_tool.card)

    Runner.resource_mgr.add_tool(transfer_tool)
    agent.ability_manager.add(transfer_tool.card)

    # ── 注册 Rail（零 A2A 依赖）──────────────────────────────────────────────
    await agent.register_rail(VersatileInterruptRail())

    _agent = agent
    logger.info(f"[DPA] 初始化完成：agent_id={settings.dpa_agent_id}")


async def agent_stream(
    query: str,
    conv_id: str,
    cascade_result: Optional[dict] = None,
    context: Optional[dict] = None,
) -> AsyncGenerator[AgentEvent, None]:
    """
    DPA 唯一入口。零 A2A 依赖。

    首轮（cascade_result=None）：
      - session.pre_run → runner.run_stream → yield events
      - 若 VersatileInterruptRail 触发 interrupt：session state 写入 pending_delegate
      - stream 结束后检查 pending → yield DelegateRequest

    Cascade 续轮（cascade_result 非 None）：
      - session 从 Checkpoint 恢复（pre_run 自动加载）
      - 注入 cascade_result → VersatileInterruptRail 读取 → reject(tool_result) → runner 继续
      - yield events（可能再次触发中断事件 → 多轮委托）

    Args:
        query:          用户输入文本（续轮时传 "continue"，实际 query 由 Checkpoint 恢复）
        conv_id:        会话 ID（跨轮复用，Checkpointer 按此 ID 存档）
        cascade_result: Cascade 续轮时注入的 WorkflowAgent 结果（dict）
        context:        透传上下文，通常包含 {"body": original_body}

    Yields:
        ThoughtEvent    — LLM 思考过程
        AnswerEvent     — 回答片段或最终回答（final=True 时流即将结束）
        DelegateRequest — 委托请求（流中只出现一次，出现后 agent_stream 即结束）
    """
    agent = _get_agent()
    original_body = (context or {}).get("body", {})

    from openjiuwen.core.session.agent import create_agent_session

    session = create_agent_session(session_id=conv_id, card=agent.card)

    if cascade_result is not None:
        # ── Cascade 续轮路径 ──────────────────────────────────────────────
        logger.info(f"[DPA] Cascade 续轮：conv_id={conv_id}")
        await session.pre_run(inputs={"query": "continue", "conversation_id": conv_id})
        session.update_state(
            {
                "cascade_result": cascade_result,
                "original_body": original_body,
                "pending_delegate": None,  # 防止 pre_run 恢复旧值导致循环触发
            }
        )
        async for raw_event in agent.stream(
            inputs={"query": "continue", "conversation_id": conv_id},
            session=session,
        ):
            event = _convert_event(raw_event)
            if event is not None:
                yield event

    else:
        # ── 首轮路径 ──────────────────────────────────────────────────────
        logger.info(f"[DPA] 首轮：conv_id={conv_id}, query={query!r:.60}")
        await session.pre_run(inputs={"query": query, "conversation_id": conv_id})
        session.update_state({"original_body": original_body})

        async for raw_event in agent.stream(
            inputs={"query": query, "conversation_id": conv_id},
            session=session,
        ):
            event = _convert_event(raw_event)
            if event is not None:
                yield event

    # ── Rail 中断后的挂起事件检查 ─────────────────────────────────────────
    # VersatileInterruptRail 在 interrupt() 前把委托信息写入 session state
    pending_delegate = session.get_state("pending_delegate")
    if pending_delegate:
        logger.info(
            f"[DPA] 检测到委托请求：intent={pending_delegate.get('intent')}, "
            f"desc={pending_delegate.get('task_description', '')!r:.60}"
        )
        yield DelegateRequest.model_validate(pending_delegate)
        session.update_state({"pending_delegate": None})  # 消费掉，避免续轮重复触发
        return


# ─────────────────────────────────────────────────────────────────────────────
# 内部辅助
# ─────────────────────────────────────────────────────────────────────────────


def _get_agent():
    if _agent is None:
        raise RuntimeError(
            "DPA 未初始化，请先调用 await initialize_dpa()"
        )
    return _agent


def _convert_event(raw_event) -> Optional[AgentEvent]:
    """
    OpenJiuwen Runner 原始事件 → DPA 内部事件类型。

    Runner 实际 yield 的 OutputSchema 事件类型：
      "llm_reasoning" → ThoughtEvent（LLM 推理过程）
      "llm_output"    → AnswerEvent(final=False)（LLM 流式输出片段）
      "answer"        → AnswerEvent(final=True)（invoke 完成后的最终结果）

    内容在 payload["output"] 或 payload["content"] 中，不在 event.content 上。
    """
    if raw_event is None:
        return None

    event_type = getattr(raw_event, "type", None)
    payload = getattr(raw_event, "payload", None) or {}

    if isinstance(payload, dict):
        content = payload.get("output") or payload.get("content") or ""
    else:
        content = ""

    if event_type == "llm_reasoning":
        return ThoughtEvent(content=content)

    if event_type == "llm_output":
        return AnswerEvent(content=content, final=False)

    if event_type == "answer":
        return AnswerEvent(content=content, final=True)

    return None
