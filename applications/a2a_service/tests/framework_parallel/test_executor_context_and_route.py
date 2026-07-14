# coding: utf-8
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import Message, Part, ROLE_USER

from orchestrator.handlers.requester_handler import RequesterHandler
from tests.framework_parallel._helpers import data_part, make_executor


def _context_with_session():
    return SimpleNamespace(
        context_id="child-conv",
        task_id="task-1",
        call_context=None,
        current_task=None,
        message=Message(
            role=ROLE_USER,
            message_id="m-1",
            parts=[
                Part(text="hello"),
                data_part(
                    {
                        "session_context": {
                            "headers": {"Authorization": "Bearer token"},
                            "params": {"workspace_id": "ws"},
                            "trace_id": "trace-1",
                            "body": {"input": {"account": "001"}},
                            "sub_task_path": ["A", "wf:a"],
                        }
                    }
                ),
            ],
        ),
    )


def test_extract_request_info_reads_sub_task_path():
    executor = make_executor()

    query, headers, body, path = executor._extract_request_info(_context_with_session())

    assert query == "hello"
    assert headers == {}
    assert body == {
        "session_context": {
            "headers": {"Authorization": "Bearer token"},
            "params": {"workspace_id": "ws"},
            "trace_id": "trace-1",
            "body": {"input": {"account": "001"}},
            "sub_task_path": ["A", "wf:a"],
        }
    }
    assert path == ("A", "wf:a")


async def test_init_session_context_writes_redis_when_missing():
    executor = make_executor(redis_json=None)
    executor._redis.get_json = AsyncMock(return_value=None)
    executor._redis.set_json = AsyncMock()

    await executor._init_session_context_if_needed(_context_with_session())

    executor._redis.set_json.assert_awaited_once()
    _, payload = executor._redis.set_json.await_args.args[:2]
    assert payload["headers"] == {"Authorization": "Bearer token"}
    assert payload["params"] == {"workspace_id": "ws"}
    assert payload["trace_id"] == "trace-1"
    assert payload["body"] == {"input": {"account": "001"}}


def test_route_dispatcher_exposes_configured_handler_instance():
    executor = make_executor()
    requester = RequesterHandler(executor._state_manager)
    executor._route_dispatcher._handler_instances["requester"] = requester

    assert executor._route_dispatcher.get_handler_instance("requester") is requester


async def test_cancel_task_uses_route_dispatcher_remote_handler_when_not_cached():
    executor = make_executor()
    remote_handler = executor._test_remote_handler
    remote_handler.cancel_task = AsyncMock()
    executor._remote_handler = None
    delattr(executor, "_test_remote_handler")
    executor._route_dispatcher._handler_instances["remote_agent"] = remote_handler
    executor._state_manager.update_task_status = AsyncMock()
    executor._state_manager.get_task = AsyncMock(return_value=None)
    executor._redis.get = MagicMock(return_value="")

    await executor.cancel_task("conv-1")

    remote_handler.cancel_task.assert_awaited_once_with("conv-1", [])


async def test_execute_reuses_session_heartbeat_runtime_until_terminal():
    executor = make_executor()
    conv_id = "conv-heartbeat"
    task_id = "task-heartbeat"
    runtimes = []

    async def _dispatch_stub(_event, handler_context):
        runtimes.append(handler_context.get("heartbeat_runtime"))

    executor._route_dispatcher.dispatch = _dispatch_stub
    executor._state_manager.get_task = AsyncMock(
        side_effect=[
            {"id": task_id, "status_state": "INPUT_REQUIRED"},
            {"id": task_id, "status_state": "COMPLETED"},
        ]
    )

    context = RequestContext(
        call_context=ServerCallContext(),
        request=None,
        task_id=task_id,
        context_id=conv_id,
        task=None,
    )
    queue_first = EventQueue()
    queue_second = EventQueue()

    await executor.execute(context, queue_first)
    assert len(runtimes) == 1
    runtime = runtimes[0]
    assert runtime is not None
    assert executor._heartbeat_runtime_registry.get(conv_id) is runtime

    cleanup_mock = AsyncMock()
    runtime.cleanup = cleanup_mock  # type: ignore[attr-defined]

    await executor.execute(context, queue_second)

    assert len(runtimes) == 2
    assert runtimes[1] is runtime
    cleanup_mock.assert_awaited_once()
    assert conv_id not in executor._heartbeat_runtime_registry

    await queue_first.close()
    await queue_second.close()
