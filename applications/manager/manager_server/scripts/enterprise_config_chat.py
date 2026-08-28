#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""配置完成后，经 Gateway WebChannel 发送一条用户聊天请求。

``bot_id`` = ``instance_agent_resource.resource_id``（由
``enterprise_config_demo_data_config.py`` 输出）。Gateway / AgentServer 按该 id
加载对应 ``agent_template.template_ref`` 中的模型等配置。

典型用法::

    # VIP Agent（seed 打印的 R_VIP）
    uv run python applications/manager/manager_server/scripts/enterprise_config_chat.py \\
        --bot-id {R_VIP} --user-id alice --group-id g_demo_sales \\
        --scenario vip --web-port 19234

    # 销售组 Agent
    uv run python applications/manager/manager_server/scripts/enterprise_config_chat.py \\
        --bot-id {R_SALES} --user-id bob --group-id g_demo_sales \\
        --scenario sales --web-port 19234

    # 兜底 Agent
    uv run python applications/manager/manager_server/scripts/enterprise_config_chat.py \\
        --bot-id {R_FALLBACK} --user-id bob --group-id g_unknown \\
        --scenario fallback --web-port 19234

连接远程 Gateway::

    uv run python .../enterprise_config_chat.py \\
        --bot-id {R_VIP} --scenario vip --ws-url ws://10.0.0.1:19001/ws
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
_stream_logger = logging.getLogger(f"{__name__}.stream")


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

    _stream_logger.handlers.clear()
    _stream_logger.propagate = False
    _stream_logger.setLevel(logging.INFO)
    stream_out = logging.StreamHandler(sys.stdout)
    stream_out.setLevel(logging.INFO)
    stream_out.setFormatter(fmt)
    stream_out.terminator = ""
    _stream_logger.addHandler(stream_out)


def _write_stream(text: str) -> None:
    _stream_logger.info(text)


_MODEL_SLOT_KEYS = ("default_model", "vision_model", "video_model", "audio_model")

_DEMO_SCENARIO_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "vip": {
        "model_slots": {
            "default_model": "M3 VIP-加强对话 (gpt-5)",
            "vision_model": "M3 VIP-加强对话 (gpt-5)",
            "video_model": "M1 兜底-经济型 (gpt-4o-mini)",
            "audio_model": "M1 兜底-经济型 (gpt-4o-mini)",
        },
        "embedding_model": "B3 VIP 向量模型",
        "skill_whitelist": "W1 销售组-天气 Skill",
        "extension_config": "E3 Agent Server 错误恢复",
        "note": "bot_id=R_VIP → agent_template A_VIP.template_ref（仅字面 template_id）",
    },
    "sales": {
        "model_slots": {
            "default_model": "M2 销售组-标准型 (gpt-4o)",
            "vision_model": "M2 销售组-标准型 (gpt-4o)",
            "video_model": "M1 兜底-经济型 (gpt-4o-mini)",
            "audio_model": "M1 兜底-经济型 (gpt-4o-mini)",
        },
        "embedding_model": "B2 销售组向量模型",
        "skill_whitelist": "W1 + W2",
        "extension_config": "E1 + E2",
        "note": "bot_id=R_SALES → agent_template A_SALES.template_ref",
    },
    "fallback": {
        "model_slots": {
            "default_model": "M1 兜底-经济型 (gpt-4o-mini)",
            "vision_model": "M1 兜底-经济型 (gpt-4o-mini)",
            "video_model": "M1 兜底-经济型 (gpt-4o-mini)",
            "audio_model": "M1 兜底-经济型 (gpt-4o-mini)",
        },
        "embedding_model": "B1 兜底向量模型",
        "skill_whitelist": "W3 兜底 Skill",
        "extension_config": "E4 Gateway 定时清理",
        "note": "bot_id=R_FALLBACK → agent_template A_FALLBACK.template_ref",
    },
}


def _log_demo_expectation(scenario: str) -> None:
    hint = _DEMO_SCENARIO_EXPECTATIONS.get(scenario)
    if hint is None:
        return
    slots = hint.get("model_slots") or {}
    slot_parts = [f"{k}={slots.get(k, '未配置')}" for k in _MODEL_SLOT_KEYS]
    logger.info("[expect] 演示 seed 预期模型槽位: %s", "; ".join(slot_parts))
    if hint.get("note"):
        logger.info("[expect] 说明: %s", hint["note"])
    logger.info(
        "[expect] embedding_model=%s; skill_whitelist=%s; extension_config=%s",
        hint.get("embedding_model"),
        hint["skill_whitelist"],
        hint["extension_config"],
    )
    logger.info(
        "[expect] 可在 AgentServer 日志中查找 "
        "[enterprise_config] loaded enterprise config by resource_id 确认各槽位"
    )


def _load_web_port_from_provision(path: Path) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw.get("data", raw)
    ports = data.get("ports") if isinstance(data, dict) else None
    if not isinstance(ports, dict):
        raise ValueError(f"无法在 {path} 中找到 data.ports")
    web = ports.get("web")
    if web is None:
        raise ValueError(f"无法在 {path} 中找到 data.ports.web")
    return int(web)


def _resolve_ws_url(args: argparse.Namespace) -> str:
    if args.ws_url:
        url = str(args.ws_url).strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("ws", "wss"):
            raise ValueError(f"--ws-url 须为 ws:// 或 wss://，当前 scheme={parsed.scheme!r}")
        if not parsed.netloc:
            raise ValueError(f"--ws-url 无效（缺少 host）: {url!r}")
        return url
    if args.provision_json is not None:
        web_port = _load_web_port_from_provision(args.provision_json)
    else:
        web_port = int(args.web_port)
    return f"ws://{args.host}:{web_port}{args.ws_path}"


def _browser_origin_header(ws_url: str) -> dict[str, str]:
    parsed = urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    http_scheme = "https" if parsed.scheme == "wss" else "http"
    port = parsed.port
    default_port = 443 if http_scheme == "https" else 80
    if port is not None and port != default_port:
        origin = f"{http_scheme}://{host}:{port}"
    else:
        origin = f"{http_scheme}://{host}"
    return {"Origin": origin}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="经 Gateway /ws 发送 chat.send（bot_id=instance_agent_resource.resource_id）"
    )
    p.add_argument("--host", default="127.0.0.1", help="Gateway 主机，默认 127.0.0.1")
    p.add_argument("--ws-path", default="/ws", help="WebSocket 路径，默认 /ws")
    p.add_argument("--session-id", default="", help="会话 ID；留空则自动生成")
    p.add_argument(
        "--content",
        default="你好，请用一句话回复，并说明你当前使用的模型名称。",
        help="用户消息正文",
    )
    p.add_argument(
        "--group-id",
        default="g_demo_sales",
        help="请求上下文 group_id（授权 match_expr 用；配置加载不依赖）",
    )
    p.add_argument(
        "--bot-id",
        required=True,
        help="instance_agent_resource.resource_id（seed 脚本输出的 R_*）",
    )
    p.add_argument("--user-id", default="alice", help="请求上下文 user_id")
    p.add_argument(
        "--scenario",
        choices=sorted(_DEMO_SCENARIO_EXPECTATIONS),
        default=None,
        help="打印演示预期：vip / sales / fallback",
    )
    p.add_argument("--mode", default="agent.plan", help="运行模式，如 agent.plan")
    p.add_argument("--timeout", type=float, default=180.0, help="等待 chat.final 超时（秒）")
    p.add_argument("--print-deltas", action="store_true", help="打印 chat.delta 流式片段")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--web-port", type=int, help="Gateway WebChannel 端口")
    src.add_argument(
        "--provision-json",
        type=Path,
        help="provision-local 响应 JSON（读取 data.ports.web）",
    )
    src.add_argument("--ws-url", help="完整 WebSocket URL，如 ws://host:19001/ws")
    return p.parse_args()


async def _recv_json(ws: Any, timeout: float) -> dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError(f"非 JSON 对象: {raw!r}")
    return data


async def _run_chat(args: argparse.Namespace) -> int:
    import websockets

    ws_url = _resolve_ws_url(args)

    session_id = (args.session_id or "").strip() or f"sess_{uuid.uuid4().hex[:12]}"
    req_id = f"req_{uuid.uuid4().hex[:12]}"

    params: dict[str, Any] = {
        "session_id": session_id,
        "content": args.content,
        "query": args.content,
        "mode": args.mode,
        "group_id": args.group_id,
        "bot_id": args.bot_id,
        "user_id": args.user_id,
    }

    req = {
        "type": "req",
        "id": req_id,
        "method": "chat.send",
        "params": params,
    }

    logger.info("[connect] %s", ws_url)
    logger.info(
        "[send] session_id=%s group_id=%s bot_id(resource_id)=%s user_id=%s",
        session_id,
        args.group_id,
        args.bot_id,
        args.user_id,
    )
    if args.scenario:
        _log_demo_expectation(args.scenario)
    logger.info("[send] content=%r", args.content)

    deadline = asyncio.get_running_loop().time() + args.timeout

    ws_headers = _browser_origin_header(ws_url)
    async with websockets.connect(
        ws_url,
        open_timeout=15,
        additional_headers=ws_headers,
    ) as ws:
        await ws.send(json.dumps(req, ensure_ascii=False))

        accepted = False
        while asyncio.get_running_loop().time() < deadline:
            remaining = max(0.1, deadline - asyncio.get_running_loop().time())
            try:
                frame = await _recv_json(ws, remaining)
            except asyncio.TimeoutError:
                logger.error("[timeout] 未在时限内收到 chat.final")
                return 2

            ftype = frame.get("type")

            if ftype == "res" and frame.get("id") == req_id:
                ok = bool(frame.get("ok"))
                payload = frame.get("payload") or {}
                logger.info(
                    "[res] ok=%s payload=%s",
                    ok,
                    json.dumps(payload, ensure_ascii=False),
                )
                if not ok:
                    err = frame.get("error") or payload.get("error") or frame
                    logger.error("[error] %s", err)
                    return 1
                accepted = bool(payload.get("accepted", True))
                if not accepted:
                    logger.error("[error] chat.send 未被接受")
                    return 1
                continue

            if ftype == "event":
                event = frame.get("event")
                payload = frame.get("payload") or {}
                if event == "connection.ack":
                    logger.info("[event] connection.ack")
                    continue
                if event == "chat.delta" and args.print_deltas:
                    chunk = payload.get("content") or payload.get("text") or ""
                    if chunk:
                        _write_stream(chunk)
                    continue
                if event == "chat.final":
                    if args.print_deltas:
                        _write_stream("\n")
                    final_text = str(payload.get("content") or payload.get("text") or "")
                    logger.info(
                        "[event] chat.final session_id=%s",
                        payload.get("session_id", session_id),
                    )
                    logger.info("%s", final_text or "(empty)")
                    return 0
                if event == "chat.error":
                    logger.error(
                        "[event] chat.error %s",
                        json.dumps(payload, ensure_ascii=False),
                    )
                    return 1
                logger.info(
                    "[event] %s %s",
                    event,
                    json.dumps(payload, ensure_ascii=False)[:200],
                )
                continue

            logger.info("[frame] %s", json.dumps(frame, ensure_ascii=False)[:500])

        if not accepted:
            logger.error("[timeout] 未收到 chat.send 的 res 确认")
            return 2
        logger.error("[timeout] 已接受请求但未收到 chat.final")
        return 2


def main() -> int:
    _configure_cli_logging()
    try:
        import websockets  # noqa: F401
    except ImportError:
        logger.error("缺少 websockets，请执行: pip install websockets")
        return 1

    args = _parse_args()
    try:
        return asyncio.run(_run_chat(args))
    except KeyboardInterrupt:
        return 130
    except OSError as connect_err:
        logger.error("[connect-failed] %s", connect_err)
        logger.error(
            "请确认 Gateway 已启动，且 --web-port / --provision-json / --ws-url 指向可访问的 WebChannel。"
        )
        return 1
    except Exception as err:
        logger.error("[failed] %s", err)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
