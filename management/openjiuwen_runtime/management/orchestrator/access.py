# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Access 入口类 - 编排服务生命周期和消息路由"""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_runtime.management.manager import DeploymentManager

from .message_queue import InMemoryMessageQueue, RabbitMqMessageQueue, ZmqMessageQueue, IMessageQueue
from .models import Message, MessagePriority
from .interfaces import IMessage, MessageWrapper
from .service_manager import ServiceManager
from .timer import Timer

logger = get_logger(__name__)


@dataclass
class AccessConfig:
    """Access 配置类"""

    db_handler: DBHandler
    image: str
    max_concurrency: int = 200
    min_idle_services: int = 1
    max_services: int = 10
    target_port: int = 8000
    invoke_path: str = "/invoke"
    service_ttl: int = 300
    queue_size: int = 100
    message_timeout: int = 30  # 消息处理超时（秒）
    max_retries: int = 3  # 最大重试次数
    queue_backend: str = "memory"
    rabbitmq_url: str = "amqp://localhost"
    rabbitmq_queue_name: str = "orchestrator"
    zmq_bind_address: str = "tcp://*:5555"
    consume_from_broker: bool = False


class Access:
    """Access 入口类，编排服务生命周期和消息路由"""

    def __init__(self, config: AccessConfig):
        self._config = config
        self._deployment_manager: Optional[DeploymentManager] = None
        self._message_queue: Optional[IMessageQueue] = None
        self._timer: Optional[Timer] = None
        self._service_manager: Optional[ServiceManager] = None
        self._running = False
        # 本地消息循环任务句柄（仅在非 broker 消费模式或 broker 降级时启用）
        self._message_loop_task: Optional[asyncio.Task] = None
        # 记录 broker 订阅是否成功启动，stop 时据此决定是否显式解订阅
        self._broker_consumer_started = False
        self._response_channels: dict[str, asyncio.Queue] = {}
        self._consume_from_broker = bool(config.consume_from_broker)
        logger.info(
            f"Access created with config: image='{config.image}', "
            f"max_concurrency={config.max_concurrency}, min_idle_services={config.min_idle_services}, "
            f"max_services={config.max_services}, target_port={config.target_port}, "
            f"invoke_path='{config.invoke_path}', service_ttl={config.service_ttl}s, queue_size={config.queue_size}, "
            f"queue_backend='{config.queue_backend}', consume_from_broker={self._consume_from_broker}"
        )

    async def init(self) -> None:
        """初始化 Access"""
        logger.info("Initializing Access")

        self._deployment_manager = DeploymentManager(db_handler=self._config.db_handler)
        await self._deployment_manager.initialize()
        logger.info("DeploymentManager initialized")

        self._message_queue = self._build_message_queue()
        logger.info("MessageQueue initialized: backend='%s'", (self._config.queue_backend or "memory"))

        self._timer = Timer()
        logger.info("Timer initialized")

        self._service_manager = ServiceManager(
            deployment_manager=self._deployment_manager,
            image=self._config.image,
            max_concurrency=self._config.max_concurrency,
            min_idle_services=self._config.min_idle_services,
            max_services=self._config.max_services,
            target_port=self._config.target_port,
            invoke_path=self._config.invoke_path,
            service_ttl=self._config.service_ttl,
            timer=self._timer,
            message_queue=self._message_queue,
            message_timeout=self._config.message_timeout,
            max_retries=self._config.max_retries,
        )
        logger.info("ServiceManager initialized")

        await self._service_manager._ensure_min_idle_services()
        await self._service_manager.start()

        self._running = True
        self._broker_consumer_started = False
        if self._consume_from_broker and isinstance(self._message_queue, RabbitMqMessageQueue):
            started = await self._message_queue.start_consume(self._handle_broker_message)
            if started:
                self._broker_consumer_started = True
                logger.info(
                    "RabbitMQ consumer mode enabled: queue=%s url=%s",
                    self._config.rabbitmq_queue_name,
                    self._config.rabbitmq_url,
                )
            else:
                # broker 不可用时降级到原有本地 message loop，保证可继续处理 send_message
                logger.warning(
                    "RabbitMQ consumer mode requested but not started, "
                    "fallback to local message loop: queue=%s url=%s",
                    self._config.rabbitmq_queue_name,
                    self._config.rabbitmq_url,
                )
                self._message_loop_task = asyncio.create_task(self._message_loop())
                logger.info("Message loop started")
        else:
            if self._consume_from_broker and not isinstance(self._message_queue, RabbitMqMessageQueue):
                logger.warning(
                    "consume_from_broker is only supported for rabbitmq backend, "
                    "fallback to message loop for backend='%s'",
                    (self._config.queue_backend or "memory"),
                )
            self._message_loop_task = asyncio.create_task(self._message_loop())
            logger.info("Message loop started")

        logger.info("Access initialized successfully")

    async def send_message(self, msg: IMessage) -> dict:
        """
        发送消息

        Args:
            msg: 实现 IMessage 接口的消息对象

        Returns:
            分发结果字典，包含 response_queue 用于接收流式响应
        """
        session_id = msg.get_session_id()
        concurrency = msg.get_session_concurrency()
        ttl = msg.get_session_ttl()
        request_id = msg.get_request_id()
        payload = msg.get_payload()
        priority = msg.get_priority()

        if not session_id:
            logger.error("send_message failed: session_id is required")
            return {
                "success": False,
                "message": "session_id is required",
            }

        logger.debug(
            f"Sending message: session_id='{session_id}', request_id='{request_id}', "
            f"concurrency={concurrency}, ttl={ttl}"
        )

        response_queue: asyncio.Queue = asyncio.Queue()
        self._response_channels[session_id] = response_queue
        if self._consume_from_broker and self._message_loop_task is None:
            logger.error("send_message is unavailable in broker-consumer mode")
            return {
                "success": False,
                "message": "send_message is unavailable when consume_from_broker=True",
            }

        message = MessageWrapper(msg, response_queue)

        try:
            await self._message_queue.put(message)
            logger.debug(f"Message put into queue: session_id='{session_id}'")

            return {
                "success": True,
                "session_id": session_id,
                "response_queue": response_queue,
            }

        except Exception as e:
            logger.error(f"send_message failed: session_id='{session_id}', error={e}")
            return {
                "success": False,
                "message": str(e),
            }

    async def receive_stream(self, session_id: str):
        """
        接收流式响应

        Args:
            session_id: 会话 ID

        Yields:
            响应消息，直到 is_complete == True
        """
        response_queue = self._response_channels.get(session_id)
        if not response_queue:
            logger.error(f"No response queue for session: session_id='{session_id}'")
            return

        try:
            while True:
                message = await response_queue.get()
                yield message
                if message.is_complete:
                    logger.debug(f"Stream completed: session_id='{session_id}'")
                    break
        finally:
            if session_id in self._response_channels:
                del self._response_channels[session_id]

    async def send_response_end(self, session_id: str) -> None:
        """
        发送响应结束消息

        Args:
            session_id: 会话ID
        """
        logger.info(f"Sending response end: session_id='{session_id}'")

        end_message = Message(
            session_id=session_id,
            concurrency=0,
            ttl=0,
            priority=MessagePriority.HIGH,
            payload={"task_type": "response_end", "session_id": session_id},
            response_queue=None,
        )

        await self._message_queue.put(end_message)
        logger.debug(f"Response end message put into queue: session_id='{session_id}'")

    async def stop(self) -> None:
        """停止 Access"""
        logger.info("Stopping Access")

        self._running = False
        try:
            if self._message_queue and isinstance(self._message_queue, RabbitMqMessageQueue):
                if self._broker_consumer_started:
                    try:
                        # 先解订阅，再关连接，避免停机窗口继续收到 broker 消息
                        await self._message_queue.stop_consume()
                    except Exception as exc:
                        logger.warning("Failed to stop RabbitMQ consumer: %s", exc)
                    finally:
                        self._broker_consumer_started = False
                else:
                    logger.debug("RabbitMQ consumer not started, skip stop_consume")

            if self._service_manager:
                await self._service_manager.stop()

            if self._message_queue:
                await self._message_queue.close()
                logger.info("Message queue closed")

            if self._timer:
                await self._timer.stop_all()
                logger.info("Timer stopped")

            if self._deployment_manager:
                await self._deployment_manager.shutdown()
                logger.info("DeploymentManager shutdown")
        finally:
            # 无论前面清理是否出错，都保证本地 loop 被回收
            if self._message_loop_task:
                self._message_loop_task.cancel()
                try:
                    await self._message_loop_task
                except asyncio.CancelledError:
                    pass
                self._message_loop_task = None

        self._response_channels.clear()
        logger.info("Access stopped successfully")

    def _build_message_queue(self) -> IMessageQueue:
        backend = (self._config.queue_backend or "memory").strip().lower()
        if backend == "rabbitmq":
            return RabbitMqMessageQueue(
                url=self._config.rabbitmq_url,
                queue_name=self._config.rabbitmq_queue_name,
                max_size=self._config.queue_size,
            )
        if backend == "zmq":
            return ZmqMessageQueue(
                bind_address=self._config.zmq_bind_address,
                max_size=self._config.queue_size,
            )
        return InMemoryMessageQueue(max_size=self._config.queue_size)

    async def _handle_broker_message(self, message: IMessage) -> None:
        """
        Broker 订阅回调：
        统一走 ServiceManager.handle_message，便于对齐当前编排流程。
        """
        if not self._running:
            # stop 流程中可能仍收到末尾消息，这里直接丢弃避免停机后继续执行业务逻辑
            logger.debug("Broker message dropped because access is stopping/stopped")
            return
        if not self._service_manager:
            logger.warning("Broker message dropped: service_manager not initialized")
            return
        await self._service_manager.handle_message(message)

    async def set_image(self, image: str) -> None:
        """
        设置镜像

        Args:
            image: 镜像名称
        """
        object.__setattr__(self._config, "image", image)
        logger.info(f"Image updated: image='{image}'")

        if self._service_manager:
            await self._service_manager.update_config(image=image)

    async def set_max_concurrency(self, max_concurrency: int) -> None:
        """
        设置最大并发数

        Args:
            max_concurrency: 最大并发数
        """
        object.__setattr__(self._config, "max_concurrency", max_concurrency)
        logger.info(f"Max concurrency updated: max_concurrency={max_concurrency}")

        if self._service_manager:
            await self._service_manager.update_config(max_concurrency=max_concurrency)

    async def set_min_idle_services(self, min_idle_services: int) -> None:
        """
        设置最小空闲服务数

        Args:
            min_idle_services: 最小空闲服务数
        """
        object.__setattr__(self._config, "min_idle_services", min_idle_services)
        logger.info(f"Min idle services updated: min_idle_services={min_idle_services}")

        if self._service_manager:
            await self._service_manager.update_config(min_idle_services=min_idle_services)

    async def set_max_services(self, max_services: int) -> None:
        """
        设置最大服务数

        Args:
            max_services: 最大服务数
        """
        object.__setattr__(self._config, "max_services", max_services)
        logger.info(f"Max services updated: max_services={max_services}")

        if self._service_manager:
            await self._service_manager.update_config(max_services=max_services)

    async def set_service_ttl(self, service_ttl: int) -> None:
        """
        设置服务 TTL

        Args:
            service_ttl: 服务 TTL（秒）
        """
        object.__setattr__(self._config, "service_ttl", service_ttl)
        logger.info(f"Service TTL updated: service_ttl={service_ttl}")

        if self._service_manager:
            await self._service_manager.update_config(service_ttl=service_ttl)

    async def health_check(self) -> dict:
        """
        健康检查

        Returns:
            各组件状态
        """
        logger.debug("Performing health check")

        status = {
            "Access": "running" if self._running else "stopped",
            "deployment_manager": "initialized" if self._deployment_manager else "not_initialized",
            "message_queue": "initialized" if self._message_queue else "not_initialized",
            "timer": "initialized" if self._timer else "not_initialized",
            "service_manager": "initialized" if self._service_manager else "not_initialized",
            "message_loop": "running" if self._service_manager and self._service_manager._running else "stopped",
        }

        if self._message_queue:
            try:
                queue_size = await self._message_queue.size()
                status["queue_size"] = queue_size
            except Exception as e:
                status["queue_size"] = f"error: {e}"

        if self._service_manager:
            try:
                services = await self._service_manager.list_services()
                status["active_services"] = len(services)
            except Exception as e:
                status["active_services"] = f"error: {e}"

        if self._timer:
            try:
                status["active_timers"] = len(self._timer._timers)
            except Exception as e:
                status["active_timers"] = f"error: {e}"

        logger.debug(f"Health check result: {status}")
        return status
