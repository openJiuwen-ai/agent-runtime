# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent 模板内容生成器

为 openjiuwen new agent 命令提供三种模板类型：
- empty:   空白 Agent，回显用户消息，无 LLM / 无工具
- react:   ReAct Agent，带 LLM 和示例工具
- workflow: Workflow Agent，带 LLM 和最简工作流
"""


def get_pyproject(project_name: str, pkg_name: str, template_type: str) -> str:
    """生成 pyproject.toml 内容。

    Args:
        project_name: 连字符形式的工程名 (如 my-agent)
        pkg_name: 下划线形式的包名 (如 my_agent)
        template_type: empty | react | workflow
    """
    needs_openjiuwen = template_type in ("react", "workflow")
    deps = [
        '"fastapi==0.115.11"',
        '"uvicorn[standard]==0.42.0"',
        '"pydantic==2.11.7"',
    ]
    if needs_openjiuwen:
        deps.insert(0, '"openjiuwen==0.1.10"')

    deps_str = ",\n    ".join(deps)
    desc = _description(template_type)
    return f'''[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{project_name}-runner"
version = "0.1.0"
description = "{desc}"
requires-python = ">=3.11.4"
dependencies = [
    {deps_str}
]

[project.scripts]
{project_name}-runner = "openjiuwen_runtime.{pkg_name}.__main__:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["openjiuwen_runtime*"]
'''


def get_init() -> str:
    return "# coding: utf-8\n# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved\n"


def get_main(pkg_name: str) -> str:
    return f'''#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved


def main():
    from .{pkg_name}_runner import app
    app.run()


if __name__ == "__main__":
    main()
'''


def get_runner(pkg_name: str, template_type: str) -> str:
    if template_type == "react":
        return _runner_react(pkg_name)
    if template_type == "workflow":
        return _runner_workflow(pkg_name)
    return _runner_empty(pkg_name)


# ==================== empty 模板 ====================

def _runner_empty(pkg_name: str) -> str:
    return f'''#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
{pkg_name} - 空白 Agent 模板

运行方式:
    python -m openjiuwen_runtime.{pkg_name} --port 8090
"""

from typing import AsyncIterator, Tuple

from openjiuwen_runtime.service.app.agent_app import AgentApp

app = AgentApp(
    app_name="{pkg_name}",
    app_description="空白 Agent 模板",
    version="0.1.0",
)


@app.init
async def init():
    print("{pkg_name} 初始化完成!")


@app.query
async def query(msgs, request, cancel_event=None) -> AsyncIterator[Tuple[dict, bool]]:
    """处理查询请求"""
    last_user_msg = None
    for msg in reversed(msgs or []):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    if not last_user_msg:
        yield {{"type": "text", "content": "请输入您的问题"}}, True
        return

    reply = f"收到：{{last_user_msg}}"
    yield {{"type": "text_delta", "content": reply}}, False
    yield {{"type": "result", "content": reply}}, True


@app.shutdown
async def shutdown():
    print("{pkg_name} 关闭")


if __name__ == "__main__":
    app.run()
'''


# ==================== react 模板 ====================

def _runner_react(pkg_name: str) -> str:
    return f'''#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
{pkg_name} - ReAct Agent 模板

运行方式:
    python -m openjiuwen_runtime.{pkg_name} --port 8090

环境变量:
    API_BASE       - 模型 API 地址
    API_KEY        - 模型 API Key
    MODEL_NAME     - 模型名称
    MODEL_PROVIDER - 模型提供商
"""

import asyncio
import os
from typing import AsyncIterator, Tuple

os.environ.setdefault("SSRF_PROTECT_ENABLED", "false")
os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.tool import RestfulApi, RestfulApiCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig, ReActAgent

from openjiuwen_runtime.service.app.agent_app import AgentApp

# ==================== 配置 ====================
API_BASE = os.getenv("API_BASE", "https://api.siliconflow.cn/v1")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "Qwen/Qwen3-8B")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "siliconflow")


def _create_tool():
    """创建示例工具（地理编码查询）"""
    card = RestfulApiCard(
        name="GeocodingSearch",
        description="根据城市名称查询经纬度信息",
        url="https://geocoding-api.open-meteo.com/v1/search",
        method="GET",
        headers={{}},
        input_params={{
            "type": "object",
            "properties": {{
                "name": {{"type": "string", "description": "城市名称（英文）"}},
                "count": {{"type": "string", "description": "返回结果数量"}},
                "language": {{"type": "string", "description": "语言"}},
                "format": {{"type": "string", "description": "返回格式"}},
            }},
            "required": ["name"],
        }},
    )
    return RestfulApi(card=card)


def _build_prompt_template():
    return [
        {{
            "role": "system",
            "content": "你是一个地理助手。你可以根据已有工具，为用户查询指定地点的经纬度信息。\\n注意：调用工具时，城市名称请使用英文。",
        }}
    ]


# ==================== AgentApp ====================

app = AgentApp(
    app_name="{pkg_name}",
    app_description="ReAct Agent 模板，带地理编码工具",
    version="0.1.0",
)


@app.init
async def init():
    print("=" * 50)
    print("{pkg_name} (ReAct) 初始化中...")
    print(f"Model: {{MODEL_NAME}} | Provider: {{MODEL_PROVIDER}}")
    print("=" * 50)

    model_config = ModelRequestConfig(model=MODEL_NAME, temperature=0.7, top_p=0.9)
    client_config = ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=60,
        verify_ssl=False,
    )
    react_config = ReActAgentConfig(
        model_config_obj=model_config,
        model_client_config=client_config,
        prompt_template=_build_prompt_template(),
    )
    agent_card = AgentCard(id="{pkg_name}", description="ReAct Agent")
    agent = ReActAgent(card=agent_card).configure(react_config)

    tool = _create_tool()
    Runner.resource_mgr.add_tool(tool)
    agent.ability_manager.add(tool.card)

    started = await Runner.start()
    if not started:
        print("WARNING: Runner.start() returned False")

    app.agent = agent
    print("{pkg_name} (ReAct) 初始化完成!")


@app.query
async def query(msgs, request, cancel_event=None) -> AsyncIterator[Tuple[dict, bool]]:
    """处理查询请求"""
    conversation_id = request.conversation_id

    last_user_msg = None
    for msg in reversed(msgs or []):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    if not last_user_msg:
        yield {{"type": "text", "content": "请输入您的问题"}}, True
        return

    print(f"[query] conversation_id={{conversation_id}}, query={{last_user_msg[:100]}}")
    inputs = {{"query": last_user_msg}}

    try:
        collected_text = []
        stream_iter = await asyncio.to_thread(
            Runner.run_agent_streaming,
            agent=app.agent,
            inputs=inputs,
            session=conversation_id,
        )

        while True:
            if cancel_event and cancel_event.is_set():
                break
            try:
                chunk = await stream_iter.__anext__()
            except StopAsyncIteration:
                break

            if chunk:
                chunk_type = getattr(chunk, "type", "")
                payload = getattr(chunk, "payload", None)

                if chunk_type == "end node stream" and isinstance(payload, dict):
                    delta = payload.get("response") or payload.get("output") or ""
                    if isinstance(delta, dict):
                        delta = str(delta)
                    if delta:
                        collected_text.append(str(delta))
                        yield {{"type": "text_delta", "content": str(delta)}}, False
                elif chunk_type in ("llm_output", "workflow_final") and isinstance(payload, dict):
                    delta = payload.get("content") or payload.get("output") or ""
                    if delta:
                        collected_text.append(str(delta))
                        yield {{"type": "text_delta", "content": str(delta)}}, False
                elif chunk_type == "answer":
                    pass
                elif isinstance(payload, dict):
                    output = payload.get("output") or payload.get("content") or ""
                    if output:
                        collected_text.append(str(output))
                        yield {{"type": "text_delta", "content": str(output)}}, False

        full_text = "".join(collected_text)
        yield {{"type": "result", "content": full_text}}, True

    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[query] error: {{e}}")
        yield {{"type": "error", "content": f"执行失败：{{str(e)}}"}}, True


@app.shutdown
async def shutdown():
    print("{pkg_name} 关闭")


if __name__ == "__main__":
    app.run()
'''


# ==================== workflow 模板 ====================

def _runner_workflow(pkg_name: str) -> str:
    return f'''#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
{pkg_name} - Workflow Agent 模板

运行方式:
    python -m openjiuwen_runtime.{pkg_name} --port 8090

环境变量:
    API_BASE       - 模型 API 地址
    API_KEY        - 模型 API Key
    MODEL_NAME     - 模型名称
    MODEL_PROVIDER - 模型提供商
"""

import asyncio
import os
from typing import AsyncIterator, Tuple

os.environ.setdefault("SSRF_PROTECT_ENABLED", "false")
os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")

from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig, WorkflowAgent
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.workflow import (
    Workflow,
    Start,
    End,
    LLMComponent,
    LLMCompConfig,
)
from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.workflow.workflow_config import WorkflowConfig

from openjiuwen_runtime.service.app.agent_app import AgentApp

# ==================== 配置 ====================
API_BASE = os.getenv("API_BASE", "https://api.siliconflow.cn/v1")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "Qwen/Qwen3-8B")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "siliconflow")


def _create_model_config():
    return ModelRequestConfig(model=MODEL_NAME, temperature=0.7, top_p=0.9)

def _create_client_config():
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=60,
        verify_ssl=False,
    )


def _build_workflow():
    """构建最简工作流: Start -> LLM改写 -> End"""
    workflow_config = WorkflowConfig(
        card=WorkflowCard(
            id="{pkg_name}_workflow",
            name="{pkg_name}",
            version="1.0",
            description="{pkg_name} 工作流",
        )
    )
    flow = Workflow(workflow_config=workflow_config)

    # Start
    start = Start()

    # LLM 组件: 简单改写
    llm_config = LLMCompConfig(
        model_client_config=_create_client_config(),
        model_config=_create_model_config(),
        template_content=[{{"role": "user", "content": "请用一句话回答：{{{{query}}}}"}}],
        response_format={{"type": "text"}},
        output_config={{
            "query": {{"type": "string", "description": "回复内容", "required": True}}
        }},
    )
    llm = LLMComponent(llm_config)

    # End
    end = End({{"responseTemplate": "{{{{output}}}}"}})

    # 注册组件
    flow.set_start_comp("start", start, inputs_schema={{"query": "${{query}}"}})
    flow.add_workflow_comp("llm", llm, inputs_schema={{"query": "${{start.query}}"}})
    flow.set_end_comp("end", end, inputs_schema={{"output": "${{llm.query}}"}})

    # 连接
    flow.add_connection("start", "llm")
    flow.add_connection("llm", "end")

    return flow


# ==================== AgentApp ====================

app = AgentApp(
    app_name="{pkg_name}",
    app_description="Workflow Agent 模板",
    version="0.1.0",
)


@app.init
async def init():
    print("=" * 50)
    print("{pkg_name} (Workflow) 初始化中...")
    print(f"Model: {{MODEL_NAME}} | Provider: {{MODEL_PROVIDER}}")
    print("=" * 50)

    flow = _build_workflow()
    agent_config = WorkflowAgentConfig(
        id="{pkg_name}",
        version="0.1.0",
        description="Workflow Agent 模板",
    )
    agent = WorkflowAgent(agent_config)
    agent.add_workflows([flow])

    app.agent = agent
    print("{pkg_name} (Workflow) 初始化完成!")


@app.query
async def query(msgs, request, cancel_event=None) -> AsyncIterator[Tuple[dict, bool]]:
    """处理查询请求"""
    conversation_id = request.conversation_id

    last_user_msg = None
    for msg in reversed(msgs or []):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    if not last_user_msg:
        yield {{"type": "text", "content": "请输入您的问题"}}, True
        return

    print(f"[query] conversation_id={{conversation_id}}, query={{last_user_msg[:100]}}")
    inputs = {{"query": last_user_msg, "conversation_id": conversation_id}}

    try:
        collected_text = []
        stream_iter = await asyncio.to_thread(
            Runner.run_agent_streaming,
            agent=app.agent,
            inputs=inputs,
            session=conversation_id,
        )

        while True:
            if cancel_event and cancel_event.is_set():
                break
            try:
                chunk = await stream_iter.__anext__()
            except StopAsyncIteration:
                break

            if chunk:
                chunk_type = getattr(chunk, "type", "")
                payload = getattr(chunk, "payload", None)

                if chunk_type == "end node stream" and isinstance(payload, dict):
                    delta = payload.get("response") or payload.get("output") or ""
                    if isinstance(delta, dict):
                        delta = str(delta)
                    if delta:
                        collected_text.append(str(delta))
                        yield {{"type": "text_delta", "content": str(delta)}}, False
                elif chunk_type in ("llm_output", "workflow_final") and isinstance(payload, dict):
                    delta = payload.get("content") or payload.get("output") or ""
                    if delta:
                        collected_text.append(str(delta))
                        yield {{"type": "text_delta", "content": str(delta)}}, False
                elif chunk_type == "answer":
                    pass
                elif chunk_type == "tracer_workflow" and isinstance(payload, dict):
                    if payload.get("status") == "finish" and payload.get("componentType") == "LLMExecutable":
                        outputs = payload.get("outputs", {{}})
                        if isinstance(outputs, dict):
                            delta = outputs.get("query", "")
                            if delta:
                                collected_text.append(str(delta))
                                yield {{"type": "text_delta", "content": str(delta)}}, False
                elif isinstance(payload, dict):
                    output = payload.get("output") or payload.get("content") or ""
                    if output:
                        collected_text.append(str(output))
                        yield {{"type": "text_delta", "content": str(output)}}, False

        full_text = "".join(collected_text)
        yield {{"type": "result", "content": full_text}}, True

    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[query] error: {{e}}")
        yield {{"type": "error", "content": f"执行失败：{{str(e)}}"}}, True


@app.shutdown
async def shutdown():
    print("{pkg_name} 关闭")


if __name__ == "__main__":
    app.run()
'''


# ==================== 辅助 ====================

def _description(template_type: str) -> str:
    return {
        "empty": "空白 Agent 模板",
        "react": "ReAct Agent 模板，带 LLM 和示例工具",
        "workflow": "Workflow Agent 模板，带 LLM 和最简工作流",
    }.get(template_type, "Agent 模板")
