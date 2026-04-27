# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
#
# 真实 K8s 部署 + WebSocket 的 Access 可执行入口（与 test_session_sdk 同目录）。
# 需本机可用 kubectl/ kubeconfig 或 in-cluster 配置；与 ``kubectl get pods -n <ns>`` 使用同一套权限。
#
# 命令参考（在 **management** 包根目录，已 `uv sync` 安装依赖后）::
#
#   uv run python tests/system_tests/management_session/main_k8s_access.py ^
#     --image your-registry/svc:tag ^
#     --namespace default ^
#     --name-prefix jiuwenclaw ^
#     --container-port 18092 ^
#     --ws-tls 0 ^
#   需要 ``/invoke`` 路径时再加:  --invoke-path /invoke
#     --min-idle 1 --max-services 2 --service-concurrency 10 ^
#     --message-json "{\"text\":\"hi\",\"request_id\":\"r1\",\"chat_id\":\"c1\",\"bot_id\":\"b1\"}"
#
# 或从文件读入 JSON，并用 CLI 覆盖部分字段::
#
#   uv run python tests/system_tests/management_session/main_k8s_access.py ^
#     --image your-registry/svc:tag --message-file ./payload.json ^
#     --chat-id c1 --bot-id b1
#
# 若未用 `uv` 而只用源码，需在仓库根为 **management** 与 **foundation** 的 `openjiuwen_runtime` 设好
# `PYTHONPATH`（或安装可编辑包），与运行 pytest 时一致。
#
# 说明：``--kubeconfig`` 可指向 kubeconfig 文件；缺省与 `kubectl` 相同（`~/.kube/config` 或 in-cluster）。部署后 Pod
# 会保留在集群中；本脚本不自动级联删除，可用 ``kubectl get pods -n <ns>`` 查看，手动 ``kubectl delete`` 清理。

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, List, Optional

from openjiuwen_runtime.management.session.access import Access
from openjiuwen_runtime.management.session.dual_queue import PriorityDualAsyncQueues
from openjiuwen_runtime.management.session.interfaces import (
    IResponseParser,
    IServiceInstanceFactory,
    IServiceHandler,
    IRequest,
)
from openjiuwen_runtime.management.session.k8s_service_handler import (
    K8sDeployController,
    K8sServiceHandler,
)
from openjiuwen_runtime.management.session.models import AccessConfig, SessionConfig
from openjiuwen_runtime.management.session.service_handler import ServiceHandler
from openjiuwen_runtime.management.session.service_manager import (
    ServiceManager,
    QueueItem,
)
from openjiuwen_runtime.management.session.strategies.per_chat_bot import (
    PerChatBotStrategy,
)
from openjiuwen_runtime.management.session.timer import Timer
from openjiuwen_runtime.management.session.ws_client_channel import WSServiceMessageChannel


def _e2a_nested_is_complete(data: dict[str, Any]) -> bool:
    """``provenance.details.is_complete``（jiuwenclaw 网关对 agent chunk 的归一化）。"""
    prov = data.get("provenance")
    if not isinstance(prov, dict):
        return False
    det = prov.get("details")
    if not isinstance(det, dict):
        return False
    return det.get("is_complete") is True


class E2aEnvelopResponseParser(IResponseParser):
    """解析 e2a / jiuwenclaw 网关归一化后的 WebSocket 下行 JSON。

    典型终态一帧（节选）::

        {
            "protocol_version": "1.0",
            "request_id": "req_xxx",
            "response_id": "req_xxx",
            "is_final": true,
            "status": "succeeded",
            "response_kind": "e2a.complete",
            "provenance": {"details": {"is_complete": true, ...}},
            "body": {"result": {}},
            ...
        }

    * ``request_id``：与上行多路复用键一致，优先取 ``request_id``，否则 ``response_id``/``id``。
    * **终态**（``is_completed`` 为真）：任一为真即可——``is_final``、``provenance.details.is_complete``、
      历史兼容字段（``error``/``done``/``is_end``/``event`` 等）。
    * ``response``：返回**整条原始 dict**（不剥 ``body``），便于业务自行读 ``body.result`` 等。
    """

    _END_EVENTS = {"stream.end", "stream.done", "chat.done", "response.end"}
    _TERMINAL_STATUS = {"succeeded", "failed", "canceled", "cancelled", "error"}

    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        rid = data.get("request_id") or data.get("response_id") or data.get("id")
        return str(rid) if rid is not None else None

    def is_completed(self, data: dict[str, Any]) -> bool:
        if data.get("is_final") is True:
            return True
        if _e2a_nested_is_complete(data):
            return True
        st = data.get("status")
        if isinstance(st, str) and st in self._TERMINAL_STATUS and st != "succeeded":
            return True
        if "error_code" in data or "error" in data:
            return True
        if data.get("completed") is True:
            return True
        if data.get("done") is True or data.get("is_end") is True:
            return True
        ev = data.get("event")
        if isinstance(ev, str) and ev in self._END_EVENTS:
            return True
        rk = data.get("response_kind")
        if isinstance(rk, str) and rk.endswith(".complete"):
            return True
        return False

    def response(self, data: dict[str, Any]) -> Any:
        return data


# 供 Access / ServiceManager 等保持旧名称引用
DictStreamParser = E2aEnvelopResponseParser


class WireIRequest(IRequest):
    """``IRequest`` 的具体实现：原始 JSON 作为 ``wire_dict`` 整体上行（WebSocket 帧）。

    适配的 JSON 形态（与 jiuwenclaw-agentserver 兼容，所有字段均为可选，按需取用）::

        {
            "request_id":  "req_xxx",                 # 必需，多路复用键
            "chat_id":     "default_chat",            # 影响 PerChatBotStrategy 会话亲和
            "bot_id":      "default_bot",             # 同上
            "user_id":     "default_user",
            "session_id":  "sess_xxx",                # 透传业务 session
            "channel_id":  "web",
            "req_method":  "chat.send",               # JSON-RPC 风格方法名
            "params": {                               # 请求参数对象
                "session_id": "sess_xxx",
                "content":    "...",
                "mode":       "agent",
                "query":      "..."
            },
            "is_stream":   true,
            "timestamp":   1774319466.4684224,
            "metadata":    {"query": {}, "method": "chat.send"}
        }

    :class:`Access` 解析的标准 ``IRequest`` 字段（``request_id`` 等）来自顶层；其余结构不动，
    全部经 ``wire_dict`` 透传给 Pod 业务方。
    """

    def __init__(self, d: dict[str, Any]) -> None:
        if not isinstance(d, dict):
            raise TypeError(f"WireIRequest 需要 dict 顶层 JSON，收到 {type(d).__name__}")
        self.wire_dict: dict[str, Any] = d

    def _opt(self, key: str) -> Optional[str]:
        v = self.wire_dict.get(key)
        return str(v) if v is not None else None

    @property
    def request_id(self) -> Optional[str]:
        return self._opt("request_id")

    @property
    def chat_id(self) -> Optional[str]:
        return self._opt("chat_id")

    @property
    def user_id(self) -> Optional[str]:
        return self._opt("user_id")

    @property
    def bot_id(self) -> Optional[str]:
        return self._opt("bot_id")

    @property
    def session_id(self) -> Optional[str]:
        return self._opt("session_id")


def _parse_env(pairs: List[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"无效 --env 格式（需 KEY=val）: {p!r}")
        k, _, v = p.partition("=")
        out[k] = v
    return out


def _load_message_payload(ns: argparse.Namespace) -> dict[str, Any]:
    if ns.message_file and ns.message_json:
        raise SystemExit("请只指定其一：--message-file 或 --message-json")
    if ns.message_file:
        with open(ns.message_file, encoding="utf-8") as f:
            raw = f.read()
    elif ns.message_json is not None:
        raw = ns.message_json
    else:
        raw = json.dumps(
            {
                "request_id": "req_demo_1",
                "chat_id": "default_chat",
                "bot_id": "default_bot",
                "user_id": "default_user",
                "session_id": "sess_demo",
                "channel_id": "web",
                "req_method": "chat.send",
                "params": {
                    "session_id": "sess_demo",
                    "content": "你好",
                    "mode": "agent",
                    "query": "你好",
                },
                "is_stream": True,
                "metadata": {"query": {}, "method": "chat.send"},
            },
            ensure_ascii=False,
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"消息体不是合法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise SystemExit("消息 JSON 必须是对象（顶层为 dict）")
    if ns.request_id is not None:
        data["request_id"] = ns.request_id
    if ns.chat_id is not None:
        data["chat_id"] = ns.chat_id
    if ns.bot_id is not None:
        data["bot_id"] = ns.bot_id
    if ns.user_id is not None:
        data["user_id"] = ns.user_id
    if ns.session_id is not None:
        data["session_id"] = ns.session_id
    return data


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Access + 真实 K8s Pod 部署 + WebSocket 发消息"
    )
    p.add_argument("--kubeconfig", default=None, help="kubeconfig 路径，缺省与 kubectl 一致")
    p.add_argument("--namespace", default="default", help="K8s 命名空间")
    p.add_argument("--image", required=True, help="工作负载镜像")
    p.add_argument("--name-prefix", default="jiuwenclaw", help="Pod 名前缀")
    p.add_argument("--container-name", default="jiuwenclaw-agentserver")
    p.add_argument("--container-port", type=int, default=18092, help="容器暴露端口")
    p.add_argument("--port-name", default="http1")
    p.add_argument(
        "--image-pull-policy", default="IfNotPresent", choices=["Always", "IfNotPresent", "Never"]
    )
    p.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="K=V",
        help="容器环境变量，可多次指定",
    )
    p.add_argument("--readiness-initial-delay", type=int, default=5)
    p.add_argument("--readiness-period", type=int, default=10)
    p.add_argument("--ready-timeout", type=float, default=300.0)
    p.add_argument("--ready-poll", type=float, default=2.0)

    p.add_argument("--user-queue", type=int, default=1000)
    p.add_argument("--system-queue", type=int, default=100)
    p.add_argument("--service-concurrency", type=int, default=10)
    p.add_argument("--min-idle", type=int, default=1)
    p.add_argument("--max-services", type=int, default=3)
    p.add_argument(
        "--target-port",
        type=int,
        default=None,
        help="WSS 连接端口；缺省与 --container-port 相同",
    )
    p.add_argument(
        "--invoke-path",
        default="",
        help="WebSocket 路径，如 /invoke；缺省为空，不拼在 host:port 后",
    )
    p.add_argument(
        "--ws-tls",
        type=int,
        default=0,
        choices=(0, 1),
        help="1 使用 wss://，0 使用 ws://（直连 pod_ip 时一般为 0）",
    )
    p.add_argument("--service-ttl", type=int, default=30)
    p.add_argument("--message-timeout", type=int, default=30)
    p.add_argument("--autoscale-interval", type=float, default=0.2)
    p.add_argument(
        "--session-concurrency", type=int, default=1, help="同一会话内最大并行处理请求数"
    )
    p.add_argument("--session-ttl", type=int, default=0, help="0 表示不启用 session TTL 计时器")

    p.add_argument(
        "--message-json",
        default=None,
        help="内联 JSON 消息（对象）",
    )
    p.add_argument(
        "--message-file",
        default=None,
        help="从文件读取 JSON 消息",
    )
    p.add_argument("--request-id", default=None)
    p.add_argument("--chat-id", default=None)
    p.add_argument("--bot-id", default=None)
    p.add_argument("--user-id", default=None)
    p.add_argument("--session-id", default=None)
    return p


async def _amain() -> int:
    args = _build_parser().parse_args()
    target_port = args.target_port if args.target_port is not None else args.container_port
    if target_port != args.container_port:
        print(
            "注意: --target-port 与 --container-port 不一致时，"
            "请确保 Pod 内进程实际监听的地址与 WSS 连接一致。",
            file=sys.stderr,
        )

    env = _parse_env(args.env)
    acc_cfg = AccessConfig(
        user_queue_size=args.user_queue,
        system_queue_size=args.system_queue,
        image=args.image,
        service_concurrency=args.service_concurrency,
        min_idle_services=args.min_idle,
        max_services=args.max_services,
        target_port=target_port,
        invoke_path=args.invoke_path,
        ws_use_tls=bool(args.ws_tls),
        service_ttl=args.service_ttl,
        message_timeout=args.message_timeout,
        autoscale_interval=args.autoscale_interval,
    )
    session_cfg = SessionConfig(
        concurrency=args.session_concurrency,
        ttl=args.session_ttl,
    )

    class _Factory(IServiceInstanceFactory):
        async def new_service(
                self, response_parser: IResponseParser
        ) -> IServiceHandler:
            k8s = K8sServiceHandler(
                args.image,
                name_prefix=args.name_prefix,
                namespace=args.namespace,
                container_name=args.container_name,
                container_port=args.container_port,
                port_name=args.port_name,
                image_pull_policy=args.image_pull_policy,
                env_vars=env,
                kubeconfig=args.kubeconfig,
                readiness_initial_delay=args.readiness_initial_delay,
                readiness_period=args.readiness_period,
                ready_timeout=args.ready_timeout,
                ready_poll_interval=args.ready_poll,
            )
            ch = WSServiceMessageChannel(
                target_port=acc_cfg.target_port,
                invoke_path=acc_cfg.invoke_path,
                ws_use_tls=acc_cfg.ws_use_tls,
            )
            return ServiceHandler(
                total_concurrency=acc_cfg.service_concurrency,
                message_channel=ch,
                response_parser=response_parser,
                deploy_controller=K8sDeployController(k8s),
            )

    factory = _Factory()
    dual_q: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(
        acc_cfg.user_queue_size, acc_cfg.system_queue_size
    )
    sm = ServiceManager(
        service_factory=factory,
        dual_queue=dual_q,
        timer=Timer(),
        service_concurrency=acc_cfg.service_concurrency,
        min_idle_services=acc_cfg.min_idle_services,
        max_services=acc_cfg.max_services,
        autoscale_interval=acc_cfg.autoscale_interval,
        service_idle_ttl=acc_cfg.service_ttl,
    )
    access = Access(sm)
    try:
        await access.init(
            response_parser=DictStreamParser(),
            config=acc_cfg,
            session_config=session_cfg,
            strategy=PerChatBotStrategy(),
        )
        msg = _load_message_payload(args)
        wire = WireIRequest(msg)
        if not wire.request_id:
            print(
                "提示: 消息未提供 request_id，Access 会自动生成 UUID 用于多路复用",
                file=sys.stderr,
            )
        print(
            f"已发送 → request_id={wire.request_id} chat_id={wire.chat_id} "
            f"bot_id={wire.bot_id}，等待流式响应…",
            file=sys.stderr,
        )
        async for chunk in access.send_message(wire):
            print("receive chunk:")
            print(json.dumps(chunk, ensure_ascii=False, default=str))

        print("响应完成，等待2分钟，pod销毁")
        await asyncio.sleep(120)
    finally:
        await access.shutdown()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
