# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Access 单元测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from openjiuwen_runtime.management.orchestrator.models import Message, MessagePriority


class MockMessage:
    """Mock IMessage 实现"""
    
    def __init__(
        self,
        session_id: str = "test-session",
        request_id: str = None,
        concurrency: int = 1,
        ttl: int = 30,
        priority: MessagePriority = MessagePriority.MEDIUM,
        payload: dict = None,
        is_complete: bool = False,
    ):
        self._session_id = session_id
        self._request_id = request_id
        self._concurrency = concurrency
        self._ttl = ttl
        self._priority = priority
        self._payload = payload or {}
        self._is_complete = is_complete
        self._response_channel = None

    def get_session_id(self) -> str:
        return self._session_id

    def get_session_concurrency(self) -> int:
        return self._concurrency

    def get_session_ttl(self) -> int:
        return self._ttl

    def get_request_id(self):
        return self._request_id

    def get_payload(self):
        return self._payload

    def get_priority(self) -> MessagePriority:
        return self._priority

    def is_complete_msg(self) -> bool:
        return self._is_complete

    def get_response_channel(self):
        return self._response_channel


class TestAccess:
    """测试 Access 类"""

    @pytest.fixture
    def db_handler(self):
        """创建 mock DBHandler"""
        handler = MagicMock()
        handler.initialize = AsyncMock()
        handler.shutdown = AsyncMock()
        return handler

    @pytest.fixture
    def access_config(self, db_handler):
        """创建 AccessConfig 实例"""
        from openjiuwen_runtime.management.orchestrator.access import AccessConfig
        return AccessConfig(
            db_handler=db_handler,
            image="test-image:latest",
            max_concurrency=10,
            min_idle_services=0,
            max_services=5,
            target_port=8000,
            invoke_path="/invoke",
            service_ttl=300,
            queue_size=10,
        )

    @pytest.fixture
    def access(self, access_config):
        """创建 Access 实例"""
        from openjiuwen_runtime.management.orchestrator.access import Access
        return Access(access_config)

    @pytest.fixture
    def mock_message(self):
        """创建 Mock 消息"""
        return MockMessage(session_id="test-session")

    @pytest.mark.asyncio
    async def test_init(self, access):
        """测试初始化"""
        await access.init()

        assert access._deployment_manager is not None
        assert access._message_queue is not None
        assert access._timer is not None
        assert access._service_manager is not None

        await access.stop()

    @pytest.mark.asyncio
    async def test_send_message_missing_session_id(self, access):
        """测试发送消息缺少 session_id"""
        await access.init()

        msg = MockMessage(session_id="")
        result = await access.send_message(msg)

        assert result["success"] is False
        assert "session_id" in result["message"]

        await access.stop()

    @pytest.mark.asyncio
    async def test_send_message_with_imessage(self, access):
        """测试使用 IMessage 接口发送消息"""
        await access.init()

        msg = MockMessage(
            session_id="test-session",
            concurrency=2,
            ttl=60,
            payload={"data": "test"},
        )
        
        result = await access.send_message(msg)

        assert result["success"] is True
        assert result["session_id"] == "test-session"

        await access.stop()

    @pytest.mark.asyncio
    async def test_health_check(self, access):
        """测试健康检查"""
        await access.init()

        status = await access.health_check()

        assert "Access" in status
        assert "deployment_manager" in status
        assert "message_queue" in status
        assert "timer" in status
        assert "service_manager" in status

        await access.stop()

    @pytest.mark.asyncio
    async def test_stop(self, access):
        """测试停止"""
        await access.init()
        await access.stop()

        assert access._running is False

    @pytest.mark.asyncio
    async def test_set_image(self, access):
        """测试设置镜像"""
        await access.init()
        await access.set_image("new-image:latest")

        assert access._config.image == "new-image:latest"

        await access.stop()

    @pytest.mark.asyncio
    async def test_set_max_concurrency(self, access):
        """测试设置最大并发度"""
        await access.init()
        await access.set_max_concurrency(20)

        assert access._config.max_concurrency == 20

        await access.stop()

    @pytest.mark.asyncio
    async def test_set_min_idle_services(self, access):
        """测试设置最小空闲服务数"""
        await access.init()
        await access.set_min_idle_services(2)

        assert access._config.min_idle_services == 2

        await access.stop()

    @pytest.mark.asyncio
    async def test_set_max_services(self, access):
        """测试设置最大服务数"""
        await access.init()
        await access.set_max_services(10)

        assert access._config.max_services == 10

        await access.stop()

    @pytest.mark.asyncio
    async def test_set_service_ttl(self, access):
        """测试设置服务 TTL"""
        await access.init()
        await access.set_service_ttl(600)

        assert access._config.service_ttl == 600

        await access.stop()
