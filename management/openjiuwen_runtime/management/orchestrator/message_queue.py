# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""异步优先级消息队列模块"""

import asyncio
import heapq
import logging
from typing import Optional

from .interfaces import IMessageQueue
from .models import Message, MessagePriority

logger = logging.getLogger(__name__)


class InMemoryMessageQueue(IMessageQueue):
    """基于内存的异步优先级消息队列"""

    def __init__(self, max_size: int = 100):
        self._max_size = max_size
        self._queue: list[tuple[int, int, Message]] = []
        self._counter = 0
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Condition()
        self._closed = False

    def _priority_value(self, priority: MessagePriority) -> int:
        priority_map = {
            MessagePriority.HIGH: 0,
            MessagePriority.MEDIUM: 1,
            MessagePriority.LOW: 2,
        }
        return priority_map.get(priority, 1)

    async def put(self, message: Message) -> None:
        if self._closed:
            raise RuntimeError("Queue is closed")

        async with self._not_empty:
            if len(self._queue) >= self._max_size:
                logger.warning(f"Queue is full (size={self._max_size}), waiting...")
                while len(self._queue) >= self._max_size and not self._closed:
                    await asyncio.sleep(0.01)

            if self._closed:
                raise RuntimeError("Queue is closed")

            priority_val = self._priority_value(message.priority)
            self._counter += 1
            heapq.heappush(self._queue, (priority_val, self._counter, message))
            logger.debug(f"Message put into queue, priority={message.priority}, queue_size={len(self._queue)}")
            self._not_empty.notify()

    async def get(self) -> Message:
        if self._closed and len(self._queue) == 0:
            raise RuntimeError("Queue is closed and empty")

        async with self._not_empty:
            while len(self._queue) == 0 and not self._closed:
                await self._not_empty.wait()

            if len(self._queue) == 0:
                raise RuntimeError("Queue is closed and empty")

            _, _, message = heapq.heappop(self._queue)
            logger.debug(f"Message get from queue, queue_size={len(self._queue)}")
            return message

    async def close(self) -> None:
        async with self._not_empty:
            self._closed = True
            self._not_empty.notify_all()
        logger.info("InMemoryMessageQueue closed")

    async def size(self) -> int:
        return len(self._queue)

    async def is_full(self) -> bool:
        return len(self._queue) >= self._max_size


class ZmqMessageQueue(IMessageQueue):
    """基于 ZMQ 的异步消息队列"""

    def __init__(self, bind_address: str = "tcp://*:5555", max_size: int = 100):
        self._bind_address = bind_address
        self._max_size = max_size
        self._context: Optional[object] = None
        self._socket: Optional[object] = None
        self._closed = False
        self._queue: InMemoryMessageQueue = InMemoryMessageQueue(max_size=max_size)

    async def _ensure_connection(self) -> None:
        if self._socket is None:
            try:
                import zmq.asyncio

                self._context = zmq.asyncio.Context()
                self._socket = self._context.socket(zmq.PULL)
                self._socket.set_hwm(self._max_size)
                self._socket.bind(self._bind_address)
                logger.info(f"ZMQ socket bound to {self._bind_address}")
            except ImportError:
                logger.error("zmq library not installed, falling back to in-memory queue")
                self._socket = None

    async def put(self, message: Message) -> None:
        if self._closed:
            raise RuntimeError("Queue is closed")

        await self._queue.put(message)

    async def get(self) -> Message:
        if self._closed:
            raise RuntimeError("Queue is closed")

        return await self._queue.get()

    async def close(self) -> None:
        self._closed = True
        await self._queue.close()
        if self._socket is not None:
            self._socket.close()
            logger.info("ZMQ socket closed")
        if self._context is not None:
            self._context.term()
            logger.info("ZMQ context terminated")

    async def size(self) -> int:
        return await self._queue.size()

    async def is_full(self) -> bool:
        return await self._queue.is_full()


class RabbitMqMessageQueue(IMessageQueue):
    """基于 RabbitMQ 的异步消息队列"""

    def __init__(
        self,
        url: str = "amqp://localhost",
        queue_name: str = "orchestrator",
        max_size: int = 100,
    ):
        self._url = url
        self._queue_name = queue_name
        self._max_size = max_size
        self._connection: Optional[object] = None
        self._channel: Optional[object] = None
        self._queue: Optional[object] = None
        self._closed = False
        self._fallback_queue: InMemoryMessageQueue = InMemoryMessageQueue(max_size=max_size)
        self._use_fallback = True

    async def _ensure_connection(self) -> None:
        if self._connection is not None:
            return

        try:
            import aio_pika

            self._connection = await aio_pika.connect_robust(self._url)
            self._channel = await self._connection.channel()
            self._queue = await self._channel.declare_queue(
                self._queue_name,
                arguments={"x-max-length": self._max_size},
            )
            self._use_fallback = False
            logger.info(f"RabbitMQ connected to {self._url}, queue={self._queue_name}")
        except ImportError:
            logger.error("aio_pika library not installed, falling back to in-memory queue")
            self._use_fallback = True
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}, falling back to in-memory queue")
            self._use_fallback = True

    async def put(self, message: Message) -> None:
        if self._closed:
            raise RuntimeError("Queue is closed")

        await self._ensure_connection()

        if self._use_fallback:
            await self._fallback_queue.put(message)
            return

        try:
            import aio_pika

            message_body = message.model_dump_json()
            await self._channel.default_exchange.publish(
                aio_pika.Message(body=message_body.encode()),
                routing_key=self._queue_name,
            )
            logger.debug(f"Message published to RabbitMQ queue {self._queue_name}")
        except Exception as e:
            logger.error(f"Failed to publish message to RabbitMQ: {e}")
            await self._fallback_queue.put(message)

    async def get(self) -> Message:
        if self._closed:
            raise RuntimeError("Queue is closed")

        await self._ensure_connection()

        if self._use_fallback:
            return await self._fallback_queue.get()

        try:
            async with self._queue.iterator() as queue_iter:
                async for message in queue_iter:
                    async with message.process():
                        message_body = message.body.decode()
                        return Message.model_validate_json(message_body)
        except Exception as e:
            logger.error(f"Failed to get message from RabbitMQ: {e}")
            return await self._fallback_queue.get()

    async def close(self) -> None:
        self._closed = True
        await self._fallback_queue.close()
        if self._connection is not None:
            await self._connection.close()
            logger.info("RabbitMQ connection closed")

    async def size(self) -> int:
        if self._use_fallback:
            return await self._fallback_queue.size()

        try:
            if self._queue is not None:
                return self._queue.declaration_result.message_count
        except Exception:
            pass
        return await self._fallback_queue.size()

    async def is_full(self) -> bool:
        if self._use_fallback:
            return await self._fallback_queue.is_full()

        size = await self.size()
        return size >= self._max_size
