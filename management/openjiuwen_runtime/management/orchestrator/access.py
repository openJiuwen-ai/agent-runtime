# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Access 入口类 - 编排服务生命周期和消息路由"""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_runtime.management.manager import DeploymentManager

from .message_queue import InMemoryMessageQueue, IMessageQueue
from .models import Message, MessagePriority
from .interfaces import IMessage
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


class Access:
    """Access 入口类，编排服务生命周期和消息路由"""

    def __init__(self, config: AccessConfig):
        self._config = config
        self._deployment_manager: Optional[DeploymentManager] = None
        self._message_queue: Optional[IMessageQueue] = None
        self._timer: Optional[Timer] = None
        self._service_manager: Optional[ServiceManager] = None
        self._message_loop_task: Optional[asyncio.Task] = None
        self._running = False
        self._response_channels: dict[str, asyncio.Future] = {}
        logger.info(
            f"Access created with config: image='{config.image}', "
            f"max_concurrency={config.max_concurrency}, min_idle_services={config.min_idle_services}, "
            f"max_services={config.max_services}, target_port={config.target_port}, "
            f"invoke_path='{config.invoke_path}', service_ttl={config.service_ttl}s, queue_size={config.queue_size}"
        )

    async def init(self) -> None:
        """初始化 Access"""
        logger.info("Initializing Access")

        self._deployment_manager = DeploymentManager(db_handler=self._config.db_handler)
        await self._deployment_manager.initialize()
        logger.info("DeploymentManager initialized")

        self._message_queue = InMemoryMessageQueue(max_size=self._config.queue_size)
        logger.info(f"MessageQueue initialized with size={self._config.queue_size}")

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
        )
        logger.info("ServiceManager initialized")

        self._running = True
        self._message_loop_task = asyncio.create_task(self._message_loop())
        logger.info("Message loop started")

        logger.info("Access initialized successfully")

    async def send_message(self, msg: IMessage) -> dict:
        """
        发送消息

        Args:
            msg: 实现 IMessage 接口的消息对象

        Returns:
            分发结果字典
        """
        session_id = msg.get_session_id()
        concurrency = msg.get_session_concurrency()
        ttl = msg.get_session_ttl()
        request_id = msg.get_request_id()
        payload = msg.get_payload()
        priority = msg.get_priority()
        response_channel = msg.get_response_channel()

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

        response_future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._response_channels[session_id] = response_future

        message = Message(
            session_id=session_id,
            request_id=request_id,
            concurrency=concurrency,
            ttl=ttl,
            priority=priority if priority else MessagePriority.MEDIUM,
            payload=payload,
            response_channel=response_channel if response_channel else response_future,
        )

        try:
            await self._message_queue.put(message)
            logger.debug(f"Message put into queue: session_id='{session_id}'")

            result = await response_future
            return result

        except Exception as e:
            logger.error(f"send_message failed: session_id='{session_id}', error={e}")
            return {
                "success": False,
                "message": str(e),
            }
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
            response_channel=None,
        )

        await self._message_queue.put(end_message)
        logger.debug(f"Response end message put into queue: session_id='{session_id}'")

    async def stop(self) -> None:
        """停止 Access"""
        logger.info("Stopping Access")

        self._running = False

        if self._message_loop_task:
            self._message_loop_task.cancel()
            try:
                await self._message_loop_task
            except asyncio.CancelledError:
                pass
            logger.info("Message loop stopped")

        if self._service_manager:
            services = await self._service_manager.list_services()
            for service_info in services:
                try:
                    await self._service_manager.stop_service(service_info.deployment_id)
                except Exception as e:
                    logger.error(
                        f"Failed to stop service: deployment_id='{service_info.deployment_id}', error={e}"
                    )
            logger.info("All services stopped")

        if self._message_queue:
            await self._message_queue.close()
            logger.info("Message queue closed")

        if self._timer:
            await self._timer.stop_all()
            logger.info("Timer stopped")

        if self._deployment_manager:
            await self._deployment_manager.shutdown()
            logger.info("DeploymentManager shutdown")

        self._response_channels.clear()
        logger.info("Access stopped successfully")

    async def _message_loop(self) -> None:
        """消息处理循环"""
        logger.info("Message loop started")

        while self._running:
            try:
                message = await self._message_queue.get()
                logger.debug(
                    f"Message received from queue: session_id='{message.session_id}', "
                    f"priority={message.priority}"
                )

                await self._service_manager.handle_message(message)

                if message.response_channel and isinstance(message.response_channel, asyncio.Future):
                    if not message.response_channel.done():
                        message.response_channel.set_result({
                            "success": True,
                            "session_id": message.session_id,
                        })

            except asyncio.CancelledError:
                logger.debug("Message loop cancelled")
                break
            except Exception as e:
                logger.error(f"Message loop error: error={e}")
                await asyncio.sleep(0.1)

        logger.info("Message loop ended")

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
            "message_loop": "running" if self._running and self._message_loop_task and not self._message_loop_task.done() else "stopped",
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
