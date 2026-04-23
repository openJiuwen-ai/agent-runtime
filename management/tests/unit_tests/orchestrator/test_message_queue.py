# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""InMemoryMessageQueue 单元测试"""

import pytest
import asyncio

from openjiuwen_runtime.management.orchestrator.message_queue import InMemoryMessageQueue
from openjiuwen_runtime.management.orchestrator.models import Message, MessagePriority


class TestInMemoryMessageQueue:
    """测试 InMemoryMessageQueue 类"""

    @pytest.fixture
    def queue(self):
        """创建 InMemoryMessageQueue 实例"""
        return InMemoryMessageQueue(max_size=3)

    @pytest.fixture
    def sample_message(self):
        """创建示例消息"""
        return Message(
            session_id="test-session",
            concurrency=1,
            ttl=30,
            priority=MessagePriority.MEDIUM,
            payload={"data": "test"},
        )

    def _create_message(self, session_id: str, priority: MessagePriority) -> Message:
        """创建消息的辅助方法"""
        return Message(
            session_id=session_id,
            concurrency=1,
            ttl=30,
            priority=priority,
            payload={"data": f"test_{session_id}"},
        )

    @pytest.mark.asyncio
    async def test_put_and_get(self, queue, sample_message):
        """测试放入和获取消息"""
        await queue.put(sample_message)

        size = await queue.size()
        assert size == 1

        message = await queue.get()

        assert message.session_id == sample_message.session_id
        assert message.priority == sample_message.priority
        assert message.payload == sample_message.payload

        size = await queue.size()
        assert size == 0

    @pytest.mark.asyncio
    async def test_close(self, queue):
        """测试关闭队列"""
        await queue.close()

        with pytest.raises(RuntimeError, match="Queue is closed"):
            await queue.put(
                Message(
                    session_id="test",
                    concurrency=1,
                    ttl=30,
                    priority=MessagePriority.MEDIUM,
                    payload={},
                )
            )

    @pytest.mark.asyncio
    async def test_close_with_empty_queue(self, queue):
        """测试关闭空队列后获取消息"""
        await queue.close()

        with pytest.raises(RuntimeError, match="Queue is closed and empty"):
            await queue.get()

    @pytest.mark.asyncio
    async def test_size(self, queue, sample_message):
        """测试获取队列大小"""
        assert await queue.size() == 0

        await queue.put(sample_message)
        assert await queue.size() == 1

        await queue.put(sample_message)
        assert await queue.size() == 2

        await queue.get()
        assert await queue.size() == 1

    @pytest.mark.asyncio
    async def test_is_full(self, queue, sample_message):
        """测试队列是否已满"""
        assert await queue.is_full() is False

        await queue.put(sample_message)
        assert await queue.is_full() is False

        await queue.put(sample_message)
        assert await queue.is_full() is False

        await queue.put(sample_message)
        assert await queue.is_full() is True

    @pytest.mark.asyncio
    async def test_priority_ordering(self, queue):
        """测试优先级排序（高优先级先出队）"""
        low_msg = self._create_message("low", MessagePriority.LOW)
        high_msg = self._create_message("high", MessagePriority.HIGH)
        medium_msg = self._create_message("medium", MessagePriority.MEDIUM)

        await queue.put(low_msg)
        await queue.put(high_msg)
        await queue.put(medium_msg)

        first = await queue.get()
        assert first.session_id == "high"
        assert first.priority == MessagePriority.HIGH

        second = await queue.get()
        assert second.session_id == "medium"
        assert second.priority == MessagePriority.MEDIUM

        third = await queue.get()
        assert third.session_id == "low"
        assert third.priority == MessagePriority.LOW

    @pytest.mark.asyncio
    async def test_priority_ordering_same_priority(self, queue):
        """测试相同优先级的 FIFO 顺序"""
        msg1 = self._create_message("msg1", MessagePriority.MEDIUM)
        msg2 = self._create_message("msg2", MessagePriority.MEDIUM)
        msg3 = self._create_message("msg3", MessagePriority.MEDIUM)

        await queue.put(msg1)
        await queue.put(msg2)
        await queue.put(msg3)

        first = await queue.get()
        assert first.session_id == "msg1"

        second = await queue.get()
        assert second.session_id == "msg2"

        third = await queue.get()
        assert third.session_id == "msg3"

    @pytest.mark.asyncio
    async def test_queue_full_blocking(self, queue):
        """测试队列满时阻塞"""
        for i in range(3):
            await queue.put(
                self._create_message(f"msg{i}", MessagePriority.MEDIUM)
            )

        assert await queue.is_full() is True

        put_completed = asyncio.Event()
        put_task = asyncio.create_task(
            self._test_put_with_event(queue, "msg_extra", put_completed)
        )

        await asyncio.sleep(0.1)
        assert put_completed.is_set() is False

        await queue.get()

        await asyncio.wait_for(put_task, timeout=1.0)
        assert put_completed.is_set() is True

    async def _test_put_with_event(
        self, queue: InMemoryMessageQueue, session_id: str, event: asyncio.Event
    ):
        """辅助方法：放入消息并设置事件"""
        msg = self._create_message(session_id, MessagePriority.MEDIUM)
        await queue.put(msg)
        event.set()

    @pytest.mark.asyncio
    async def test_get_waits_for_message(self, queue):
        """测试获取消息时等待"""
        get_completed = asyncio.Event()

        async def get_message():
            msg = await queue.get()
            get_completed.set()
            return msg

        get_task = asyncio.create_task(get_message())

        await asyncio.sleep(0.1)
        assert get_completed.is_set() is False

        await queue.put(self._create_message("test", MessagePriority.MEDIUM))

        await asyncio.wait_for(get_task, timeout=1.0)
        assert get_completed.is_set() is True

    @pytest.mark.asyncio
    async def test_close_unblocks_waiting_get(self, queue):
        """测试关闭队列解除等待中的 get"""
        get_task = asyncio.create_task(queue.get())

        await asyncio.sleep(0.1)

        await queue.close()

        with pytest.raises(RuntimeError, match="Queue is closed and empty"):
            await get_task

    @pytest.mark.asyncio
    async def test_multiple_producers_consumers(self, queue):
        """测试多生产者多消费者场景"""
        num_messages = 10
        produced = []
        consumed = []

        async def producer(start: int):
            for i in range(start, start + num_messages // 2):
                msg = self._create_message(f"msg_{i}", MessagePriority.MEDIUM)
                await queue.put(msg)
                produced.append(f"msg_{i}")

        async def consumer():
            for _ in range(num_messages // 2):
                msg = await queue.get()
                consumed.append(msg.session_id)

        await asyncio.gather(
            producer(0),
            producer(num_messages // 2),
            consumer(),
            consumer(),
        )

        assert len(produced) == num_messages
        assert len(consumed) == num_messages
        assert set(produced) == set(consumed)
