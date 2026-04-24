# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""ServiceManager 单元测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from openjiuwen_runtime.management.orchestrator.timer import Timer
from openjiuwen_runtime.management.orchestrator.message_queue import InMemoryMessageQueue
from openjiuwen_runtime.management.orchestrator.models import Message, MessagePriority


class TestServiceManager:
    """测试 ServiceManager 类"""

    @pytest.fixture
    def mock_deployment_manager(self):
        """创建 mock DeploymentManager"""
        manager = MagicMock()
        deployment_info = MagicMock()
        deployment_info.deployment_id = "test-deployment-1"
        manager.deploy_image = AsyncMock(return_value=deployment_info)
        manager.delete_deployment = AsyncMock(return_value=True)
        manager.list_deployments = AsyncMock(return_value=[])
        manager.initialize = AsyncMock()
        manager.shutdown = AsyncMock()
        return manager

    @pytest.fixture
    def timer(self):
        """创建 Timer 实例"""
        return Timer()

    @pytest.fixture
    def message_queue(self):
        """创建消息队列实例"""
        return InMemoryMessageQueue(max_size=10)

    @pytest.fixture
    def service_manager(self, mock_deployment_manager, timer, message_queue):
        """创建 ServiceManager 实例"""
        from openjiuwen_runtime.management.orchestrator.service_manager import ServiceManager
        return ServiceManager(
            deployment_manager=mock_deployment_manager,
            image="test-image:latest",
            max_concurrency=10,
            min_idle_services=1,
            max_services=5,
            target_port=8000,
            invoke_path="/invoke",
            service_ttl=300,
            timer=timer,
            message_queue=message_queue,
        )

    @pytest.mark.asyncio
    async def test_deploy_service(self, service_manager, mock_deployment_manager):
        """测试部署服务"""
        deployment_id = await service_manager.deploy_service()

        assert deployment_id is not None
        mock_deployment_manager.deploy_image.assert_called_once()
        
        service_handler = service_manager.services.get(deployment_id)
        assert service_handler is not None
        from openjiuwen_runtime.management.orchestrator.models import ServiceState
        assert service_handler.state == ServiceState.IDLE

    @pytest.mark.asyncio
    async def test_stop_service(self, service_manager, mock_deployment_manager):
        """测试停止服务"""
        deployment_id = await service_manager.deploy_service()

        result = await service_manager.stop_service(deployment_id)

        assert result is True
        mock_deployment_manager.delete_deployment.assert_called()
        assert deployment_id not in service_manager.services

    @pytest.mark.asyncio
    async def test_stop_service_not_found(self, service_manager):
        """测试停止不存在的服务"""
        result = await service_manager.stop_service("non-existent")
        assert result is False

    @pytest.mark.asyncio
    async def test_list_services(self, service_manager):
        """测试列出所有服务"""
        await service_manager.deploy_service()

        services = await service_manager.list_services()

        assert len(services) >= 1

    @pytest.mark.asyncio
    async def test_send_to_service(self, service_manager):
        """测试发送消息到服务"""
        deployment_id = await service_manager.deploy_service()

        message = Message(
            session_id="session1",
            concurrency=1,
            ttl=30,
            priority=MessagePriority.MEDIUM,
            payload={"data": "test"},
        )

        await service_manager.send_to_service(deployment_id, message)

    @pytest.mark.asyncio
    async def test_send_to_service_not_found(self, service_manager):
        """测试发送消息到不存在的服务"""
        message = Message(
            session_id="session1",
            concurrency=1,
            ttl=30,
            priority=MessagePriority.MEDIUM,
            payload={"data": "test"},
        )

        await service_manager.send_to_service("non-existent", message)

    @pytest.mark.asyncio
    async def test_update_config(self, service_manager):
        """测试更新配置"""
        await service_manager.update_config(max_concurrency=20, max_services=10)

        assert service_manager._max_concurrency == 20
        assert service_manager._max_services == 10

    @pytest.mark.asyncio
    async def test_deploy_service_state_flow(self, service_manager, mock_deployment_manager):
        """测试部署服务的状态流转"""
        from openjiuwen_runtime.management.orchestrator.models import ServiceState
        
        deployment_id = await service_manager.deploy_service()
        service_handler = service_manager.services[deployment_id]
        
        assert service_handler.state == ServiceState.IDLE

    @pytest.mark.asyncio
    async def test_max_services_limit(self, service_manager, mock_deployment_manager):
        """测试最大服务数限制"""
        for i in range(5):
            await service_manager.deploy_service()
        
        with pytest.raises(RuntimeError, match="Maximum services limit reached"):
            await service_manager.deploy_service()
