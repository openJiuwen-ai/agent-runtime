# coding: utf-8
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

from dataclasses import dataclass

import pytest

from orchestrator.route.normalized_event import NormalizedEvent, RouteContext
from orchestrator.route.route_profiles import (
    LocalAgentSourceProfile,
    RemoteAgentSourceProfile,
    RequesterSourceProfile,
)
from orchestrator.route.route_strategies import (
    LocalAgentSourceStrategy,
    RemoteAgentSourceStrategy,
    RequesterSourceStrategy,
)
from orchestrator.state.task_state_manager import (
    META_KEY_REMOTE_TASK_ID,
    META_KEY_SOURCE_AGENT,
    META_KEY_SUB_TASKS,
)


class _StateManager:
    def __init__(self, tasks: dict[str, dict]) -> None:
        self.tasks = tasks

    async def get_task(self, task_id: str):
        return self.tasks.get(task_id)


def _event(event_type: str = "message", *, metadata: dict | None = None, data: dict | None = None):
    return NormalizedEvent(type=event_type, data=data or {}, metadata=metadata or {})


def _context(**kwargs):
    defaults = {
        "source": "requester",
        "root_task_id": "root",
        "task_id": "root",
        "context_id": "conv",
        "is_specify_task": True,
    }
    defaults.update(kwargs)
    return RouteContext(**defaults)


@pytest.mark.asyncio
async def test_requester_strategy_routes_to_local_when_task_missing_or_not_suspended():
    strategy = RequesterSourceStrategy(
        _StateManager({"root": {"status_state": "WORKING", "metadata": {}}}),
        RequesterSourceProfile(suspended_states=["INPUT_REQUIRED"]),
    )

    assert (await strategy.route(_event(), _context())).type == "local_agent"
    assert (await strategy.route(_event(), _context(task_id="missing"))).type == "local_agent"


@pytest.mark.asyncio
async def test_requester_strategy_legacy_remote_task_path():
    tasks = {
        "root": {
            "status_state": "INPUT_REQUIRED",
            "metadata": {META_KEY_REMOTE_TASK_ID: "remote-task"},
        },
        "remote-task": {"status_state": "INPUT_REQUIRED", "metadata": {META_KEY_SOURCE_AGENT: "remote-a"}},
    }
    strategy = RequesterSourceStrategy(
        _StateManager(tasks),
        RequesterSourceProfile(suspended_states=["INPUT_REQUIRED"]),
    )

    target = await strategy.route(_event(), _context(is_specify_task=False))

    assert target.type == "remote_agent"
    assert target.agent_key == "remote-a"


@pytest.mark.asyncio
async def test_requester_strategy_resolves_cascade_next_hop():
    tasks = {
        "root": {
            "status_state": "INPUT_REQUIRED",
            "metadata": {META_KEY_SOURCE_AGENT: "DPA", META_KEY_SUB_TASKS: ["child"]},
        },
        "child": {
            "status_state": "INPUT_REQUIRED",
            "metadata": {META_KEY_SOURCE_AGENT: "remote-a", META_KEY_SUB_TASKS: ["grandchild"]},
        },
        "grandchild": {
            "status_state": "INPUT_REQUIRED",
            "metadata": {META_KEY_SOURCE_AGENT: "remote-b"},
        },
    }
    strategy = RequesterSourceStrategy(
        _StateManager(tasks),
        RequesterSourceProfile(suspended_states=["INPUT_REQUIRED"]),
        local_agent_keys={"DPA"},
    )

    target = await strategy.route(_event(), _context(task_id="grandchild"))

    assert target.type == "remote_agent"
    assert target.agent_key == "remote-a"
    assert strategy._determine_next_hop([]) == ""


@pytest.mark.asyncio
async def test_local_and_remote_strategy_branches():
    local = LocalAgentSourceStrategy(
        LocalAgentSourceProfile(delegate_types=["delegate"], default_remote_agent="default-remote")
    )
    assert (await local.route(_event("thought"), _context())).type == "requester"

    target = await local.route(_event("delegate", data={"agent_key": "agent-x"}), _context())
    assert target.type == "remote_agent"
    assert target.agent_key == "agent-x"

    remote = RemoteAgentSourceStrategy(
        RemoteAgentSourceProfile(
            terminal_frame_types=["CONTROL_COMPLETED"],
            frame_type_map={"COMPLETED": "CONTROL_COMPLETED"},
            default_frame_type="DATA",
        )
    )
    assert (await remote.route(_event("completed"), _context())).type == "local_agent"
    assert (await remote.route(_event("message"), _context())).type == "requester"
    assert remote._classify_frame(_event("ignored", metadata={"frame_type": "CUSTOM"})) == "CUSTOM"
