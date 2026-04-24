# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""服务处理模块 - 管理服务状态和会话"""

import asyncio
import time
import uuid
import json
from typing import Dict, Optional, Any, Set

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import IServiceHandler, IMessage
from .models import Message, SessionInfo, SessionState, ServiceState
from .timer import Timer

logger = get_logger(__name__)


class ServiceHandler(IServiceHandler):
    """服务处理器，管理服务状态和会话"""

    TRANSITIONS: Dict[ServiceState, Set[ServiceState]] = {
        ServiceState.DEPLOYING: {ServiceState.RUNNING, ServiceState.IDLE},
        ServiceState.RUNNING: {ServiceState.IDLE, ServiceState.UNLOADING},
        ServiceState.IDLE: {ServiceState.RUNNING, ServiceState.UNLOADING},
        ServiceState.UNLOADING: set(),
    }

    def __init__(
        self,
        deployment_id: str,
        max_concurrency: int,
        service_ttl: int,
        timer: Timer,
        deployment_manager: Optional[Any] = None,
        image: Optional[str] = None,
        target_port: int = 8000,
        invoke_path: str = "/invoke",
        service_url: Optional[str] = None,
    ):
        self._deployment_id = deployment_id
        self._max_concurrency = max_concurrency
        self._service_ttl = service_ttl
        self._timer = timer
        self._deployment_manager = deployment_manager
        self._image = image
        self._target_port = target_port
        self._invoke_path = invoke_path
        self._service_url = service_url
        self._state = ServiceState.DEPLOYING
        self._sessions: Dict[str, SessionInfo] = {}
        self._session_queues: Dict[str, asyncio.Queue] = {}
        self._current_concurrency = 0
        self._lock = asyncio.Lock()
        self._websocket: Optional[Any] = None
        self._ws_receive_task: Optional[asyncio.Task] = None
        self._running = False
        self._message_loop_task: Optional[asyncio.Task] = None
        logger.info(
            f"ServiceHandler initialized: deployment_id='{deployment_id}', "
            f"max_concurrency={max_concurrency}, service_ttl={service_ttl}s, "
            f"service_url='{service_url}'"
        )

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def sessions(self) -> Dict[str, SessionInfo]:
        return self._sessions

    @property
    def session_queues(self) -> Dict[str, asyncio.Queue]:
        return self._session_queues

    @property
    def current_concurrency(self) -> int:
        return self._current_concurrency

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @property
    def websocket(self) -> Optional[Any]:
        return self._websocket

    def has_capacity(self) -> bool:
        """检查服务是否有空闲容量"""
        return self._current_concurrency < self._max_concurrency

    async def connect_websocket(self) -> bool:
        """连接 WebSocket 服务"""
        if not self._service_url:
            logger.warning(f"No service URL configured for deployment '{self._deployment_id}'")
            return False

        try:
            import websockets

            ws_url = self._service_url.replace("http://", "ws://").replace("https://", "wss://")
            self._websocket = await websockets.connect(ws_url)
            self._running = True
            self._ws_receive_task = asyncio.create_task(self._ws_receive_loop())
            logger.info(f"WebSocket connected: deployment_id='{self._deployment_id}', url='{ws_url}'")
            return True
        except ImportError:
            logger.warning("websockets library not installed, WebSocket functionality disabled")
            return False
        except Exception as e:
            logger.error(f"Failed to connect WebSocket: deployment_id='{self._deployment_id}', error={e}")
            return False

    async def disconnect_websocket(self) -> None:
        """断开 WebSocket 连接"""
        self._running = False

        if self._ws_receive_task:
            self._ws_receive_task.cancel()
            try:
                await self._ws_receive_task
            except asyncio.CancelledError:
                pass
            self._ws_receive_task = None

        if self._websocket:
            await self._websocket.close()
            self._websocket = None
            logger.info(f"WebSocket disconnected: deployment_id='{self._deployment_id}'")

    async def _ws_receive_loop(self) -> None:
        """WebSocket 接收循环"""
        while self._running and self._websocket:
            try:
                message = await self._websocket.recv()
                await self._handle_ws_response(message)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WebSocket receive error: deployment_id='{self._deployment_id}', error={e}")
                await asyncio.sleep(1)

    async def _handle_ws_response(self, message: str) -> None:
        """处理 WebSocket 响应"""
        try:
            data = json.loads(message)
            session_id = data.get("session_id")
            request_id = data.get("request_id")
            is_complete = data.get("is_complete", False)
            payload = data.get("payload", data)

            if not session_id or not request_id:
                logger.warning(f"Response missing session_id or request_id: {message[:100]}")
                return

            async with self._lock:
                if session_id not in self._sessions:
                    logger.warning(f"Session '{session_id}' not found for response")
                    return

                session = self._sessions[session_id]
                if request_id not in session.pending_requests:
                    logger.warning(f"Request '{request_id}' not found in session '{session_id}'")
                    return

                response_channel = session.pending_requests[request_id]

                response_message = Message(
                    session_id=session_id,
                    request_id=request_id,
                    concurrency=0,
                    ttl=0,
                    priority=data.get("priority", "medium"),
                    payload=payload,
                    is_complete=is_complete,
                )

                if response_channel and isinstance(response_channel, asyncio.Future):
                    if not response_channel.done():
                        response_channel.set_result(response_message)

                if is_complete:
                    del session.pending_requests[request_id]
                    logger.debug(f"Request completed: session_id='{session_id}', request_id='{request_id}'")

                session.last_active_at = time.time()

            logger.debug(
                f"Response handled: session_id='{session_id}', request_id='{request_id}', "
                f"is_complete={is_complete}"
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode WebSocket message: {e}")
        except Exception as e:
            logger.error(f"Error handling WebSocket response: {e}")

    async def send_request(
        self,
        session_id: str,
        payload: Any,
        request_id: Optional[str] = None,
        response_channel: Optional[asyncio.Future] = None,
    ) -> Optional[str]:
        """发送请求到 WebSocket 服务"""
        if not self._websocket:
            logger.warning(f"WebSocket not connected for deployment '{self._deployment_id}'")
            return None

        if session_id not in self._sessions:
            logger.warning(f"Session '{session_id}' not found")
            return None

        request_id = request_id or str(uuid.uuid4())

        async with self._lock:
            session = self._sessions[session_id]
            if response_channel:
                session.pending_requests[request_id] = response_channel

        request_data = {
            "session_id": session_id,
            "request_id": request_id,
            "payload": payload,
        }

        try:
            await self._websocket.send(json.dumps(request_data))
            logger.debug(f"Request sent: session_id='{session_id}', request_id='{request_id}'")
            return request_id
        except Exception as e:
            logger.error(f"Failed to send request: session_id='{session_id}', error={e}")
            async with self._lock:
                if request_id in session.pending_requests:
                    del session.pending_requests[request_id]
            return None

    async def handle_message(self, message: IMessage) -> None:
        logger.debug(f"Handling message: session_id='{message.get_session_id()}'")
        session_id = message.get_session_id()
        if session_id not in self._sessions:
            logger.warning(f"Session '{session_id}' not found, cannot handle message")
            return

        if message.is_complete_msg() and message.get_request_id():
            await self._handle_response_message(message)
        else:
            await self.write_to_session(session_id, message)
        logger.debug(f"Message handled for session '{session_id}'")

    async def _handle_response_message(self, message: IMessage) -> None:
        """处理响应消息"""
        session_id = message.get_session_id()
        request_id = message.get_request_id()

        async with self._lock:
            if session_id not in self._sessions:
                return

            session = self._sessions[session_id]
            if request_id not in session.pending_requests:
                return

            response_channel = session.pending_requests[request_id]
            if response_channel and isinstance(response_channel, asyncio.Future):
                if not response_channel.done():
                    response_channel.set_result(message)

            if message.is_complete_msg():
                del session.pending_requests[request_id]

    async def add_session(self, session_id: str, concurrency: int, ttl: int) -> None:
        async with self._lock:
            if session_id in self._sessions:
                logger.warning(f"Session '{session_id}' already exists")
                return

            if not await self.has_capacity(concurrency):
                logger.warning(
                    f"Cannot add session '{session_id}': insufficient capacity "
                    f"(required={concurrency}, available={self._max_concurrency - self._current_concurrency})"
                )
                return

            now = time.time()
            session_info = SessionInfo(
                session_id=session_id,
                concurrency=concurrency,
                ttl=ttl,
                state=SessionState.RUNNING,
                created_at=now,
                last_active_at=now,
                pending_requests={},
            )
            self._sessions[session_id] = session_info
            self._session_queues[session_id] = asyncio.Queue()
            self._current_concurrency += concurrency

            await self._timer.start_timer(
                f"session_{session_id}", ttl, lambda: self._on_session_timeout(session_id)
            )

            await self._update_state()
            logger.info(
                f"Session added: session_id='{session_id}', concurrency={concurrency}, "
                f"ttl={ttl}s, current_concurrency={self._current_concurrency}"
            )

    async def remove_session(self, session_id: str) -> None:
        async with self._lock:
            if session_id not in self._sessions:
                logger.warning(f"Session '{session_id}' not found, cannot remove")
                return

            session_info = self._sessions[session_id]
            self._current_concurrency -= session_info.concurrency

            for request_id, channel in session_info.pending_requests.items():
                if channel and isinstance(channel, asyncio.Future) and not channel.done():
                    channel.set_exception(RuntimeError(f"Session '{session_id}' removed"))

            del self._sessions[session_id]
            if session_id in self._session_queues:
                del self._session_queues[session_id]

            await self._timer.cancel_timer(f"session_{session_id}")

            await self._update_state()
            logger.info(
                f"Session removed: session_id='{session_id}', "
                f"current_concurrency={self._current_concurrency}"
            )

    async def get_session_count(self) -> int:
        return len(self._sessions)

    async def write_to_session(self, session_id: str, message: IMessage) -> None:
        if session_id not in self._session_queues:
            logger.warning(f"Session queue '{session_id}' not found")
            return

        await self._session_queues[session_id].put(message)
        logger.debug(f"Message written to session queue: session_id='{session_id}'")

    async def _on_session_timeout(self, session_id: str) -> None:
        logger.info(f"Session timeout: session_id='{session_id}'")
        await self.remove_session(session_id)

    def _can_transition(self, target_state: ServiceState) -> bool:
        return target_state in self.TRANSITIONS.get(self._state, set())

    async def _transition_to(self, target_state: ServiceState) -> bool:
        if not self._can_transition(target_state):
            logger.warning(f"Invalid state transition: {self._state} -> {target_state}")
            return False
        old_state = self._state
        self._state = target_state
        logger.info(f"State transition: {old_state} -> {target_state}")
        return True

    async def deploy(self) -> bool:
        if self._state != ServiceState.DEPLOYING:
            logger.warning(f"Cannot deploy: invalid state {self._state}")
            return False

        try:
            from openjiuwen_runtime.management.models.deployment_params import DeployImageParams
            from openjiuwen_runtime.management.models.enums import DeployMode

            params = DeployImageParams(
                image=self._image,
                name=f"service-{self._deployment_id[:8]}",
                version="1.0.0",
                mode=DeployMode.K8S,
                extras={
                    "target_port": self._target_port,
                    "invoke_path": self._invoke_path,
                },
            )
            deployment_info = await self._deployment_manager.deploy_image(params)
            self._deployment_id = deployment_info.deployment_id

            await self._transition_to(ServiceState.IDLE)
            logger.info(f"Service deployed: deployment_id='{self._deployment_id}'")
            return True
        except Exception as e:
            logger.error(f"Failed to deploy service: {e}")
            return False

    async def undeploy(self) -> bool:
        if self._state == ServiceState.UNLOADING:
            logger.warning("Service already unloading")
            return False

        if len(self._sessions) > 0:
            logger.warning(f"Cannot undeploy: {len(self._sessions)} active sessions")
            return False

        if not await self._transition_to(ServiceState.UNLOADING):
            return False

        try:
            success = await self._deployment_manager.delete_deployment(self._deployment_id)
            if success:
                logger.info(f"Service undeployed: deployment_id='{self._deployment_id}'")
            return success
        except Exception as e:
            logger.error(f"Failed to undeploy service: {e}")
            return False

    async def start(self) -> None:
        if self._running:
            logger.warning("ServiceHandler already running")
            return

        self._running = True
        self._message_loop_task = asyncio.create_task(self._message_loop())
        logger.info(f"ServiceHandler started: deployment_id='{self._deployment_id}'")

    async def stop(self) -> None:
        self._running = False

        if hasattr(self, '_message_loop_task') and self._message_loop_task:
            self._message_loop_task.cancel()
            try:
                await self._message_loop_task
            except asyncio.CancelledError:
                pass
            self._message_loop_task = None

        await self.disconnect_websocket()
        logger.info(f"ServiceHandler stopped: deployment_id='{self._deployment_id}'")

    async def _message_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Message loop error: {e}")
                await asyncio.sleep(1)

    async def _update_state(self) -> None:
        if len(self._sessions) == 0:
            if self._state == ServiceState.RUNNING:
                await self._transition_to(ServiceState.IDLE)
        else:
            if self._state == ServiceState.IDLE:
                await self._transition_to(ServiceState.RUNNING)

    async def has_capacity(self, concurrency: int) -> bool:
        return self._current_concurrency + concurrency <= self._max_concurrency

    async def get_session_from_queue(self, session_id: str) -> Optional[Message]:
        if session_id not in self._session_queues:
            logger.warning(f"Session queue '{session_id}' not found")
            return None

        try:
            message = self._session_queues[session_id].get_nowait()
            logger.debug(f"Message retrieved from session queue: session_id='{session_id}'")
            return message
        except asyncio.QueueEmpty:
            logger.debug(f"Session queue empty: session_id='{session_id}'")
            return None

    async def get_pending_request_count(self, session_id: str) -> int:
        """获取 session 中待处理请求的数量"""
        if session_id not in self._sessions:
            return 0
        return len(self._sessions[session_id].pending_requests)

    async def cancel_request(self, session_id: str, request_id: str) -> bool:
        """取消指定请求"""
        async with self._lock:
            if session_id not in self._sessions:
                return False

            session = self._sessions[session_id]
            if request_id not in session.pending_requests:
                return False

            channel = session.pending_requests[request_id]
            if channel and isinstance(channel, asyncio.Future) and not channel.done():
                channel.set_exception(RuntimeError(f"Request '{request_id}' cancelled"))

            del session.pending_requests[request_id]
            logger.debug(f"Request cancelled: session_id='{session_id}', request_id='{request_id}'")
            return True
