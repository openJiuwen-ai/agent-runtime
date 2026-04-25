#!/usr/bin/env python3
# coding: utf-8

"""RabbitMQ 测试统一入口（orchestrator）。

支持测试场景：
1) queue: RabbitMqMessageQueue 收发与订阅生命周期
2) access: Access(queue_backend=rabbitmq, consume_from_broker=True) 消费链路
3) config_format: config_broadcast 消息格式验证（仅 RabbitMQ 发/收）
4) suite: 依次执行 queue + access + config_format
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4


def _bootstrap_env() -> None:
    # 避免导入 runtime 模块时触发 Settings 校验失败
    os.environ.setdefault("IP", "127.0.0.1")
    os.environ.setdefault("LOWCODE_IMAGE", "smoke-image:latest")


_bootstrap_env()

from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.management.orchestrator.access import Access, AccessConfig
from openjiuwen_runtime.management.orchestrator.message_queue import RabbitMqMessageQueue
from openjiuwen_runtime.management.orchestrator.models import Message, MessagePriority

# 测试场景："queue", "access", "config_format", "suite"
DEFAULT_TEST_CASE = "config_format"
DEFAULT_RABBITMQ_URL = "amqp://runtime:Poisson%40123@localhost:5672/"
DEFAULT_RABBITMQ_QUEUE = "orchestrator"
DEFAULT_QUEUE_TIMEOUT_SECONDS = 8.0
DEFAULT_ACCESS_TIMEOUT_SECONDS = 10.0
DEFAULT_ACCESS_DB_PATH = ".tmp/access_rabbitmq_smoke.db"


@dataclass
class RuntimeRabbitmqTestConfig:
    rabbitmq_url: str
    queue_name: str
    queue_timeout_seconds: float = 8.0
    access_timeout_seconds: float = 10.0
    access_db_path: str = ".tmp/access_rabbitmq_smoke.db"


async def run_queue_smoke(cfg: RuntimeRabbitmqTestConfig) -> int:
    queue = RabbitMqMessageQueue(url=cfg.rabbitmq_url, queue_name=cfg.queue_name, max_size=100)
    got_message = asyncio.Event()
    message_count = 0
    consumed_message: Optional[Message] = None

    async def _handler(msg: Message) -> None:
        nonlocal message_count
        nonlocal consumed_message
        message_count += 1
        consumed_message = msg
        print(f"[queue.consume] session_id={msg.session_id}, request_id={msg.request_id}, payload={msg.payload}", flush=True)
        got_message.set()

    print(f"[queue.start] url={cfg.rabbitmq_url}, queue={cfg.queue_name}", flush=True)
    started = await queue.start_consume(_handler)
    if not started:
        print("[queue.error] RabbitMQ consumer 未启动（fallback mode）", file=sys.stderr, flush=True)
        await queue.close()
        return 2

    msg = Message(
        session_id="smoke-session",
        request_id="smoke-request-1",
        concurrency=1,
        ttl=60,
        priority=MessagePriority.MEDIUM,
        payload={"type": "smoke", "message": "hello runtime rabbitmq"},
        is_complete=False,
    )
    await queue.put(msg)

    try:
        await asyncio.wait_for(got_message.wait(), timeout=cfg.queue_timeout_seconds)
    except asyncio.TimeoutError:
        print(f"[queue.error] timeout after {cfg.queue_timeout_seconds}s", file=sys.stderr, flush=True)
        await queue.stop_consume()
        await queue.close()
        return 1

    ok = consumed_message is not None and consumed_message.session_id == msg.session_id and consumed_message.request_id == msg.request_id
    await queue.stop_consume()
    await queue.stop_consume()

    # 验证 stop_consume 生效：停订阅后再次投递不应触发 handler
    got_message.clear()
    await queue.put(
        Message(
            session_id="smoke-session-after-stop",
            request_id="smoke-request-after-stop",
            concurrency=1,
            ttl=60,
            priority=MessagePriority.MEDIUM,
            payload={"type": "smoke", "message": "should not be consumed"},
            is_complete=False,
        )
    )
    await asyncio.sleep(1.0)
    if got_message.is_set() or message_count != 1:
        print("[queue.error] stop_consume 后仍有消息被消费", file=sys.stderr, flush=True)
        await queue.close()
        return 1

    await queue.close()
    if not ok:
        print("[queue.error] 消费消息校验失败", file=sys.stderr, flush=True)
        return 1
    print("[queue.ok] rabbitmq queue smoke passed", flush=True)
    return 0


async def run_access_smoke(cfg: RuntimeRabbitmqTestConfig) -> int:
    db_file = Path(cfg.access_db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_handler = SQLiteHandler(str(db_file))

    access = Access(
        AccessConfig(
            db_handler=db_handler,
            image="smoke-image:latest",
            queue_backend="rabbitmq",
            rabbitmq_url=cfg.rabbitmq_url,
            rabbitmq_queue_name=cfg.queue_name,
            consume_from_broker=True,
            queue_size=100,
        )
    )
    producer = RabbitMqMessageQueue(url=cfg.rabbitmq_url, queue_name=cfg.queue_name, max_size=100)
    access_stopped = False

    got_message = asyncio.Event()
    consume_counter = {"count": 0}
    consumed = {"session_id": "", "request_id": ""}

    try:
        await access.init()
        if access._service_manager is None:
            print("[access.error] service_manager not initialized", file=sys.stderr, flush=True)
            return 2

        async def _mock_handle_message(message):
            consume_counter["count"] += 1
            consumed["session_id"] = message.get_session_id()
            consumed["request_id"] = message.get_request_id() or ""
            print(
                f"[access.consume] session_id={consumed['session_id']}, request_id={consumed['request_id']}",
                flush=True,
            )
            got_message.set()

        access._service_manager.handle_message = _mock_handle_message  # type: ignore[method-assign]

        msg = Message(
            session_id="access-smoke-session",
            request_id="access-smoke-request-1",
            concurrency=1,
            ttl=60,
            priority=MessagePriority.MEDIUM,
            payload={"type": "smoke", "message": "hello access rabbitmq"},
            is_complete=False,
        )
        await producer.put(msg)

        try:
            await asyncio.wait_for(got_message.wait(), timeout=cfg.access_timeout_seconds)
        except asyncio.TimeoutError:
            print(f"[access.error] timeout after {cfg.access_timeout_seconds}s", file=sys.stderr, flush=True)
            return 1

        if consumed["session_id"] != msg.session_id or consumed["request_id"] != msg.request_id:
            print("[access.error] 消费结果校验失败", file=sys.stderr, flush=True)
            return 1

        await access.stop()
        access_stopped = True
        got_message.clear()

        # 验证 Access.stop 生效：停服后 broker 消息不应再进入 handle_message
        await producer.put(
            Message(
                session_id="access-smoke-after-stop",
                request_id="access-smoke-after-stop-1",
                concurrency=1,
                ttl=60,
                priority=MessagePriority.MEDIUM,
                payload={"type": "smoke", "message": "should not be consumed by stopped access"},
                is_complete=False,
            )
        )
        await asyncio.sleep(1.0)
        if got_message.is_set() or consume_counter["count"] != 1:
            print("[access.error] access.stop 后仍有消息被消费", file=sys.stderr, flush=True)
            return 1

        print("[access.ok] access rabbitmq smoke passed", flush=True)
        return 0
    finally:
        await producer.close()
        if not access_stopped:
            await access.stop()


async def run_config_format_smoke(cfg: RuntimeRabbitmqTestConfig) -> int:
    """验证 config_broadcast 消息格式 """
    queue_name = f"{cfg.queue_name}.config_format"
    try:
        import aio_pika
    except ImportError:
        print("[config_format.error] aio_pika 未安装", file=sys.stderr, flush=True)
        return 2

    connection = await aio_pika.connect_robust(cfg.rabbitmq_url)
    channel = await connection.channel()
    queue = await channel.declare_queue(queue_name, auto_delete=True)
    request_id = f"cfg-{uuid4().hex[:8]}"
    body = {
        "type": "config_broadcast",
        "request_id": request_id,
        "scope": "all",
        "payload": {
            "model_config": {
                "MODEL_PROVIDER": "OpenAI",
                "MODEL_NAME": "gpt-4.1",
            },
            "channel_config": {
                "feishu": {
                    "enabled": True,
                    "app_id": "demo-app-id",
                    "app_secret": "demo-app-secret",
                }
            },
        },
    }

    try:
        await channel.default_exchange.publish(
            aio_pika.Message(body=json.dumps(body, ensure_ascii=False).encode()),
            routing_key=queue_name,
        )
        incoming = await queue.get(timeout=cfg.queue_timeout_seconds)
        async with incoming.process():
            received = json.loads(incoming.body.decode())

        if received.get("type") != "config_broadcast":
            print("[config_format.error] type 字段错误", file=sys.stderr, flush=True)
            return 1
        if received.get("request_id") != request_id:
            print("[config_format.error] request_id 不匹配", file=sys.stderr, flush=True)
            return 1
        if received.get("scope") not in {"model", "channel", "all"}:
            print("[config_format.error] scope 字段非法", file=sys.stderr, flush=True)
            return 1

        payload = received.get("payload")
        if not isinstance(payload, dict):
            print("[config_format.error] payload 必须是对象", file=sys.stderr, flush=True)
            return 1
        if not isinstance(payload.get("model_config"), dict):
            print("[config_format.error] payload.model_config 必须是对象", file=sys.stderr, flush=True)
            return 1
        if not isinstance(payload.get("channel_config"), dict):
            print("[config_format.error] payload.channel_config 必须是对象", file=sys.stderr, flush=True)
            return 1

        print(
            f"[config_format.ok] request_id={request_id}, scope={received.get('scope')}, queue={queue_name}",
            flush=True,
        )
        return 0
    except asyncio.TimeoutError:
        print(f"[config_format.error] timeout after {cfg.queue_timeout_seconds}s", file=sys.stderr, flush=True)
        return 1
    finally:
        await queue.delete(if_unused=False, if_empty=False)
        await channel.close()
        await connection.close()


async def run_suite(cfg: RuntimeRabbitmqTestConfig) -> int:
    print("=== Running queue smoke ===", flush=True)
    code = await run_queue_smoke(cfg)
    if code != 0:
        print(f"=== queue smoke FAIL (code={code}) ===", flush=True)
        return code

    print("=== Running access smoke ===", flush=True)
    code = await run_access_smoke(cfg)
    if code != 0:
        print(f"=== access smoke FAIL (code={code}) ===", flush=True)
        return code

    print("=== Running config format smoke ===", flush=True)
    code = await run_config_format_smoke(cfg)
    if code != 0:
        print(f"=== config format smoke FAIL (code={code}) ===", flush=True)
        return code

    print("[suite.ok] rabbitmq suite passed", flush=True)
    return 0


async def _run(case: str, cfg: RuntimeRabbitmqTestConfig) -> int:
    if case == "queue":
        return await run_queue_smoke(cfg)
    if case == "access":
        return await run_access_smoke(cfg)
    if case == "config_format":
        return await run_config_format_smoke(cfg)
    return await run_suite(cfg)


def main() -> int:
    # 以 SDK 被业务方调用的方式模拟配置注入
    case = os.getenv("RABBITMQ_TEST_CASE", DEFAULT_TEST_CASE)
    rabbitmq_url = os.getenv("RABBITMQ_URL", DEFAULT_RABBITMQ_URL)
    queue_name = os.getenv("RABBITMQ_QUEUE", DEFAULT_RABBITMQ_QUEUE)
    queue_timeout = float(os.getenv("RABBITMQ_QUEUE_TIMEOUT", str(DEFAULT_QUEUE_TIMEOUT_SECONDS)))
    access_timeout = float(os.getenv("RABBITMQ_ACCESS_TIMEOUT", str(DEFAULT_ACCESS_TIMEOUT_SECONDS)))
    access_db_path = os.getenv("RABBITMQ_ACCESS_DB_PATH", DEFAULT_ACCESS_DB_PATH)

    if case not in {"queue", "access", "config_format", "suite"}:
        print(f"[error] invalid RABBITMQ_TEST_CASE: {case}", file=sys.stderr, flush=True)
        return 2

    cfg = RuntimeRabbitmqTestConfig(
        rabbitmq_url=rabbitmq_url,
        queue_name=queue_name,
        queue_timeout_seconds=queue_timeout,
        access_timeout_seconds=access_timeout,
        access_db_path=access_db_path,
    )
    return asyncio.run(_run(case, cfg))


if __name__ == "__main__":
    raise SystemExit(main())
