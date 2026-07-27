# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""单服务实例 = 一个 WebSocket 客户端：在 deploy 后根据 pod（或容器的 host）与配置 port+path 建链，多 request_id 在一条连接上并发流式多路复用。"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Dict, Optional

import websockets
import websockets.exceptions

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import (
    IResponseParser,
    IServiceHandler,
    OnRequestCompleteCallback,
    SessionRequestWrapper,
)

logger = get_logger(__name__)

__all__ = ("WSServiceMessageChannel", "serialize_request_payload")

PayloadBuilder = Callable[[Any], str]


def serialize_request_payload(raw_msg: Any) -> str:
    """将 ``IRequest`` / 原始业务对象编码为 WebSocket 发送的 JSON 字符串。"""
    obj = _to_jsonable(raw_msg)
    return json.dumps(obj, ensure_ascii=False)


def _to_jsonable(obj: Any) -> Any:
    wire = getattr(obj, "wire_dict", None)
    if isinstance(wire, dict):
        return wire
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump()  # type: ignore[no-untyped-call]
    return {
        "request_id": getattr(obj, "request_id", None),
        "chat_id": getattr(obj, "chat_id", None),
        "user_id": getattr(obj, "user_id", None),
        "bot_id": getattr(obj, "bot_id", None),
        "session_id": getattr(obj, "session_id", None),
    }


def _build_ws_url(
        host: str, port: int, path: str, use_tls: bool
) -> str:
    """空路径时仅 ``ws(s)://host:port``，不附加任何 path（不默认 ``/invoke``）。"""
    scheme = "wss" if use_tls else "ws"
    base = f"{scheme}://{host}:{int(port)}"
    s = (path or "").strip()
    if not s:
        return base
    if not s.startswith("/"):
        s = f"/{s}"
    return base + s


class WSServiceMessageChannel:
    """每个 :class:`ServiceHandler` 持有一个实例，与对端全双工流式通信。

    以结构子类型满足 :class:`IServiceMessageChannel`；具体类**不**继承该 ``Protocol``，
    避免与类型检查器对「具体类子类化 Protocol」的告警或排斥。
    """

    def __init__(
            self,
            target_port: Optional[int] = None,
            invoke_path: str = "",
            *,
            target_container: Optional[str] = None,
            ws_use_tls: bool = False,
            payload_from_raw: Optional[PayloadBuilder] = None,
            connect_timeout: float = 30.0,
            additional_headers: Optional[Any] = None,
            verify_peer: Optional[Callable[[dict], bool]] = None,
            ws_ping_interval: float = 20.0,
            ws_ping_timeout: float = 20.0,
    ) -> None:
        self._fallback_port = int(target_port) if target_port is not None else None
        self._port = self._fallback_port or 0
        self._target_container = target_container
        self._path = invoke_path
        self._use_tls = ws_use_tls
        self._payload_from_raw: PayloadBuilder = (
                payload_from_raw or serialize_request_payload
        )
        self._connect_timeout = connect_timeout
        # 可选：握手期附加的 HTTP 头（如链路鉴权令牌）。
        # 可传 dict（静态），或无参回调 ``() -> Optional[dict]``（每次连接现取，
        # 用于令牌需逐次刷新的场景，避免重连复用同一令牌被对端的 nonce/有效期校验拦截）。
        # 默认 None=不附加，行为与既有调用方完全一致。
        self._additional_headers: Optional[Any] = additional_headers
        # 可选：对端核验回调 ``(connection_ack_frame: dict) -> bool``。设置后，连接建立即
        # 消费首帧 connection.ack 交其核验（如反向链路鉴权：确认连到的是合法对端），返回
        # False 则断开。默认 None=不核验，行为与既有调用方完全一致。
        self._verify_peer: Optional[Callable[[dict], bool]] = verify_peer

        # 强引用：接收循环须稳定持有 ServiceHandler 以 dispatch；弱引用在部分 GC/嵌入场景下
        # 可能在 recv 首帧前失效，导致接收协程空跑退出、全链路无下行（表现为「能连上但不转发」）。
        self._handler: Optional[IServiceHandler] = None
        self._default_parser: Optional[IResponseParser] = None
        self._ws_url: Optional[str] = None
        self._ws: Any = None
        self._recv_task: Optional[asyncio.Task[None]] = None
        self._connect_lock = asyncio.Lock()
        self._closed = False
        # 在途 request 流式结束: is_completed 时 set
        self._request_done: Dict[str, asyncio.Event] = {}
        # 在途 request 的 cancel Future（由 send 注册，连接断开时由 _recv_loop 唤醒）
        self._inflight_cancels: Dict[str, "asyncio.Future"] = {}
        self._last_service_id: str = ""
        self._ws_ping_interval = ws_ping_interval
        self._ws_ping_timeout = ws_ping_timeout

        logger.debug(
            "WSServiceMessageChannel: port=%s container=%s path=%s tls=%s",
            target_port,
            target_container,
            invoke_path,
            ws_use_tls,
        )

    def bind_handler(
            self, handler: IServiceHandler, response_parser: IResponseParser
    ) -> None:
        """在 :class:`ServiceHandler` 构造阶段绑定，供接收循环 dispatch。

        经 :meth:`ServiceHandler.invoke_channel` → :meth:`send` 上行业务；勿用弱引用，否则
        ``_recv_loop`` 可能在首包前取不到 handler 而退出，连接已建却无法写回
        ``response_queue``/完成 request。
        """
        self._handler = handler
        self._default_parser = response_parser
        logger.debug("WSS 已绑定 ServiceHandler, id=%s", getattr(handler, "id", None))

    async def on_pod_ready(self, service_id: str, pod_info: Any) -> None:
        """由 ``ServiceHandler.deploy`` 在 Pod/容器可用后回调，组合 URL，并**预建 WebSocket 链**后返回。

        对多 container Pod，优先使用 ``target_container`` 在 ``pod_info.containers`` 中
        选择端口；若未提供则回退到 ``pod_info.port``，最后回退到构造时 ``target_port``。
        """
        # K8s: PodDeployInfo.pod_ip + port；Docker: DockerRunInfo.host + port
        host = getattr(pod_info, "pod_ip", None) or getattr(pod_info, "host", None)
        if not host or not str(host).strip():
            raise ValueError("pod_info 中缺少可连接的 host (pod_ip 或 host)")

        selected_port: Optional[int] = None
        container_map = getattr(pod_info, "containers", None)
        if isinstance(container_map, dict) and container_map and self._target_container:
            endpoint = container_map.get(self._target_container)
            if endpoint is None:
                raise ValueError(f"pod_info.containers 中不存在 container={self._target_container}")
            endpoint_port = getattr(endpoint, "port", None)
            if endpoint_port is not None:
                pi = int(endpoint_port)
                if pi > 0:
                    selected_port = pi

        if selected_port is None:
            p_attr = getattr(pod_info, "port", None)
            if p_attr is not None:
                pi = int(p_attr)
                if pi > 0:
                    selected_port = pi
        if selected_port is None:
            selected_port = self._fallback_port
        if selected_port is None or selected_port <= 0:
            raise ValueError("无法确定可连接端口: 缺少 pod_info.port 且未提供可用 target_port")
        self._port = selected_port
        self._ws_url = _build_ws_url(str(host), self._port, self._path, self._use_tls)
        self._last_service_id = service_id
        logger.info("WSS 基址已就绪: service_id=%s url=%s", service_id, self._ws_url)
        await self.ensure_connected()
        logger.info("WSS 已在 deploy 后完成预建链: service_id=%s", service_id)

    async def close(self) -> None:
        """释放 WebSocket 与接收协程。"""
        self._closed = True
        t = self._recv_task
        self._recv_task = None
        if t and not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        w = self._ws
        self._ws = None
        if w is not None:
            with contextlib.suppress(Exception):
                await w.close()
        for ev in self._request_done.values():
            if not ev.is_set():
                ev.set()
        self._request_done.clear()
        for cancel_fut in self._inflight_cancels.values():
            if not cancel_fut.done():
                try:
                    cancel_fut.set_result(None)
                except asyncio.InvalidStateError:
                    pass
        self._inflight_cancels.clear()
        self._handler = None
        self._default_parser = None
        logger.info("WSServiceMessageChannel 已关闭: last_service_id=%s", self._last_service_id)

    async def ensure_connected(self) -> None:
        """显式建立 WebSocket 连接（若尚未建立或已断开）。

        暴露为公共方法供 ``ServiceHandler._reconnect_or_evict`` 在接收侧死亡后
        做重连探测调用; 内部仍带 ``_connect_lock`` 防并发重连。
        """
        if not self._ws_url:
            raise RuntimeError(
                "WebSocket URL 未就绪: 需先 K8s/Docker deploy 并成功 on_pod_ready"
            )
        w = self._ws
        if w is not None and not _ws_is_closed(w):
            return
        async with self._connect_lock:
            w2 = self._ws
            if w2 is not None and not _ws_is_closed(w2):
                return
            if self._closed:
                raise RuntimeError("WSS 已关闭")
            if not self._ws_url:
                raise RuntimeError("WebSocket URL 未设置")
            logger.info("WSS 正在连接: %s, ping_interval=%f, ping_timeout=%f", self._ws_url, self._ws_ping_interval, self._ws_ping_timeout)
            # dict 直接用；回调则每次连接现取一份（如刷新链路令牌：新 nonce/新签发时间）。
            hdrs = self._additional_headers
            if callable(hdrs):
                hdrs = hdrs()
            new_ws = await asyncio.wait_for(
                websockets.connect(
                    self._ws_url,
                    open_timeout=self._connect_timeout,
                    ping_interval=self._ws_ping_interval,
                    ping_timeout=self._ws_ping_timeout,
                    additional_headers=hdrs,
                ),
                timeout=self._connect_timeout,
            )
            # link-auth 反向：消费首帧 connection.ack 并核验对端令牌；不通过则断开。
            if self._verify_peer is not None:
                first_raw = await asyncio.wait_for(new_ws.recv(), timeout=self._connect_timeout)
                first = _decode_ws_message(first_raw)
                if not self._verify_peer(first if isinstance(first, dict) else {}):
                    with contextlib.suppress(Exception):
                        await new_ws.close()
                    raise RuntimeError("WSS 对端 link-auth 校验失败")
            self._ws = new_ws
            p = self._default_parser
            if p is not None and (self._recv_task is None or self._recv_task.done()):
                if self._recv_task is not None and self._recv_task.done():
                    logger.info("WSS 接收协程已结束, 将重启: url=%s", self._ws_url)
                self._recv_task = asyncio.create_task(self._recv_loop(), name="ws-recv")
            logger.info("WSS 已连接并启动接收循环: %s", self._ws_url)

    async def _recv_loop(self) -> None:
        h = self._handler
        p = self._default_parser
        if h is None or p is None:
            logger.error("WSS 接收循环: handler 或 parser 未绑定, 退出")
            return
        w = self._ws
        if w is None:
            return
        # 接收侧异常退出原因; None 表示正常退出(主动 close/cancel), 不触发摘路由
        died_reason: Optional[str] = None
        try:
            while not self._closed and w is not None and not _ws_is_closed(w):
                try:
                    raw = await w.recv()
                except (websockets.exceptions.ConnectionClosed, OSError) as e:
                    logger.error("WSS 接收已断开: %s", e, exc_info=True)
                    died_reason = f"{type(e).__name__}: {e}"
                    break
                data = _decode_ws_message(raw)
                if data is None:
                    continue
                await h.dispatch_inbound_chunk(data, p)
                rid = p.request_id(data)
                if not rid:
                    continue
                if p.is_completed(data) and rid in self._request_done:
                    self._request_done[rid].set()
                    logger.debug("WSS 本请求流结束: request_id=%s", rid)
                w = self._ws
        except asyncio.CancelledError:
            logger.debug("WSS 接收循环被取消")
            raise
        except Exception as e:
            logger.error("WSS 接收循环异常: %s", e, exc_info=True)
            died_reason = f"{type(e).__name__}: {e}"
        finally:
            # 连接已断：清除 stale ws 引用，使下次 ensure_connected 能重连。
            # 不清除的话 _ws_is_closed 对 CLOSING 态可能返回 False，ensure_connected
            # 会误判"连接正常"直接 return，导致永不重连（keepalive ping timeout 事故根因）。
            self._ws = None
            for ev in self._request_done.values():
                if not ev.is_set():
                    ev.set()
            if died_reason is not None and not self._closed:
                # 接收侧异常退出: 交给 ServiceHandler 失败在飞请求 + 摘路由 + 重连探测。
                # _fail_inflight_requests 会先写 error chunk 到 response_queue, 再
                # set_result(wrapper.cancel) 唤醒 send 协程, 因此此处不再直接 set
                # cancel future (避免在 error chunk 写入前提前唤醒 send 导致前端只见
                # 流静默结束)。
                try:
                    await h.on_receive_loop_died(died_reason)
                except Exception as hook_err:  # noqa: BLE001
                    logger.error(
                        "on_receive_loop_died 回调失败: %s", hook_err, exc_info=True,
                    )
            else:
                # 正常退出(主动 close/cancel): 唤醒所有卡在 `await wrapper.cancel` 的
                # send 协程, 让其走 on_request_complete → _inflight 归零, 否则 session
                # TTL 到期时 inflight>0 无法移除绑定, 亲和性反复路由到死实例。
                for rid, cancel_fut in list(self._inflight_cancels.items()):
                    if not cancel_fut.done():
                        try:
                            cancel_fut.set_result(None)
                        except asyncio.InvalidStateError:
                            pass

    async def send(
            self,
            service_id: str,
            wrapper: SessionRequestWrapper,
            *,
            response_parser: IResponseParser,
            on_request_complete: OnRequestCompleteCallback,
    ) -> None:
        if self._closed:
            raise RuntimeError("WSS 已关闭, 不能发送")
        if wrapper.cancel.done():
            await on_request_complete(wrapper.session_request.request_id)
            return
        rid = wrapper.session_request.request_id
        if not rid:
            logger.error("WSS 多路复用需要非空 request_id")
            await on_request_complete(None)
            return
        self._default_parser = response_parser
        self._last_service_id = service_id
        try:
            await self.ensure_connected()
        except Exception as e:
            logger.error("WSS 连接失败: %s", e, exc_info=True)
            await on_request_complete(rid)
            raise
        w = self._ws
        if w is None or _ws_is_closed(w):
            await on_request_complete(rid)
            return

        ev = asyncio.Event()
        self._request_done[rid] = ev
        self._inflight_cancels[rid] = wrapper.cancel
        try:
            logger.info(
                "WSS 业务上行: service_id=%s request_id=%s",
                service_id,
                rid,
            )
            payload = wrapper.session_request.raw_msg
            if isinstance(payload, (bytearray, memoryview)):
                payload = bytes(payload)
            if not isinstance(payload, (str, bytes)):
                payload = self._payload_from_raw(payload)
            await w.send(payload)
        except Exception as e:
            self._request_done.pop(rid, None)
            self._inflight_cancels.pop(rid, None)
            logger.error("WSS 上行失败: request_id=%s %s", rid, e, exc_info=True)
            await on_request_complete(rid)
            raise

        # Access.send_message 在 finally 里 set cancel；必须等 Access 迭代结束（含终态 yield）再 on_request_complete。
        # 曾与 recv 的 ev、以及 wrapper.cancel 做 asyncio.wait(FIRST_COMPLETED)：若 cancel 先于终端帧入队/消费完成，
        # 会立刻 on_request_complete 并 pop _by_request，recv 仍在写队列 → 表现为尾包丢失。
        # 注意：连接断开时 _recv_loop 的 finally 会直接 set_result(wrapper.cancel) 唤醒此处，
        # 因为此时 recv 已退出、不再写队列，不存在尾包丢失风险。
        await wrapper.cancel
        if not ev.is_set():
            logger.info(
                "WSS 未观察到终端下行即结束(cancel/超时/关闭): request_id=%s",
                rid,
            )
        await on_request_complete(rid)
        self._request_done.pop(rid, None)
        self._inflight_cancels.pop(rid, None)

    @property
    def ws_url(self) -> Optional[str]:
        return self._ws_url


def _ws_is_closed(ws: Any) -> bool:
    if ws is None:
        return True
    # 优先用 closed 属性（websockets legacy: state is State.CLOSED）
    closed = getattr(ws, "closed", None)
    if closed is not None and bool(closed):
        return True
    # 兜底：检查 state 属性（websockets 的 CLOSING/CLOSED 都视为不可用）
    state = getattr(ws, "state", None)
    if state is not None:
        state_name = getattr(state, "name", str(state))
        if state_name in ("CLOSING", "CLOSED"):
            return True
        # 兼容旧版用整数/字符串表示的 state
        if str(state).upper() in ("CLOSING", "CLOSED"):
            return True
    # closed 为 False 且 state 为 OPEN：连接可用
    if closed is not None:
        return False
    return False


def _decode_ws_message(raw: object) -> Optional[dict[str, Any]]:
    try:
        out = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("WSS 下行非 JSON, 已忽略: %s", e)
        return None
    if not isinstance(out, dict):
        logger.error("WSS 下行非 JSON dict, 已忽略")
        return None
    return out
