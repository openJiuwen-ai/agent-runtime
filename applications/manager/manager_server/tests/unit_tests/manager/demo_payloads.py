# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""演示数据 HTTP 请求体（与 enterprise_config_demo_data_config 对齐）。"""

from __future__ import annotations

from typing import Any

_DEMO_AGENT_IMAGE = "jiuwenclaw/agent-server:latest"
_DEMO_MAIN_CID = "c-agentserver"


def _demo_main_container(*, image: str = _DEMO_AGENT_IMAGE, port: int = 8080) -> dict[str, Any]:
    return {
        "container_id": _DEMO_MAIN_CID,
        "name": "agent-server",
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "ports": [{"name": "sse", "containerPort": port}],
        "readinessProbe": {
            "httpGet": {"path": "/api/v1/health", "port": port},
            "initialDelaySeconds": 5,
            "periodSeconds": 5,
        },
    }


def demo_agent_server_base() -> dict[str, Any]:
    return {
        "namespace": "jiuwenclaw",
        "pod_name": "agentserver",
        "sse_path": "/api/v1/events/stream",
        "ready_timeout": 300,
        "ready_poll_interval": 2,
        "main_container_id": _DEMO_MAIN_CID,
        "sidecar_container_ids": [],
        "min_idle_pods": 0,
        "pod_concurrency": 2,
        "pod_ttl": 300,
        "message_timeout": 600,
        "scope_concurrency": 3,
        "session_ttl": 60,
        "data": {
            "config_sync": {
                "containers": [_demo_main_container()],
            }
        },
    }


def model_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "M1",
            {
                "template_name": "全局兜底-经济型",
                "description": "无服务/Agent 命中时使用",
                "model_type": ["default"],
                "model_tags": ["chat"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-global",
                "model_id": "gpt-4o-mini",
                "model_provider": "openai",
                "parameters": {"temperature": 0.7, "max_tokens": 4096},
                "enabled": True,
                "data": {},
            },
        ),
        (
            "M2",
            {
                "template_name": "销售组-标准型",
                "model_type": ["default"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-sales",
                "model_id": "gpt-4o",
                "model_provider": "openai",
                "enabled": True,
                "data": {},
            },
        ),
        (
            "M3",
            {
                "template_name": "VIP-加强对话",
                "model_type": ["default", "vision"],
                "model_tags": ["chat", "vision"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-vip",
                "model_id": "gpt-5",
                "model_provider": "openai",
                "enabled": True,
                "data": {},
            },
        ),
        (
            "M4",
            {
                "template_name": "Carol 默认映射模型",
                "model_type": ["default"],
                "api_base": "https://api.deepseek.com/v1",
                "api_key": "sk-demo-carol",
                "model_id": "deepseek-v3",
                "model_provider": "deepseek",
                "enabled": True,
                "data": {},
            },
        ),
        (
            "M5",
            {
                "template_name": "销售组映射专用",
                "model_type": ["default"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-group-map",
                "model_id": "gpt-4o-group-map",
                "model_provider": "openai",
                "enabled": True,
                "data": {},
            },
        ),
    ]


def extension_config_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "E1",
            {
                "template_name": "Gateway 请求前鉴权",
                "description": "请求前参数校验与权限检查（gateway）",
                "component": "gateway",
                "hook_type": "pre_request",
                "hook_config": {
                    "handler": "hooks.auth.pre_request",
                    "params": {"require_token": True, "allowed_roles": ["user", "admin"]},
                },
                "custom_config": {"auth_header": "Authorization"},
                "enabled": True,
                "data": {"demo": "e1"},
            },
        ),
        (
            "E2",
            {
                "template_name": "Gateway 请求后日志",
                "description": "请求完成后记录访问日志（gateway）",
                "component": "gateway",
                "hook_type": "post_request",
                "hook_config": {
                    "handler": "hooks.logging.post_request",
                    "params": {"log_level": "info", "include_body": False},
                },
                "custom_config": {},
                "enabled": True,
                "data": {"demo": "e2"},
            },
        ),
        (
            "E3",
            {
                "template_name": "Agent Server 错误恢复",
                "description": "请求失败时告警与降级（agent_server）",
                "component": "agent_server",
                "hook_type": "error",
                "hook_config": {
                    "handler": "hooks.recovery.on_error",
                    "params": {"notify_channel": "demo-alerts", "max_retries": 1},
                },
                "custom_config": {"fallback_message": "服务暂时不可用，请稍后重试"},
                "enabled": True,
                "data": {"demo": "e3"},
            },
        ),
        (
            "E4",
            {
                "template_name": "Gateway 定时清理",
                "description": "定时清理临时缓存与会话残留（gateway）",
                "component": "gateway",
                "hook_type": "schedule",
                "hook_config": {
                    "handler": "hooks.maintenance.cleanup",
                    "schedule": "0 */5 * * *",
                    "params": {"ttl_seconds": 3600},
                },
                "custom_config": {"workspace": "demo"},
                "enabled": True,
                "data": {"demo": "e4"},
            },
        ),
    ]


def skill_prebuilt_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "W1",
            {
                "template_name": "销售组-天气 Skill",
                "description": "销售通道允许 search/weather",
                "skill_id": "search/weather",
                "package_url": "https://artifacts.example.com/skills/weather-1.2.0.zip",
                "enabled": True,
                "data": {"demo": "w1"},
            },
        ),
        (
            "W2",
            {
                "template_name": "销售组-CRM Skill",
                "description": "销售通道允许 crm/lead_lookup",
                "skill_id": "crm/lead_lookup",
                "package_url": "https://artifacts.example.com/skills/crm-2.0.1.zip",
                "enabled": True,
                "data": {"demo": "w2"},
            },
        ),
        (
            "W3",
            {
                "template_name": "全局兜底 Skill",
                "description": "未命中服务策略时的最小预置 Skill",
                "skill_id": "search/weather",
                "package_url": "https://artifacts.example.com/skills/weather-1.0.0.zip",
                "enabled": True,
                "data": {"demo": "w3"},
            },
        ),
        (
            "W4",
            {
                "template_name": "SPI 预置 Skill",
                "description": "provider 路径示例",
                "skill_id": "crm/lead_lookup",
                "source_id": "skillhub",
                "version_id": "2.0.1",
                "enabled": True,
                "data": {"demo": "w4"},
            },
        ),
    ]



def mcp_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "M1",
            {
                "template_name": "Demo MCP Server",
                "description": "HTTP MCP for demo tools",
                "mcp_entry": {
                    "name": "demo-tools",
                    "transport": "http",
                    "url": "http://127.0.0.1:9000/mcp",
                },
                "enabled": True,
                "data": {"demo": "m1"},
            },
        ),
    ]


def service_config_templates() -> list[tuple[str, dict[str, Any]]]:
    base = demo_agent_server_base()
    return [
        (
            "S1",
            {
                **base,
                "template_name": "销售组 AgentServer 池",
                "description": "销售通道 g_demo_sales 使用的 AgentServer 动态池",
                "min_idle_pods": 2,
                "pod_concurrency": 5,
                "enabled": True,
                "data": {
                    "demo": "s1",
                    "config_sync": {
                        "containers": [_demo_main_container()],
                    },
                },
            },
        ),
        (
            "S2",
            {
                **base,
                "template_name": "全局兜底 AgentServer 池",
                "description": "未命中服务策略时的最小 AgentServer 池",
                "min_idle_pods": 1,
                "pod_concurrency": 2,
                "enabled": True,
                "data": {
                    "demo": "s2",
                    "config_sync": {
                        "containers": [_demo_main_container()],
                    },
                },
            },
        ),
    ]


def container_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "C1",
            {
                "template_name": "默认 AgentServer 容器",
                "description": "主容器模板（含 name=sse 端口）",
                "container_id": "c-agentserver",
                "name": "agent-server",
                "image": _DEMO_AGENT_IMAGE,
                "image_pull_policy": "IfNotPresent",
                "ports": [{"name": "sse", "containerPort": 8080}],
                "readiness_probe": {
                    "httpGet": {"path": "/api/v1/health", "port": 8080},
                    "initialDelaySeconds": 5,
                    "periodSeconds": 5,
                },
                "enabled": True,
                "data": {},
            },
        ),
        (
            "C2",
            {
                "template_name": "默认 Sandbox 容器",
                "description": "Sidecar 容器模板",
                "container_id": "c-jiuwenbox",
                "name": "jiuwenbox",
                "image": "jiuwenclaw/jiuwenbox:latest",
                "image_pull_policy": "IfNotPresent",
                "ports": [{"containerPort": 8321}],
                "enabled": True,
                "data": {},
            },
        ),
    ]


def instance_create_body(
    *,
    jiuwenclaw_name: str = "ut-demo-instance",
    gateway_host: str = "http://127.0.0.1:18080",
    runtime_host: str = "http://127.0.0.1:18081",
    user_web_host: str = "http://127.0.0.1:15173",
    gateway_web_http_host: str = "http://127.0.0.1:19002",
    gateway_web_ws_host: str = "http://127.0.0.1:19000",
) -> dict[str, Any]:
    return {
        "jiuwenclaw_name": jiuwenclaw_name,
        "created_by": "ut-tester",
        "description": "manager API unit test instance",
        "namespace": "default",
        "space_id": "default",
        "gateway_host": gateway_host,
        "runtime_host": runtime_host,
        "user_web_host": user_web_host,
        "gateway_web_http_host": gateway_web_http_host,
        "gateway_web_ws_host": gateway_web_ws_host,
    }
