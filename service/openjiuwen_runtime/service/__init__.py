# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
openjiuwen-runtime-sdk

agent-studio 的 Agent 和 Plugin 运行时 SDK。

导出:
    AgentApp: 用于部署 Agent 的应用类
    PluginApp: 用于部署工具的应用类
    BaseApp: 所有应用的基类
    AppGroup: 多应用容器，共享端口
    QueryRequest: Agent 查询的请求模型
    ResetConversationRequest: 重置对话的请求模型

示例 - AgentApp:
    from openjiuwen_runtime.sdk import AgentApp

    app = AgentApp("MyAgent")

    @app.init
    async def init():
        app.agent = MyAgent()

    @app.query
    async def query(msgs, request):
        async for msg, last in app.agent.stream(...):
            yield msg, last

    app.run(port=8090)

示例 - PluginApp:
    from openjiuwen_runtime.sdk import PluginApp

    app = PluginApp("CalculatorTools")

    @app.restful.tool(name="add", description="加法运算")
    async def add(a: float, b: float) -> dict:
        return {"result": a + b}

    app.run(port=8091)

示例 - AppGroup:
    from openjiuwen_runtime.sdk import AgentApp, AppGroup

    customer_app = AgentApp("CustomerAgent")
    assistant_app = AgentApp("AssistantAgent")

    group = AppGroup("MultiAgentGroup")
    group.mount("/customer", customer_app)
    group.mount("/assistant", assistant_app)
    group.run(port=8090)
"""

from .app import BaseApp, AgentApp, PluginApp, AppGroup
from .app import Middleware, MiddlewareContext, LoggingMiddleware
from .models import QueryRequest, ResetConversationRequest

__all__ = ["AgentApp", "BaseApp", "PluginApp", "AppGroup", "QueryRequest", "ResetConversationRequest",
           "Middleware", "MiddlewareContext", "LoggingMiddleware"]

__version__ = "0.2.0"
