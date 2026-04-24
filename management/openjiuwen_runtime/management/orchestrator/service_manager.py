# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""服务管理模块 - 管理服务生命周期和消息路由"""

import asyncio
import time
import uuid
from typing import Dict, List, Optional, Callable, Any

from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_runtime.management.manager import DeploymentManager

from .interfaces import IServiceManager, IMessageQueue, IMessage, MessageWrapper
from .models import Message, MessagePriority, ServiceInfo, ServiceState, SessionInfo
from .service_handler import ServiceHandler
from .session_router import SessionRouter, RoutingStrategy
from .autoscaler import AutoScaler, ScalingConfig, ScalingAction
from .timer import Timer

logger = get_logger(__name__)


class ServiceManager(IServiceManager):
    """服务管理器，管理服务生命周期和消息路由"""

    def __init__(
        self,
        deployment_manager: DeploymentManager,
        image: str,
        max_concurrency: int,
        min_idle_services: int,
        max_services: int,
        target_port: int,
        invoke_path: str,
        service_ttl: int,
        timer: Timer,
        message_queue: IMessageQueue,
        message_timeout: int = 30,
        max_retries: int = 3,
    ):
        self._deployment_manager = deployment_manager
        self._image = image
        self._max_concurrency = max_concurrency
        self._min_idle_services = min_idle_services
        self._max_services = max_services
        self._target_port = target_port
        self._invoke_path = invoke_path
        self._service_ttl = service_ttl
        self._timer = timer
        self._message_queue = message_queue
        self._message_timeout = message_timeout
        self._max_retries = max_retries
        self._services: Dict[str, ServiceHandler] = {}
        self._session_router = SessionRouter(strategy=RoutingStrategy.LOOSE)
        self._autoscaler = AutoScaler(ScalingConfig(
            min_idle_services=min_idle_services,
            max_services=max_services,
        ))
        self._lock = asyncio.Lock()
        self._running = False
        self._message_loop_task: Optional[asyncio.Task] = None
        self._autoscaling_task: Optional[asyncio.Task] = None
        self._config = {
            "image": image,
            "max_concurrency": max_concurrency,
            "min_idle_services": min_idle_services,
            "max_services": max_services,
            "target_port": target_port,
            "invoke_path": invoke_path,
            "service_ttl": service_ttl,
            "message_timeout": message_timeout,
            "max_retries": max_retries,
        }
        logger.info(
            f"ServiceManager initialized: image='{image}', max_concurrency={max_concurrency}, "
            f"min_idle_services={min_idle_services}, max_services={max_services}, "
            f"target_port={target_port}, invoke_path='{invoke_path}', service_ttl={service_ttl}s"
        )

    @property
    def services(self) -> Dict[str, ServiceHandler]:
        return self._services

    @property
    def config(self) -> Dict[str, Any]:
        return self._config

    async def start(self) -> None:
        """启动 ServiceManager"""
        if self._running:
            logger.warning("ServiceManager already running")
            return

        self._running = True
        self._message_loop_task = asyncio.create_task(self._message_loop())
        self._autoscaling_task = asyncio.create_task(self._autoscaling_loop())
        logger.info("ServiceManager started")

    async def stop(self) -> None:
        """停止 ServiceManager"""
        self._running = False

        if self._message_loop_task:
            self._message_loop_task.cancel()
            try:
                await self._message_loop_task
            except asyncio.CancelledError:
                pass
            self._message_loop_task = None

        if self._autoscaling_task:
            self._autoscaling_task.cancel()
            try:
                await self._autoscaling_task
            except asyncio.CancelledError:
                pass
            self._autoscaling_task = None

        for deployment_id in list(self._services.keys()):
            try:
                await self.stop_service(deployment_id)
            except Exception as e:
                logger.error(f"Failed to stop service: deployment_id='{deployment_id}', error={e}")

        logger.info("ServiceManager stopped")

    async def _message_loop(self) -> None:
        """消息处理循环"""
        logger.info("Message loop started")

        while self._running:
            try:
                raw_message = await self._message_queue.get()
                if not isinstance(raw_message, MessageWrapper):
                    logger.error(f"Invalid message type: {type(raw_message)}")
                    continue

                message = raw_message
                logger.debug(
                    f"Message received from queue: session_id='{message.session_id}', "
                    f"priority={message.priority}"
                )

                try:
                    await asyncio.wait_for(
                        self.handle_message(message),
                        timeout=self._message_timeout
                    )

                    await message.queue.put(Message(
                        session_id=message.session_id,
                        request_id=message.request_id,
                        concurrency=0,
                        ttl=0,
                        priority=message.priority,
                        payload={"status": "accepted"},
                        is_complete=False,
                    ))

                except asyncio.TimeoutError:
                    logger.warning(
                        f"Message processing timeout: session_id='{message.session_id}'"
                    )
                    await self._handle_message_failure(message, "timeout")

                except Exception as e:
                    logger.error(
                        f"Message processing error: session_id='{message.session_id}', error={e}"
                    )
                    await self._handle_message_failure(message, str(e))

            except asyncio.CancelledError:
                logger.debug("Message loop cancelled")
                break
            except Exception as e:
                logger.error(f"Message loop error: error={e}")
                await asyncio.sleep(0.1)

        logger.info("Message loop ended")

    async def _handle_message_failure(self, message: MessageWrapper, reason: str) -> None:
        """处理消息失败"""
        if hasattr(message._message, 'retry_count') and hasattr(message._message, 'max_retries'):
            if message._message.retry_count < message._message.max_retries:
                message._message.retry_count += 1
                logger.info(
                    f"Retrying message: session_id='{message.session_id}', "
                    f"retry_count={message._message.retry_count}, reason='{reason}'"
                )
                await self._message_queue.put(message)
                return

        logger.error(
            f"Message failed: session_id='{message.session_id}', reason='{reason}'"
        )
        await message.queue.put(Message(
            session_id=message.session_id,
            request_id=message.request_id,
            concurrency=0,
            ttl=0,
            priority=message.priority,
            payload={"error": reason},
            is_complete=True,
        ))

    async def _autoscaling_loop(self) -> None:
        """自动伸缩检查循环"""
        logger.info("Autoscaling loop started")

        while self._running:
            try:
                await asyncio.sleep(30)
                await self._check_autoscaling()
            except asyncio.CancelledError:
                logger.debug("Autoscaling loop cancelled")
                break
            except Exception as e:
                logger.error(f"Autoscaling loop error: error={e}")
                await asyncio.sleep(5)

        logger.info("Autoscaling loop ended")

    async def deploy_service(self) -> str:
        async with self._lock:
            if len(self._services) >= self._max_services:
                logger.warning(
                    f"Cannot deploy service: max_services limit reached ({self._max_services})"
                )
                raise RuntimeError(f"Maximum services limit reached: {self._max_services}")

            deployment_id = str(uuid.uuid4())
            logger.info(f"Creating new service handler: deployment_id='{deployment_id}'")

            try:
                service_handler = ServiceHandler(
                    deployment_id=deployment_id,
                    max_concurrency=self._max_concurrency,
                    service_ttl=self._service_ttl,
                    timer=self._timer,
                    deployment_manager=self._deployment_manager,
                    image=self._image,
                    target_port=self._target_port,
                    invoke_path=self._invoke_path,
                )
                self._services[deployment_id] = service_handler

                if not await service_handler.deploy():
                    del self._services[deployment_id]
                    raise RuntimeError(f"Failed to deploy service: deployment_id='{deployment_id}'")

                await service_handler.start()

                await self._timer.start_timer(
                    f"service_{deployment_id}",
                    self._service_ttl,
                    lambda: self._on_service_timeout(deployment_id),
                )

                logger.info(
                    f"Service deployed successfully: deployment_id='{deployment_id}', "
                    f"total_services={len(self._services)}"
                )
                return deployment_id

            except Exception as e:
                if deployment_id in self._services:
                    del self._services[deployment_id]
                logger.error(f"Failed to deploy service: error={e}")
                raise

    async def stop_service(self, deployment_id: str) -> bool:
        async with self._lock:
            if deployment_id not in self._services:
                logger.warning(f"Service not found: deployment_id='{deployment_id}'")
                return False

            logger.info(f"Stopping service: deployment_id='{deployment_id}'")

            try:
                await self._timer.cancel_timer(f"service_{deployment_id}")

                service_handler = self._services[deployment_id]
                
                await service_handler.stop()
                
                for session_id in list(service_handler.sessions.keys()):
                    await service_handler.remove_session(session_id)
                    self._session_router.unregister_session(session_id)

                success = await service_handler.undeploy()

                if success:
                    self._session_router.unregister_service(deployment_id)
                    del self._services[deployment_id]
                    logger.info(
                        f"Service stopped successfully: deployment_id='{deployment_id}', "
                        f"remaining_services={len(self._services)}"
                    )
                else:
                    logger.error(f"Failed to stop service: deployment_id='{deployment_id}'")

                return success

            except Exception as e:
                logger.error(
                    f"Error stopping service: deployment_id='{deployment_id}', error={e}"
                )
                return False

    async def list_services(self) -> List[ServiceInfo]:
        services_info = []
        for deployment_id, service_handler in self._services.items():
            service_info = ServiceInfo(
                deployment_id=deployment_id,
                state=service_handler.state,
                sessions=service_handler.sessions,
                created_at=time.time(),
            )
            services_info.append(service_info)
        logger.debug(f"Listed {len(services_info)} services")
        return services_info

    async def send_to_service(self, deployment_id: str, message: IMessage) -> None:
        if deployment_id not in self._services:
            logger.warning(f"Service not found: deployment_id='{deployment_id}'")
            return

        service_handler = self._services[deployment_id]
        await service_handler.handle_message(message)
        logger.debug(f"Message sent to service: deployment_id='{deployment_id}'")

    async def _ensure_min_idle_services(self) -> None:
        idle_count = 0
        for service_handler in self._services.values():
            if service_handler.state == ServiceState.IDLE:
                idle_count += 1

        logger.debug(
            f"Checking min idle services: current_idle={idle_count}, "
            f"min_idle_services={self._min_idle_services}"
        )

        services_to_create = self._min_idle_services - idle_count
        if services_to_create > 0:
            available_slots = self._max_services - len(self._services)
            services_to_create = min(services_to_create, available_slots)

            if services_to_create > 0:
                logger.info(
                    f"Creating {services_to_create} idle services to meet minimum requirement"
                )
                for _ in range(services_to_create):
                    try:
                        await self.deploy_service()
                    except Exception as e:
                        logger.error(f"Failed to create idle service: error={e}")
                        break

    async def _check_autoscaling(self) -> None:
        """检查自动伸缩"""
        metrics = self._autoscaler.collect_metrics(self._services)
        action, target_deployment_id = self._autoscaler.check_scaling(metrics)

        if action == ScalingAction.SCALE_UP:
            try:
                await self.deploy_service()
                self._autoscaler.record_scaling()
                logger.info("Auto-scaling: scaled up")
            except Exception as e:
                logger.error(f"Failed to scale up: error={e}")

        elif action == ScalingAction.SCALE_DOWN and target_deployment_id:
            try:
                await self.stop_service(target_deployment_id)
                self._autoscaler.record_scaling()
                logger.info(f"Auto-scaling: scaled down deployment_id='{target_deployment_id}'")
            except Exception as e:
                logger.error(f"Failed to scale down: error={e}")

        self._autoscaler.cleanup_stale_services(list(self._services.keys()))

    async def _get_available_service(self, session_id: str, concurrency: int = 1) -> Optional[str]:
        deployment_id = self._session_router.get_service_for_session(
            session_id, self._services
        )

        if deployment_id:
            return deployment_id

        for deployment_id, service_handler in self._services.items():
            if service_handler.state == ServiceState.IDLE and service_handler.has_capacity():
                logger.debug(
                    f"Found idle service with capacity: deployment_id='{deployment_id}'"
                )
                return deployment_id

        for deployment_id, service_handler in self._services.items():
            if service_handler.state == ServiceState.RUNNING and service_handler.has_capacity():
                logger.debug(
                    f"Found running service with capacity: deployment_id='{deployment_id}'"
                )
                return deployment_id

        logger.debug(f"No available service found for session_id='{session_id}'")
        return None

    async def _on_service_timeout(self, deployment_id: str) -> None:
        logger.info(f"Service timeout: deployment_id='{deployment_id}'")
        await self._scale_down(deployment_id)

    async def update_config(self, **kwargs) -> None:
        async with self._lock:
            for key, value in kwargs.items():
                if key in self._config:
                    old_value = self._config[key]
                    self._config[key] = value
                    logger.info(
                        f"Config updated: key='{key}', old_value={old_value}, new_value={value}"
                    )

                    if key == "image":
                        self._image = value
                    elif key == "max_concurrency":
                        self._max_concurrency = value
                    elif key == "min_idle_services":
                        self._min_idle_services = value
                    elif key == "max_services":
                        self._max_services = value
                    elif key == "target_port":
                        self._target_port = value
                    elif key == "invoke_path":
                        self._invoke_path = value
                    elif key == "service_ttl":
                        self._service_ttl = value
                else:
                    logger.warning(f"Unknown config key: key='{key}'")

            if "min_idle_services" in kwargs:
                await self._ensure_min_idle_services()

    async def handle_message(self, message: IMessage) -> None:
        logger.debug(
            f"Handling message: session_id='{message.get_session_id()}', "
            f"priority={message.get_priority()}"
        )

        payload = message.get_payload()
        task_type = payload.get("task_type") if isinstance(payload, dict) else None

        if task_type:
            await self._handle_task_request(message)
        else:
            await self._handle_user_request(message)

    async def _handle_user_request(self, message: IMessage) -> None:
        logger.debug(f"Handling user request: session_id='{message.get_session_id()}'")

        session_id = message.get_session_id()
        deployment_id = await self._get_available_service(
            session_id, message.get_session_concurrency()
        )

        if deployment_id:
            service_handler = self._services[deployment_id]
            await service_handler.add_session(
                session_id, message.get_session_concurrency(), message.get_session_ttl()
            )
            self._session_router.register_session(session_id, deployment_id)
            await self._refresh_timer(deployment_id)
            await service_handler.handle_message(message)
            logger.info(
                f"User request assigned to existing service: "
                f"session_id='{session_id}', deployment_id='{deployment_id}'"
            )
        else:
            if len(self._services) >= self._max_services:
                logger.warning(
                    f"Cannot create new service for user request: max_services limit reached"
                )
                return

            try:
                new_deployment_id = await self.deploy_service()
                service_handler = self._services[new_deployment_id]
                await service_handler.add_session(
                    message.get_session_id(), message.get_session_concurrency(), message.get_session_ttl()
                )
                await service_handler.handle_message(message)
                logger.info(
                    f"User request assigned to new service: "
                    f"session_id='{message.get_session_id()}', deployment_id='{new_deployment_id}'"
                )
            except Exception as e:
                logger.error(
                    f"Failed to create service for user request: error={e}"
                )

    async def _handle_task_request(self, message: IMessage) -> None:
        logger.debug(f"Handling task request: session_id='{message.get_session_id()}'")

        payload = message.get_payload()
        task_type = payload.get("task_type") if isinstance(payload, dict) else None

        if task_type == "scale_down":
            deployment_id = payload.get("deployment_id")
            if deployment_id:
                await self._scale_down(deployment_id)
        elif task_type == "config_update":
            config_updates = payload.get("config", {})
            await self.update_config(**config_updates)
        elif task_type == "response_end":
            session_id = payload.get("session_id")
            if session_id:
                await self._handle_response_end(session_id)
        else:
            logger.warning(f"Unknown task type: task_type='{task_type}'")

    async def _handle_response_end(self, session_id: str) -> None:
        """处理响应结束"""
        logger.info(f"Response end for session: session_id='{session_id}'")
        deployment_id = self._session_router.get_session_service(session_id)
        if deployment_id and deployment_id in self._services:
            service_handler = self._services[deployment_id]
            await service_handler.remove_session(session_id)
            self._session_router.unregister_session(session_id)

    async def _scale_down(self, deployment_id: str) -> None:
        logger.info(f"Scaling down service: deployment_id='{deployment_id}'")

        if deployment_id not in self._services:
            logger.warning(f"Service not found for scale down: deployment_id='{deployment_id}'")
            return

        service_handler = self._services[deployment_id]
        if service_handler.state == ServiceState.RUNNING:
            logger.debug(
                f"Service has active sessions, marking for scale down: "
                f"deployment_id='{deployment_id}'"
            )
            return

        await self.stop_service(deployment_id)

    async def _refresh_timer(self, deployment_id: str) -> None:
        if deployment_id not in self._services:
            return

        await self._timer.cancel_timer(f"service_{deployment_id}")
        await self._timer.start_timer(
            f"service_{deployment_id}",
            self._service_ttl,
            lambda: self._on_service_timeout(deployment_id),
        )
        logger.debug(f"Timer refreshed for service: deployment_id='{deployment_id}'")
