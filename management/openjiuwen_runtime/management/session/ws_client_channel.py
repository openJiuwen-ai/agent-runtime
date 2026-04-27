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


async def _await_cancel_future(fut: asyncio.Future[Any]) -> None:
    """在 asyncio.wait 中与 Event.wait 并列等待 cancel Future。"""
    await fut


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
            target_port: int,
            invoke_path: str = "",
            *,
            ws_use_tls: bool = False,
            payload_from_raw: Optional[PayloadBuilder] = None,
            connect_timeout: float = 30.0,
    ) -> None:
        self._port = int(target_port)
        self._path = invoke_path
        self._use_tls = ws_use_tls
        self._payload_from_raw: PayloadBuilder = (
                payload_from_raw or serialize_request_payload
        )
        self._connect_timeout = connect_timeout

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
        self._last_service_id: str = ""
        logger.debug(
            "WSServiceMessageChannel: port=%s path=%s tls=%s", target_port, invoke_path, ws_use_tls
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

        此前仅在此写入 URL、首次 ``send`` 才 ``connect``，会出现：K8s 已 Ready 但业务/WS
        尚未监听，或 ``target_port`` 与 Pod ``container_port`` 不一致，导致发不出去。此处用
        ``pod_info.port``（K8s/Docker 部署声明的端口）覆盖通道端口，并 ``await
        _ensure_connected()``，使 ``deploy`` 在「能连上 WS」之后才结束。
        """
        # K8s: PodDeployInfo.pod_ip + port；Docker: DockerRunInfo.host + port
        host = getattr(pod_info, "pod_ip", None) or getattr(pod_info, "host", None)
        if not host or not str(host).strip():
            raise ValueError("pod_info 中缺少可连接的 host (pod_ip 或 host)")
        p_attr = getattr(pod_info, "port", None)
        if p_attr is not None:
            pi = int(p_attr)
            if pi > 0:
                self._port = pi
        self._ws_url = _build_ws_url(str(host), self._port, self._path, self._use_tls)
        self._last_service_id = service_id
        logger.info("WSS 基址已就绪: service_id=%s url=%s", service_id, self._ws_url)
        await self._ensure_connected()
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
        self._handler = None
        self._default_parser = None
        logger.info("WSServiceMessageChannel 已关闭: last_service_id=%s", self._last_service_id)

    async def _ensure_connected(self) -> None:
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
            logger.info("WSS 正在连接: %s", self._ws_url)
            new_ws = await asyncio.wait_for(
                websockets.connect(
                    self._ws_url,
                    open_timeout=self._connect_timeout,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                ),
                timeout=self._connect_timeout,
            )
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
        try:
            while not self._closed and w is not None and not _ws_is_closed(w):
                try:
                    raw = await w.recv()
                except (websockets.exceptions.ConnectionClosed, OSError) as e:
                    logger.error("WSS 接收已断开: %s", e, exc_info=True)
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
        finally:
            for ev in self._request_done.values():
                if not ev.is_set():
                    ev.set()

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
            await self._ensure_connected()
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
        try:
            logger.info(
                "WSS 业务上行: service_id=%s request_id=%s bytes=%s",
                service_id,
                rid,
                len(wrapper.session_request.raw_msg),
            )
            await w.send(wrapper.session_request.raw_msg)
        except Exception as e:
            self._request_done.pop(rid, None)
            logger.error("WSS 上行失败: request_id=%s %s", rid, e, exc_info=True)
            await on_request_complete(rid)
            raise

        # 等下行 is_completed 置位；与 cancel/关链 竞态用 wait 而非短轮询
        t_ev = asyncio.create_task(ev.wait())
        t_cancel = asyncio.create_task(_await_cancel_future(wrapper.cancel))
        try:
            _done, pending = await asyncio.wait(
                {t_ev, t_cancel},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
        except Exception:
            t_ev.cancel()
            t_cancel.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t_ev
            with contextlib.suppress(asyncio.CancelledError):
                await t_cancel
            raise
        if not ev.is_set() and (self._closed or wrapper.cancel.done()):
            logger.info("WSS 本请求在终态前结束(取消/关闭): request_id=%s", rid)
        await on_request_complete(rid)
        self._request_done.pop(rid, None)

    @property
    def ws_url(self) -> Optional[str]:
        return self._ws_url


def _ws_is_closed(ws: Any) -> bool:
    if ws is None:
        return True
    closed = getattr(ws, "closed", None)
    if closed is not None:
        return bool(closed)
    return False


def _decode_ws_message(raw: object) -> Optional[dict[str, Any]]:
    try:
        logger.info("WSS 下行 JSON")
        out = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("WSS 下行非 JSON, 已忽略: %s", e)
        return None
    if not isinstance(out, dict):
        logger.error("WSS 下行非 JSON dict, 已忽略")
        return None
    return out
