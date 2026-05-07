# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""从 Studio 导出 IR 构建 ReActAgent。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openjiuwen.core.common.schema.param import Param
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.memory.config.config import AgentMemoryConfig
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig as NewReActAgentConfig
from openjiuwen.core.single_agent.legacy.config import LegacyReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen_studio.lowcode.compiler import AgentCompiler
from openjiuwen_studio.lowcode.config_adapter import ConfigAdapter
from openjiuwen_studio.lowcode.schemas import ModelOverride

from runtime_support.runtime_env import get_bool_env, get_env, resolve_llm_api_key_from_env, resolve_memory_scope_id


def build_model_overrides_from_default_llm_env(export_data: dict[str, Any]) -> dict[str, ModelOverride]:
    """按 DEFAULT_LLM_* 与 LLM_KEY__ 推导规则生成 ModelOverride，仅含键 str(agent.model_id)。"""
    agent = export_data.get("agent") if isinstance(export_data.get("agent"), dict) else {}
    mid = agent.get("model_id")
    if mid is None or not str(mid).strip():
        return {}

    model_name = (os.environ.get("DEFAULT_LLM_MODEL_NAME") or "").strip()
    base_url = (os.environ.get("DEFAULT_LLM_API_BASE") or "").strip()
    api_key = (os.environ.get("DEFAULT_LLM_API_KEY") or "").strip()
    provider = (os.environ.get("DEFAULT_LLM_MODEL_PROVIDER") or "").strip()

    if not api_key and base_url:
        api_key = resolve_llm_api_key_from_env(base_url)

    override_kwargs: dict[str, Any] = {}
    if model_name:
        override_kwargs["name"] = model_name
    if base_url:
        override_kwargs["base_url"] = base_url
    if api_key:
        override_kwargs["api_key"] = api_key
    if provider:
        override_kwargs["provider"] = provider

    if not override_kwargs:
        return {}

    return {str(mid): ModelOverride(**override_kwargs)}


def normalize_runtime_config_for_react_agent(
    config: LegacyReActAgentConfig | NewReActAgentConfig,
) -> NewReActAgentConfig:
    """兼容 legacy 与新版 ReActAgentConfig，统一为 core 新版配置。"""
    if isinstance(config, NewReActAgentConfig):
        return config

    model_name = getattr(config, "model_name", "") or ""
    m = getattr(config, "model_config", None)
    info = getattr(m, "model_info", None) if m is not None else None
    model_provider = str(getattr(m, "model_provider", "") or "")
    mcc = ModelClientConfig(
        model_provider=model_provider,
        api_key=str(getattr(info, "api_key", "") or ""),
        api_base=str(getattr(info, "api_base", "") or ""),
        verify_ssl=get_bool_env("LLM_SSL_VERIFY", True),
    )
    mrc = ModelRequestConfig(
        temperature=getattr(info, "temperature", None),
        max_tokens=getattr(info, "max_tokens", None),
        timeout=float(getattr(info, "timeout", 60) or 60),
    )
    ctx_cfg = ContextEngineConfig(
        max_context_message_num=200,
        default_window_round_num=config.constrain.reserved_max_chat_rounds,
    )
    return NewReActAgentConfig(
        mem_scope_id=config.memory_scope_id or "",
        model_name=str(model_name),
        model_provider=model_provider,
        api_key=str(getattr(info, "api_key", "") or ""),
        api_base=str(getattr(info, "api_base", "") or ""),
        prompt_template_name=config.prompt_template_name or "",
        prompt_template=list(config.prompt_template or []),
        max_iterations=config.constrain.max_iteration,
        model_client_config=mcc,
        model_config_obj=mrc,
        context_engine_config=ctx_cfg,
    )


def _agent_memory_config_from_export_memory(memory: Any) -> AgentMemoryConfig:
    """从导出 JSON 的 agent.memory 构建 AgentMemoryConfig；缺省为 false 或空列表。"""
    if not isinstance(memory, dict):
        memory = {}

    raw_vars = memory.get("variable_config")
    if not isinstance(raw_vars, list):
        raw_vars = []

    mem_variables: list[Any] = []
    for var in raw_vars:
        if not isinstance(var, dict):
            continue
        if not var.get("enabled", False):
            continue
        name = str(var.get("name") or "").strip()
        if not name:
            continue
        desc = str(var.get("description") or "")
        mem_variables.append(Param.string(name, description=desc, required=False))

    return AgentMemoryConfig(
        mem_variables=mem_variables,
        # 注意：AgentMemoryConfig 在 core 里默认都是 True；这里必须以导出 IR 为准，
        # 且缺省按 False 处理，避免“开关没开也加载/写入记忆”。
        enable_long_term_mem=bool(memory.get("longterm_memory_config", False)),
        enable_user_profile=bool(memory.get("user_profile_config", False)),
        enable_semantic_memory=bool(memory.get("semantic_memory_config", False)),
        enable_episodic_memory=bool(memory.get("episodic_memory_config", False)),
        enable_summary_memory=bool(memory.get("summary_memory_config", False)),
    )


def _memory_switch_enabled() -> bool:
    """全局记忆开关：默认开启；设置 IR_ENABLE_AGENT_MEMORY=false 可关闭所有记忆加载/写入。"""
    v = (os.environ.get("IR_ENABLE_AGENT_MEMORY") or "true").strip().lower()
    return v not in {"0", "false", "no", "off"}


def _is_agent_memory_cfg_enabled(cfg: AgentMemoryConfig) -> bool:
    """只要任一记忆能力开启，就认为需要挂载 MemoryRail。"""
    if not isinstance(cfg, AgentMemoryConfig):
        return False
    return bool(
        cfg.mem_variables
        or cfg.enable_long_term_mem
        or cfg.enable_user_profile
        or cfg.enable_semantic_memory
        or cfg.enable_episodic_memory
        or cfg.enable_summary_memory
    )


def _ensure_memory_placeholders_in_system_prompt(agent: Any, agent_memory_cfg: AgentMemoryConfig) -> None:
    """
    仅当导出 JSON 的开关开启时，才注入对应占位符。
    MemoryRail 使用 PromptTemplate（占位符前后缀为 {{ 与 }}）渲染 system message 中的记忆变量。
    """
    enable_vars = bool(getattr(agent_memory_cfg, "mem_variables", None))
    enable_long_term = bool(getattr(agent_memory_cfg, "enable_long_term_mem", False))
    if not (enable_vars or enable_long_term):
        return

    cfg = getattr(agent, "_config", None)
    if cfg is None:
        return
    prompt_template = getattr(cfg, "prompt_template", None)
    if not isinstance(prompt_template, list):
        prompt_template = []
        try:
            cfg.prompt_template = prompt_template
        except (AttributeError, TypeError):
            return
    # 空列表时也必须继续：否则 MemoryRail 写入 ctx.extra 的占位符永远不会出现在任何 system 消息里。

    def _has_placeholder(key: str) -> bool:
        token = "{{" + key + "}}"
        for m in prompt_template:
            if not isinstance(m, dict):
                continue
            if m.get("role") != "system":
                continue
            c = m.get("content")
            if isinstance(c, str) and token in c:
                return True
        return False

    ok_long_term = (not enable_long_term) or _has_placeholder("sys_long_term_memory")
    ok_vars = (not enable_vars) or _has_placeholder("sys_memory_variables")
    if ok_long_term and ok_vars:
        return

    lines = ["【系统记忆注入（由服务端自动追加）】"]
    lines.append("你可能会获得以下记忆信息（均为 JSON 字符串），用于辅助回答：")
    if enable_long_term:
        lines.append("- 长期记忆（列表，可能为空）：{{sys_long_term_memory}}")
    if enable_vars:
        lines.append("- 用户记忆变量（字典，可能为空）：{{sys_memory_variables}}")
    lines.append("规则：若相关字段为空，不要编造用户信息。")

    prompt_template.append({"role": "system", "content": "\n".join(lines)})


async def _register_memory_rail_from_export(agent: Any, export_agent: dict[str, Any]) -> None:
    """根据导出 IR 的 memory 配置挂载 MemoryRail（如未开启则跳过）。"""
    if not _memory_switch_enabled():
        return

    memory = export_agent.get("memory") if isinstance(export_agent, dict) else None
    if not isinstance(memory, dict) or not memory:
        return

    agent_memory_cfg = _agent_memory_config_from_export_memory(memory)
    if not _is_agent_memory_cfg_enabled(agent_memory_cfg):
        return

    scope_id = resolve_memory_scope_id(
        raw_memory_scope_id=str(getattr(agent, "_config", None).mem_scope_id or ""),
        default_memory_scope_id=get_env("DEFAULT_MEMORY_SCOPE_ID", ""),
    )

    from openjiuwen.core.application.llm_agent.rails.memory_rail import MemoryRail

    await agent.register_rail(MemoryRail(scope_id, agent_memory_cfg))
    _ensure_memory_placeholders_in_system_prompt(agent, agent_memory_cfg)


def _adapt_runtime_config(agent_config_dict: dict[str, Any]) -> Any:
    adapt_to_runtime = getattr(ConfigAdapter, "adapt_to_runtime_config", None)
    if callable(adapt_to_runtime):
        return adapt_to_runtime(agent_config_dict)
    return ConfigAdapter.adapt(agent_config_dict)


def _agent_card_from_export_agent(export_agent: dict[str, Any]) -> AgentCard:
    return AgentCard(
        id=export_agent.get("agent_id", ""),
        name=export_agent.get("agent_name", "Agent"),
        description=export_agent.get("description", ""),
        version=export_agent.get("agent_version", "draft"),
    )


def _prepend_configs_system_prompt_first(
    export_agent: dict[str, Any],
    runtime_config: NewReActAgentConfig,
) -> None:
    """将导出 IR 中 agent.configs.system_prompt 作为第一条 system 与 prompt_template 合并（置前）。"""
    configs = export_agent.get("configs") if isinstance(export_agent.get("configs"), dict) else None
    if not configs:
        return
    raw = configs.get("system_prompt")
    if not isinstance(raw, str):
        return
    text = raw.strip()
    if not text:
        return
    existing = list(runtime_config.prompt_template or [])
    runtime_config.prompt_template = [{"role": "system", "content": text}, *existing]


async def _compile_runtime_config_from_export_data(
    export_data: dict[str, Any],
    current_user: dict[str, Any],
    model_overrides: dict[str, Any] | None,
) -> tuple[AgentCard, NewReActAgentConfig]:
    compiler = AgentCompiler()
    export_agent = export_data.get("agent") if isinstance(export_data.get("agent"), dict) else {}
    compile_for_runtime = getattr(compiler, "compile_for_runtime", None)
    if callable(compile_for_runtime):
        compile_result = await compile_for_runtime(
            config=export_data,
            model_overrides=model_overrides or None,
            current_user=current_user,
        )
        runtime_config = normalize_runtime_config_for_react_agent(compile_result["runtime_config"])
        _prepend_configs_system_prompt_first(export_agent, runtime_config)
        return (compile_result["agent_card"], runtime_config)

    compiled = await compiler.compile_with_overrides_config(
        config=export_data,
        model_overrides=model_overrides,
        current_user=current_user,
    )
    agent_config_dict = compiled["agent_config"]
    runtime_config = normalize_runtime_config_for_react_agent(_adapt_runtime_config(agent_config_dict))
    _prepend_configs_system_prompt_first(export_agent, runtime_config)
    return (_agent_card_from_export_agent(export_agent), runtime_config)


async def build_react_agent_from_export_data(
    export_data: dict[str, Any],
    current_user: dict[str, Any],
    *,
    model_overrides: dict[str, Any] | None = None,
) -> ReActAgent:
    """由已解析的导出数据构建 ReActAgent。"""
    export_agent = export_data.get("agent") if isinstance(export_data.get("agent"), dict) else {}
    agent_card, runtime_config = await _compile_runtime_config_from_export_data(
        export_data,
        current_user,
        model_overrides or None,
    )
    agent = ReActAgent(card=agent_card)
    agent.configure(runtime_config)
    await _register_memory_rail_from_export(agent, export_agent)
    return agent


async def build_react_agent(
    ir_path: Path,
    current_user: dict[str, Any],
    *,
    model_overrides: dict[str, Any] | None = None,
) -> ReActAgent:
    """由 IR 文件构建 ReActAgent。"""
    export_data = json.loads(ir_path.read_text(encoding="utf-8"))
    return await build_react_agent_from_export_data(
        export_data,
        current_user,
        model_overrides=model_overrides,
    )


async def build_react_agent_from_ir(ir_path: Path, current_user: dict[str, Any]) -> ReActAgent:
    """读取 IR 文件，按进程环境补齐模型覆盖后构建 ReActAgent。"""
    export_data = json.loads(ir_path.read_text(encoding="utf-8"))
    model_overrides = build_model_overrides_from_default_llm_env(export_data)
    return await build_react_agent_from_export_data(
        export_data,
        current_user,
        model_overrides=model_overrides or None,
    )


async def build_react_agent_from_ir_dict(
    ir_root: dict[str, Any],
    current_user: dict[str, Any],
) -> ReActAgent:
    """由已解析的 IR 根对象构建 ReActAgent，模型覆盖规则与按文件读取路径一致。"""
    model_overrides = build_model_overrides_from_default_llm_env(ir_root)
    return await build_react_agent_from_export_data(
        ir_root,
        current_user,
        model_overrides=model_overrides or None,
    )
