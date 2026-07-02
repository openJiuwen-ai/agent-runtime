# coding: utf-8
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
