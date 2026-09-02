#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""按 IAM：``agent_template`` + ``instance_agent_resource`` 写入演示数据。

前置：目标实例已在 Manager 创建且 Gateway 可达；Manager 已启动（默认 ``http://127.0.0.1:8765``）。
Agent 资源写入会经 Manager 推送到 Gateway。

执行顺序：

1. 模型模板 M1–M3
2. Embedding 模板 B1–B3
3. 扩展配置模板 E1–E4
4. Skill 白名单模板 W1–W3
5. 服务配置模板 S1–S2
6. Agent 模板 A_VIP / A_SALES / A_FALLBACK（``template_ref`` 仅字面 template_id）
7. 实例 Agent 资源 R_VIP / R_SALES / R_FALLBACK（``match_expr`` 控制谁可用）

典型用法::

    uv run python applications/manager/manager_server/scripts/enterprise_config_demo_data_config.py \\
        b26bc496-dfee-488b-a2ab-8bae8ce94985

可选环境变量 ``CLAWMANAGER_BASE_URL`` 覆盖 Manager 根地址。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

_DEMO_AGENT_SERVER_IMAGE = (
    "swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-amd64:0.0.45k"
)


def _demo_agent_server_base() -> dict[str, Any]:
    return {
        "agent_image": _DEMO_AGENT_SERVER_IMAGE,
        "namespace": "jiuwenclaw",
        "pod_name": "agentserver",
        "container_name": "agent-server",
        "container_port": 18092,
        "port_name": "http1",
        "sse_port": 18092,
        "sse_path": "/api/v1/events/stream",
        "health_path": "/api/v1/health",
        "image_pull_policy": "IfNotPresent",
        "readiness_initial_delay": 5,
        "readiness_period": 5,
        "ready_timeout": 300,
        "ready_poll_interval": 2,
        "min_idle_services": 0,
        "service_concurrency": 2,
        "service_ttl": 300,
        "message_timeout": 600,
        "session_concurrency": 3,
        "session_ttl": 60,
    }


try:
    import httpx
except ImportError as _httpx_import_error:  # pragma: no cover
    httpx = None  # type: ignore[assignment,misc]
else:
    _httpx_import_error = None


def _configure_cli_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(message)s")
    out = logging.StreamHandler(sys.stdout)
    out.setLevel(logging.INFO)
    out.setFormatter(fmt)
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.ERROR)
    err.setFormatter(fmt)
    root.addHandler(out)
    root.addHandler(err)


class ManagerApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, detail: str) -> None:
        super().__init__(f"{method} {path} -> HTTP {status}: {detail}")
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail


class SeedDemoConfigError(RuntimeError):
    """演示种子数据写入前置条件不满足或业务校验失败。"""


class ManagerClient:
    def __init__(self, base_url: str, jiuwenclaw_id: str, *, timeout: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._jid = jiuwenclaw_id.strip()
        self._timeout = timeout
        if not self._jid:
            raise ValueError("jiuwenclaw_id 不能为空")

    @property
    def jiuwenclaw_id(self) -> str:
        return self._jid

    @property
    def base_url(self) -> str:
        return self._base

    def _url(self, path: str) -> str:
        platform_prefixes = (
            "/model-templates",
            "/embedding-templates",
            "/extension-config-templates",
            "/skill-whitelist-templates",
            "/service-config-templates",
            "/agent-templates",
        )
        if path.startswith(platform_prefixes):
            return f"{self._base}/api/v1{path}"
        return f"{self._base}/api/v1/instances/{self._jid}{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._url(path)
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            resp = client.request(method, url, json=json_body)
        if resp.status_code >= 400:
            detail = resp.text.strip()
            try:
                payload = resp.json()
                detail = json.dumps(payload, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
            raise ManagerApiError(method, path, resp.status_code, detail)
        if not resp.content:
            return {}
        data = resp.json()
        if not isinstance(data, dict):
            raise ManagerApiError(method, path, resp.status_code, f"非 JSON 对象: {data!r}")
        code = data.get("code", 200)
        if code not in (200, None):
            raise ManagerApiError(
                method,
                path,
                resp.status_code,
                f"code={code} message={data.get('message')!r}",
            )
        inner = data.get("data")
        if inner is None:
            return {}
        if not isinstance(inner, dict):
            return {"value": inner}
        return inner

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, json_body=body)


def _require_template_id(data: dict[str, Any], label: str) -> str:
    raw = data.get("template_id")
    if raw is None or not str(raw).strip():
        raise ManagerApiError("POST", label, 200, f"响应缺少 template_id: {data!r}")
    return str(raw).strip()


def _require_resource_id(data: dict[str, Any], label: str) -> str:
    items = data.get("items")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            rid = first.get("resource_id")
            if rid is not None and str(rid).strip():
                return str(rid).strip()
    rid = data.get("resource_id")
    if rid is not None and str(rid).strip():
        return str(rid).strip()
    raise ManagerApiError("POST", label, 200, f"响应缺少 resource_id: {data!r}")


def _model_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "M1 兜底-经济型",
            {
                "template_name": "兜底-经济型",
                "description": "Fallback Agent 使用",
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
            "M2 销售组-标准型",
            {
                "template_name": "销售组-标准型",
                "model_type": ["default", "vision"],
                "model_tags": ["chat", "vision"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-sales",
                "model_id": "gpt-4o",
                "model_provider": "openai",
                "enabled": True,
                "data": {},
            },
        ),
        (
            "M3 VIP-加强对话",
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
    ]


def _embedding_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "B1 兜底向量模型",
            {
                "template_name": "兜底向量模型",
                "description": "Fallback Agent 记忆检索",
                "embed_tags": ["memory", "fallback"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-embed-global",
                "model_id": "text-embedding-3-small",
                "model_provider": "openai",
                "parameters": {"encoding_format": "float"},
                "client_config": {"timeout": 60, "retry_count": 3, "verify_ssl": True},
                "enabled": True,
                "data": {"demo": "b1"},
            },
        ),
        (
            "B2 销售组向量模型",
            {
                "template_name": "销售组向量模型",
                "description": "销售 Agent 记忆检索",
                "embed_tags": ["memory", "sales"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-embed-sales",
                "model_id": "text-embedding-3-large",
                "model_provider": "openai",
                "parameters": {"encoding_format": "float", "dimensions": 1536},
                "client_config": {"timeout": 60, "retry_count": 3, "verify_ssl": True},
                "enabled": True,
                "data": {"demo": "b2"},
            },
        ),
        (
            "B3 VIP 向量模型",
            {
                "template_name": "VIP 向量模型",
                "description": "VIP Agent 记忆检索",
                "embed_tags": ["memory", "vip"],
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-demo-embed-vip",
                "model_id": "text-embedding-3-large",
                "model_provider": "openai",
                "parameters": {"encoding_format": "float", "dimensions": 3072},
                "client_config": {"timeout": 60, "retry_count": 3, "verify_ssl": True},
                "enabled": True,
                "data": {"demo": "b3"},
            },
        ),
    ]


def _extension_config_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "E1 Gateway 请求前鉴权",
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
            "E2 Gateway 请求后日志",
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
            "E3 Agent Server 错误恢复",
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
            "E4 Gateway 定时清理",
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


def _skill_whitelist_templates() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "W1 销售组-天气 Skill",
            {
                "template_name": "销售组-天气 Skill",
                "description": "允许 search/weather",
                "skill_id": "search/weather",
                "skill_version": "1.2.0",
                "skill_source": "https://skillhub.example.com/",
                "enabled": True,
                "data": {"demo": "w1"},
            },
        ),
        (
            "W2 销售组-CRM Skill",
            {
                "template_name": "销售组-CRM Skill",
                "description": "允许 crm/lead_lookup",
                "skill_id": "crm/lead_lookup",
                "skill_version": "2.0.1",
                "skill_source": "https://skillhub.example.com/",
                "enabled": True,
                "data": {"demo": "w2"},
            },
        ),
        (
            "W3 兜底 Skill",
            {
                "template_name": "兜底 Skill",
                "description": "Fallback Agent 最小 Skill 白名单",
                "skill_id": "search/weather",
                "skill_version": "1.0.0",
                "skill_source": "https://skillhub.example.com/",
                "enabled": True,
                "data": {"demo": "w3"},
            },
        ),
    ]


def _service_config_templates() -> list[tuple[str, dict[str, Any]]]:
    base = _demo_agent_server_base()
    return [
        (
            "S1 销售组 AgentServer 池",
            {
                **base,
                "template_name": "销售组 AgentServer 池",
                "description": "销售 Agent 使用的 AgentServer 动态池",
                "min_idle_services": 2,
                "service_concurrency": 5,
                "enabled": True,
                "data": {"demo": "s1"},
            },
        ),
        (
            "S2 兜底 AgentServer 池",
            {
                **base,
                "template_name": "兜底 AgentServer 池",
                "description": "Fallback Agent 最小 AgentServer 池",
                "min_idle_services": 1,
                "service_concurrency": 2,
                "enabled": True,
                "data": {"demo": "s2"},
            },
        ),
    ]


def seed_demo_config(client: ManagerClient) -> dict[str, Any]:
    result: dict[str, Any] = {
        "jiuwenclaw_id": client.jiuwenclaw_id,
        "model_templates": {},
        "embedding_templates": {},
        "extension_config_templates": {},
        "skill_whitelist_templates": {},
        "service_config_templates": {},
        "agent_templates": {},
        "agent_resources": {},
    }

    logger.info("[1/7] 创建 model_template（M1–M3）")
    model_ids: list[str] = []
    for label, body in _model_templates():
        row = client.post("/model-templates", body)
        tid = _require_template_id(row, "/model-templates")
        model_ids.append(tid)
        key = f"m{len(model_ids)}"
        result["model_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)
    m1, m2, m3 = model_ids

    logger.info("[2/7] 创建 embedding-templates（B1–B3）")
    embed_ids: list[str] = []
    for label, body in _embedding_templates():
        row = client.post("/embedding-templates", body)
        tid = _require_template_id(row, "/embedding-templates")
        embed_ids.append(tid)
        key = f"b{len(embed_ids)}"
        result["embedding_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)
    b1, b2, b3 = embed_ids

    logger.info("[3/7] 创建 extension-config-templates（E1–E4）")
    extension_ids: list[str] = []
    for label, body in _extension_config_templates():
        row = client.post("/extension-config-templates", body)
        tid = _require_template_id(row, "/extension-config-templates")
        extension_ids.append(tid)
        key = f"e{len(extension_ids)}"
        result["extension_config_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)
    e1, e2, e3, e4 = extension_ids

    logger.info("[4/7] 创建 skill-whitelist-templates（W1–W3）")
    whitelist_ids: list[str] = []
    for label, body in _skill_whitelist_templates():
        row = client.post("/skill-whitelist-templates", body)
        tid = _require_template_id(row, "/skill-whitelist-templates")
        whitelist_ids.append(tid)
        key = f"w{len(whitelist_ids)}"
        result["skill_whitelist_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)
    w1, w2, w3 = whitelist_ids

    logger.info("[5/7] 创建 service-config-templates（S1–S2）")
    service_config_ids: list[str] = []
    for label, body in _service_config_templates():
        row = client.post("/service-config-templates", body)
        tid = _require_template_id(row, "/service-config-templates")
        service_config_ids.append(tid)
        key = f"s{len(service_config_ids)}"
        result["service_config_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)
    s1, s2 = service_config_ids

    logger.info("[6/7] 创建 agent-templates（A_VIP / A_SALES / A_FALLBACK）")
    agent_specs = [
        (
            "a_vip",
            "VIP Agent 模板",
            {
                "template_name": "VIP Agent 模板",
                "description": "alice VIP：M3/B3/W1/E3",
                "agent_tags": ["vip", "demo"],
                "template_ref": {
                    "default_model": [m3],
                    "vision_model": [m3],
                    "video_model": [m1],
                    "audio_model": [m1],
                    "embedding_model": [b3],
                    "skill_whitelist": [w1],
                    "extension_config": [e3],
                },
                "enabled": True,
                "data": {"workspace_dir": "alice"},
            },
        ),
        (
            "a_sales",
            "销售组 Agent 模板",
            {
                "template_name": "销售组 Agent 模板",
                "description": "销售通道：M2/B2/W1+W2/E1+E2",
                "agent_tags": ["sales", "demo"],
                "template_ref": {
                    "default_model": [m2],
                    "vision_model": [m2],
                    "video_model": [m1],
                    "audio_model": [m1],
                    "embedding_model": [b2],
                    "skill_whitelist": [w1, w2],
                    "extension_config": [e1, e2],
                },
                "enabled": True,
                "data": {},
            },
        ),
        (
            "a_fallback",
            "兜底 Agent 模板",
            {
                "template_name": "兜底 Agent 模板",
                "description": "通用兜底：M1/B1/W3/E4",
                "agent_tags": ["fallback", "demo"],
                "template_ref": {
                    "default_model": [m1],
                    "vision_model": [m1],
                    "video_model": [m1],
                    "audio_model": [m1],
                    "embedding_model": [b1],
                    "skill_whitelist": [w3],
                    "extension_config": [e4],
                },
                "enabled": True,
                "data": {},
            },
        ),
    ]
    agent_template_ids: dict[str, str] = {}
    for key, label, body in agent_specs:
        row = client.post("/agent-templates/", body)
        tid = _require_template_id(row, "/agent-templates/")
        agent_template_ids[key] = tid
        result["agent_templates"][key] = tid
        logger.info("  [%s] %s -> template_id=%s", key, label, tid)

    logger.info("[7/7] 创建 instance agent-resources（R_VIP / R_SALES / R_FALLBACK）")
    resource_specs = [
        (
            "r_vip",
            {
                "ref_template_id": agent_template_ids["a_vip"],
                "resource_name": "VIP Agent（alice）",
                "resource_desc": "仅 alice 可用；聊天时 bot_id=本 resource_id",
                "match_exprs": ["user_id in ('alice')"],
                "enabled": True,
                "data": {"demo": "r_vip"},
            },
        ),
        (
            "r_sales",
            {
                "ref_template_id": agent_template_ids["a_sales"],
                "resource_name": "销售组 Agent",
                "resource_desc": "销售组 g_demo_sales 可用",
                "match_exprs": ["group_id in ('g_demo_sales')"],
                "enabled": True,
                "data": {"demo": "r_sales"},
            },
        ),
        (
            "r_fallback",
            {
                "ref_template_id": agent_template_ids["a_fallback"],
                "resource_name": "兜底 Agent",
                "resource_desc": "实例上全员可用（match_expr 空）",
                "match_exprs": [""],
                "enabled": True,
                "data": {"demo": "r_fallback"},
            },
        ),
    ]
    for key, body in resource_specs:
        row = client.post("/agent-resources", body)
        rid = _require_resource_id(row, "/agent-resources")
        result["agent_resources"][key] = rid
        logger.info(
            "  [%s] %s -> resource_id=%s (ref_template=%s)",
            key,
            body["resource_name"],
            rid,
            body["ref_template_id"],
        )

    result["template_id_literals"] = {
        "m1": m1,
        "m2": m2,
        "m3": m3,
        "b1": b1,
        "b2": b2,
        "b3": b3,
        "e1": e1,
        "e2": e2,
        "e3": e3,
        "e4": e4,
        "w1": w1,
        "w2": w2,
        "w3": w3,
        "s1": s1,
        "s2": s2,
    }
    return result


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="按 IAM agent_template / instance_agent_resource 写入演示数据",
    )
    p.add_argument(
        "jiuwenclaw_id",
        help="目标实例 jiuwenclaw_id",
    )
    p.add_argument(
        "--manager-base",
        default=os.environ.get("CLAWMANAGER_BASE_URL", "http://127.0.0.1:8765"),
        help="Manager 根 URL（默认 http://127.0.0.1:8765 或 CLAWMANAGER_BASE_URL）",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="单次 HTTP 请求超时（秒）",
    )
    p.add_argument(
        "--json-out",
        action="store_true",
        help="完成后在 stdout 打印完整结果 JSON",
    )
    return p.parse_args()


def main() -> None:
    _configure_cli_logging()
    if _httpx_import_error is not None:
        logger.error("缺少 httpx，请安装: pip install httpx")
        sys.exit(1)

    args = _parse_args()
    client = ManagerClient(args.manager_base, args.jiuwenclaw_id, timeout=args.timeout)
    logger.info("[seed] jiuwenclaw_id=%s manager=%s", client.jiuwenclaw_id, client.base_url)

    try:
        summary = seed_demo_config(client)
    except SeedDemoConfigError as exc:
        logger.error("[failed] %s", exc)
        raise SystemExit(1) from exc
    except ManagerApiError as exc:
        logger.error("[failed] %s", exc)
        raise SystemExit(1) from exc
    except httpx.ConnectError as exc:
        logger.error("[connect-failed] %s", exc)
        logger.error(
            "请确认 Manager 已在 %s 启动，且实例 %s 已 provision。",
            args.manager_base,
            args.jiuwenclaw_id,
        )
        raise SystemExit(1) from exc

    resources = summary.get("agent_resources") or {}
    r_vip = resources.get("r_vip", "{R_VIP}")
    r_sales = resources.get("r_sales", "{R_SALES}")
    r_fallback = resources.get("r_fallback", "{R_FALLBACK}")

    logger.info("")
    logger.info("[done] 演示配置已写入。聊天时 bot_id 填 resource_id：")
    logger.info("  R_VIP=%s      → M3/B3/W1/E3（VIP 模板）", r_vip)
    logger.info("  R_SALES=%s    → M2/B2/W1+W2/E1+E2/S1（销售模板）", r_sales)
    logger.info("  R_FALLBACK=%s → M1/B1/W3/E4/S2（兜底模板）", r_fallback)
    logger.info("")
    logger.info("AgentServer 聊天联调：")
    logger.info(
        "  uv run python applications/manager/manager_server/scripts/enterprise_config_chat.py "
        "--bot-id %s --user-id alice --group-id g_demo_sales --web-port {WEB_PORT}",
        r_vip,
    )
    logger.info(
        "  uv run python applications/manager/manager_server/scripts/enterprise_config_chat.py "
        "--bot-id %s --user-id bob --group-id g_demo_sales --web-port {WEB_PORT}",
        r_sales,
    )
    logger.info(
        "  uv run python applications/manager/manager_server/scripts/enterprise_config_chat.py "
        "--bot-id %s --user-id bob --group-id g_unknown --web-port {WEB_PORT}",
        r_fallback,
    )

    if args.json_out:
        logger.info("%s", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
