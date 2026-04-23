# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""ServiceHandler 单元测试"""

import pytest

from openjiuwen_runtime.management.orchestrator.timer import Timer
from openjiuwen_runtime.management.orchestrator.models import Message, MessagePriority


class TestServiceHandler:
    """测试 ServiceHandler 类"""

    @pytest.fixture
    def timer(self):
        """创建 Timer 实例"""
        return Timer()

    @pytest.fixture
    def service_handler(self, timer):
        """创建 ServiceHandler 实例"""
        from openjiuwen_runtime.management.orchestrator.service_handler import ServiceHandler
        return ServiceHandler(
            deployment_id="test-deployment",
            max_concurrency=10,
            service_ttl=300,
            timer=timer,
        )

    @pytest.mark.asyncio
    async def test_add_session(self, service_handler):
        """测试添加 session"""
        await service_handler.add_session("session1", 2, 30)

        assert await service_handler.get_session_count() == 1
        assert await service_handler.has_capacity(8) is True

    @pytest.mark.asyncio
    async def test_add_session_exceeds_capacity(self, service_handler):
        """测试超过容量时添加 session 失败"""
        await service_handler.add_session("session1", 6, 30)
        await service_handler.add_session("session2", 5, 30)

        assert await service_handler.get_session_count() == 1

    @pytest.mark.asyncio
    async def test_remove_session(self, service_handler):
        """测试移除 session"""
        await service_handler.add_session("session1", 2, 30)
        assert await service_handler.get_session_count() == 1

        await service_handler.remove_session("session1")
        assert await service_handler.get_session_count() == 0

    @pytest.mark.asyncio
    async def test_remove_session_not_found(self, service_handler):
        """测试移除不存在的 session"""
        await service_handler.remove_session("non_existent")
        assert await service_handler.get_session_count() == 0

    @pytest.mark.asyncio
    async def test_handle_message(self, service_handler):
        """测试处理消息"""
        await service_handler.add_session("session1", 1, 30)

        message = Message(
            session_id="session1",
            concurrency=1,
            ttl=30,
            priority=MessagePriority.MEDIUM,
            payload={"data": "test"},
        )

        await service_handler.handle_message(message)

    @pytest.mark.asyncio
    async def test_has_capacity(self, service_handler):
        """测试容量检查"""
        assert await service_handler.has_capacity(10) is True
        assert await service_handler.has_capacity(11) is False

        await service_handler.add_session("session1", 5, 30)
        assert await service_handler.has_capacity(5) is True
        assert await service_handler.has_capacity(6) is False

    @pytest.mark.asyncio
    async def test_get_session_count(self, service_handler):
        """测试获取 session 数量"""
        assert await service_handler.get_session_count() == 0

        await service_handler.add_session("session1", 1, 30)
        assert await service_handler.get_session_count() == 1

        await service_handler.add_session("session2", 1, 30)
        assert await service_handler.get_session_count() == 2

    @pytest.mark.asyncio
    async def test_write_to_session(self, service_handler):
        """测试写入 session 消息队列"""
        await service_handler.add_session("session1", 1, 30)

        message = Message(
            session_id="session1",
            concurrency=1,
            ttl=30,
            priority=MessagePriority.MEDIUM,
            payload={"data": "test"},
        )

        await service_handler.write_to_session("session1", message)

        result = await service_handler.get_session_from_queue("session1")
        assert result is not None
        assert result.session_id == "session1"

    @pytest.mark.asyncio
    async def test_get_pending_request_count(self, service_handler):
        """测试获取待处理请求数量"""
        await service_handler.add_session("session1", 1, 30)

        count = await service_handler.get_pending_request_count("session1")
        assert count == 0

        count = await service_handler.get_pending_request_count("non_existent")
        assert count == 0

    @pytest.mark.asyncio
    async def test_cancel_request(self, service_handler):
        """测试取消请求"""
        await service_handler.add_session("session1", 1, 30)

        result = await service_handler.cancel_request("session1", "non_existent")
        assert result is False

        result = await service_handler.cancel_request("non_existent", "request1")
        assert result is False
