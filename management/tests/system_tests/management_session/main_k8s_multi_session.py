# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
#
# 多 session 单 pod 串行发送 场景：3 个不同 session_ttl 的 session 共享同一 pod，
# 等待最长 session_ttl 到期后观察 pod 自动销毁。

# 运行::
# uv run python tests/system_tests/management_session/main_k8s_multi_session.py \
#   --image swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-amd64:0.0.1 \
#   --namespace default \
#   --name-prefix jiuwenclaw \
#   --container-port 18092 \
#   --ws-tls 0 \
#   --min-idle 0 \
#   --max-services 1 \
#   --service-concurrency 10 \
#   --service-ttl 10 \
#   --session-run-mode serial \
#   --message-timeout 300 \
#   --env MODEL_PROVIDER=OpenAI \
#   --env MODEL_NAME=Qwen/Qwen3-32B \
#   --env API_BASE=https://api.siliconflow.cn/v1 \
#   --env API_KEY=sk- \
#   --session "id=A,ttl=10,msgs=1,conc=1" \
#   --session "id=B,ttl=20,msgs=1,conc=1" \
#   --session "id=C,ttl=30,msgs=1,conc=1" \
#   --message-template-json '{"chat_id":"chat_{sid}","bot_id":"bot_{sid}","user_id":"u_{sid}","session_id":"sess_{sid}","channel_id":"web","req_method":"chat.send","params":{"session_id":"sess_{sid}","content":"hello","mode":"agent","query":"1+1等于几"},"is_stream":true,"metadata":{"query":{},"method":"chat.send"}}'

# 注：``--max-services 1 --min-idle 0`` 用于强制 3 个 session 落到唯一一个 pod，
# 并保证最后一个 session 到期后该 pod 必被回收（idle 池超过 min_idle 立即回收）。

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional

from openjiuwen_runtime.management.session.access import Access
from openjiuwen_runtime.management.session.dual_queue import PriorityDualAsyncQueues
from openjiuwen_runtime.management.session.interfaces import (
    IRequest,
    IResponseParser,
    IServiceHandler,
    IServiceInstanceFactory,
)
from openjiuwen_runtime.management.session.k8s_service_handler import (
    K8sDeployController,
    K8sServiceHandler,
)
from openjiuwen_runtime.management.session.models import AccessConfig, SessionConfig
from openjiuwen_runtime.management.session.service_handler import ServiceHandler
from openjiuwen_runtime.management.session.service_manager import (
    QueueItem,
    ServiceManager,
)
from openjiuwen_runtime.management.session.session_request import SessionRequest
from openjiuwen_runtime.management.session.strategies.per_chat_bot import (
    PerChatBotStrategy,
)
from openjiuwen_runtime.management.session.timer import Timer
from openjiuwen_runtime.management.session.ws_client_channel import (
    WSServiceMessageChannel,
)


def _e2a_nested_is_complete(data: dict[str, Any]) -> bool:
    prov = data.get("provenance")
    if not isinstance(prov, dict):
        return False
    det = prov.get("details")
    if not isinstance(det, dict):
        return False
    return det.get("is_complete") is True


class E2aEnvelopResponseParser(IResponseParser):
    """与 main_k8s_access.py 的解析器等价。"""

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


class WireIRequest(IRequest):
    """``IRequest`` + ``wire_dict`` 透传整条上行 JSON。

    本脚本中 *仅作为 ``raw_msg`` 容器*，由 SessionRequest 直接持有；
    ``Access.send_message`` 走 ``isinstance(msg, ISessionRequest)`` 分支时不会再读
    本对象的 ``request_id`` 等字段，但 ``WSServiceMessageChannel._payload_from_raw``
    会读 ``wire_dict``，故必须保留。
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


@dataclass(frozen=True)
class SessionSpec:
    sid: str
    ttl: int
    msgs: int
    conc: int


def _parse_session_spec(s: str) -> SessionSpec:
    """``id=A,ttl=10,msgs=2,conc=2`` → SessionSpec。"""
    fields: dict[str, str] = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SystemExit(f"无效 --session 片段（缺 '='）: {part!r}")
        k, _, v = part.partition("=")
        fields[k.strip()] = v.strip()
    miss = {"id", "ttl", "msgs", "conc"} - set(fields)
    if miss:
        raise SystemExit(f"--session 缺字段 {sorted(miss)}: {s!r}")
    try:
        return SessionSpec(
            sid=fields["id"],
            ttl=int(fields["ttl"]),
            msgs=int(fields["msgs"]),
            conc=int(fields["conc"]),
        )
    except ValueError as e:
        raise SystemExit(f"--session 数值字段非法: {s!r} ({e})") from e


_DEFAULT_TEMPLATE = (
    '{"chat_id":"chat_{sid}","bot_id":"bot_{sid}","user_id":"u_{sid}",'
    '"session_id":"sess_{sid}","channel_id":"web","req_method":"chat.send",'
    '"params":{"session_id":"sess_{sid}","content":"hi from {sid} #{seq}",'
    '"mode":"agent","query":"1+1=?"},"is_stream":true,'
    '"metadata":{"query":{},"method":"chat.send"}}'
)


def _render_message(template: str, sid: str, seq: int, rid: str) -> dict[str, Any]:
    """模板字面量替换 ``{sid}`` / ``{seq}`` / ``{rid}`` 三个占位符。

    用 ``str.replace`` 而非 ``.format``，避免 JSON 中 ``{}``（如 ``"query":{}``）
    被 format 解析报错。
    """
    rendered = (
        template.replace("{sid}", sid)
        .replace("{seq}", str(seq))
        .replace("{rid}", rid)
    )
    try:
        data = json.loads(rendered)
    except json.JSONDecodeError as e:
        raise SystemExit(f"模板渲染后非合法 JSON: {e}; 渲染结果: {rendered}") from e
    if not isinstance(data, dict):
        raise SystemExit("模板渲染后顶层必须是 JSON 对象")
    data["request_id"] = rid
    return data


def _parse_env(pairs: List[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"无效 --env 格式（需 KEY=val）: {p!r}")
        k, _, v = p.partition("=")
        out[k] = v
    return out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Access + 真实 K8s + WebSocket：多 session 单 pod 并发观测脚本"
    )
    # K8s / 部署
    p.add_argument("--kubeconfig", default=None)
    p.add_argument("--namespace", default="default")
    p.add_argument("--image", required=True)
    p.add_argument("--name-prefix", default="jiuwenclaw")
    p.add_argument("--container-name", default="jiuwenclaw-agentserver")
    p.add_argument("--container-port", type=int, default=18092)
    p.add_argument("--port-name", default="http1")
    p.add_argument(
        "--image-pull-policy",
        default="IfNotPresent",
        choices=["Always", "IfNotPresent", "Never"],
    )
    p.add_argument("--env", action="append", default=[], metavar="K=V")
    p.add_argument("--readiness-initial-delay", type=int, default=5)
    p.add_argument("--readiness-period", type=int, default=10)
    p.add_argument("--ready-timeout", type=float, default=300.0)
    p.add_argument("--ready-poll", type=float, default=2.0)
    # Access / ServiceManager
    p.add_argument("--user-queue", type=int, default=1000)
    p.add_argument("--system-queue", type=int, default=100)
    p.add_argument(
        "--service-concurrency",
        type=int,
        default=10,
        help="单 pod 总并发；需 ≥ sum(--session 的 conc)",
    )
    p.add_argument(
        "--min-idle",
        type=int,
        default=0,
        help="0 即不维持热备，便于观察 pod 最终被删除（推荐保持 0）",
    )
    p.add_argument(
        "--max-services",
        type=int,
        default=1,
        help="1 即强制单 pod；调大也行但需确保亲和命中",
    )
    p.add_argument("--target-port", type=int, default=None)
    p.add_argument("--invoke-path", default="")
    p.add_argument("--ws-tls", type=int, default=0, choices=(0, 1))
    p.add_argument("--service-ttl", type=int, default=15)
    p.add_argument("--message-timeout", type=int, default=300)
    p.add_argument("--autoscale-interval", type=float, default=0.2)
    # 多 session（重复 3+ 次）
    p.add_argument(
        "--session",
        action="append",
        default=[],
        metavar="id=..,ttl=..,msgs=..,conc=..",
        help="重复指定每个 session 的 (id, ttl 秒, msgs 条数, conc 并发)",
    )
    p.add_argument(
        "--session-run-mode",
        choices=["serial", "parallel"],
        default="serial",
        help="多 session 运行模式: serial (串行，一个结束后再开下一个) 或 parallel (并发同时发起)",
    )
    p.add_argument(
        "--message-template-json",
        default=_DEFAULT_TEMPLATE,
        help="消息模板（顶层 JSON 对象）；占位符 {sid}/{seq}/{rid}",
    )
    p.add_argument(
        "--observe-buffer",
        type=int,
        default=30,
        help="所有响应完成后, 在 max_ttl + service_ttl 之外再多观测的秒数（默认 30）",
    )
    return p


# ---------- 单条请求 / 单 session 跑批 ----------


def _ts_now() -> str:
    return time.strftime("%H:%M:%S", time.localtime()) + f".{int(time.time()*1000)%1000:03d}"


async def _run_one(
    access: Access,
    spec: SessionSpec,
    seq: int,
    template: str,
    *,
    t0: float,
) -> None:
    rid = f"req_{spec.sid}_{seq}_{uuid.uuid4().hex[:6]}"
    msg = _render_message(template, spec.sid, seq, rid)
    wire = WireIRequest(msg)
    sreq = SessionRequest(
        session_id=spec.sid,
        concurrency=spec.conc,
        ttl=spec.ttl,
        request_id=rid,
        raw=wire,
    )
    ts0 = time.monotonic() - t0
    print(
        f"[{_ts_now()} +{ts0:6.2f}s] >>> SEND  sid={spec.sid} req#{seq} rid={rid}",
        file=sys.stderr,
        flush=True,
    )
    chunks = 0
    async for chunk in access.send_message(sreq):
        chunks += 1
        # 仅打印较短摘要，避免日志被刷屏
        snippet = json.dumps(chunk, ensure_ascii=False, default=str)
        if len(snippet) > 200:
            snippet = snippet[:200] + "...<truncated>"
        print(
            f"[{_ts_now()}] chunk sid={spec.sid} req#{seq} #{chunks} {snippet}",
            file=sys.stderr,
            flush=True,
        )
    ts1 = time.monotonic() - t0
    print(
        f"[{_ts_now()} +{ts1:6.2f}s] <<< DONE  sid={spec.sid} req#{seq} chunks={chunks}",
        file=sys.stderr,
        flush=True,
    )


async def _run_session(
    access: Access, spec: SessionSpec, template: str, *, t0: float
) -> None:
    print(
        f"[{_ts_now()}] === START session sid={spec.sid} ttl={spec.ttl}s "
        f"msgs={spec.msgs} conc={spec.conc} ===",
        file=sys.stderr,
        flush=True,
    )
    await asyncio.gather(
        *(_run_one(access, spec, i, template, t0=t0) for i in range(spec.msgs))
    )
    print(
        f"[{_ts_now()}] === END   session sid={spec.sid} (全部 {spec.msgs} 条已完成) ===",
        file=sys.stderr,
        flush=True,
    )


async def _amain() -> int:
    args = _build_parser().parse_args()

    if not args.session or len(args.session) < 1:
        raise SystemExit("至少需要一个 --session（推荐 3 个，覆盖不同 ttl）")
    specs = [_parse_session_spec(s) for s in args.session]

    sum_conc = sum(s.conc for s in specs)
    if sum_conc > args.service_concurrency:
        raise SystemExit(
            f"sum(conc)={sum_conc} 超过 --service-concurrency={args.service_concurrency}; "
            f"无法保证全部 session 同 pod"
        )
    if args.min_idle > 0:
        print(
            f"提示: --min-idle={args.min_idle} > 0，意味着即使最后一个 session 到期，"
            "维持热备的 pod 仍不会被回收（不会观测到“全部 pod 被删除”）。",
            file=sys.stderr,
        )
    if args.max_services > 1:
        print(
            f"提示: --max-services={args.max_services} > 1，理论上仍会落同 pod（亲和+容量优先），"
            "但若 autoscale 多拉了备 pod，需自行 kubectl 区分。",
            file=sys.stderr,
        )

    target_port = (
        args.target_port if args.target_port is not None else args.container_port
    )
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
    # session_config 的值在本脚本中不会被使用（我们直接构 SessionRequest），
    # 这里仅为满足 Access.init 必填参数；保留默认 ttl=0/conc=1 即可。
    session_cfg = SessionConfig(concurrency=1, ttl=0)

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
            response_parser=E2aEnvelopResponseParser(),
            config=acc_cfg,
            session_config=session_cfg,
            strategy=PerChatBotStrategy(),
        )

        max_ttl = max(s.ttl for s in specs)

        # 根据模式计算所需的最大等待时间
        if args.session_run_mode == "parallel":
            # 并发模式：所有 session 几乎同时结束，等待时间取决于 ttl 最大的那个
            base_ttl = max(s.ttl for s in specs)
        else:
            # 串行模式：最后一个 session 结束才开始倒计时，等待时间取决于最后一个 session 的 ttl
            base_ttl = specs[-1].ttl

        wait_after = base_ttl + args.service_ttl + args.observe_buffer

        print(
            "============================================================\n"
            f"sessions={[(s.sid, s.ttl, s.msgs, s.conc) for s in specs]}\n"
            f"sum_conc={sum_conc} service_concurrency={args.service_concurrency}\n"
            f"max_ttl={max_ttl}s service_ttl={args.service_ttl}s "
            f"buffer={args.observe_buffer}s → 全部完成后等待 {wait_after}s\n"
            f"建议另开终端: kubectl get pods -n {args.namespace} -w | "
            f"findstr {args.name_prefix}\n"
            "============================================================",
            file=sys.stderr,
            flush=True,
        )

        t0 = time.monotonic()
        total_msgs = sum(s.msgs for s in specs)

        print(
            f"[{_ts_now()}] >>> 开始执行模式：{args.session_run_mode.upper()}，共 {len(specs)} 个 Session，{total_msgs} 条消息",
            file=sys.stderr, flush=True
        )

        if args.session_run_mode == "parallel":
            # 并发执行
            await asyncio.gather(
                *(_run_session(access, s, args.message_template_json, t0=t0) for s in specs)
            )
        else:
            # 串行执行
            for s in specs:
                await _run_session(access, s, args.message_template_json, t0=t0)
                ts_end_session = time.monotonic() - t0
                print(f"[{_ts_now()}] >>> Session {s.sid} 串行环节结束 (elapsed={ts_end_session:.2f}s)",
                      file=sys.stderr, flush=True)

        t_done = time.monotonic() - t0
        print(
            f"[{_ts_now()}] >>> 所有请求均已完成 (总耗时={t_done:.2f}s)",
            file=sys.stderr,
            flush=True,
        )

        # 预计事件时间锚打印
        if args.session_run_mode == "parallel":
            for s in specs:
                print(
                    f"[预期] sid={s.sid} 在 +{s.ttl}s (≈{t_done + s.ttl:.1f}s 起) 触发 remove_session",
                    file=sys.stderr,
                )
            print(
                f"[预期] 最长 sid={max(specs, key=lambda x: x.ttl).sid} 到期后 +{args.service_ttl}s "
                f"(≈{t_done + base_ttl + args.service_ttl:.1f}s) pod 转 idle 并被回收/删除",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(
                f"[预期] Pod 将在最后一个 Session (sid={specs[-1].sid}) 结束后的 TTL 到期并转 idle\n"
                f"[预期] 预计回收时间点：≈{t_done + base_ttl + args.service_ttl:.1f}s",
                file=sys.stderr, flush=True
            )

    finally:
        await access.shutdown()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
