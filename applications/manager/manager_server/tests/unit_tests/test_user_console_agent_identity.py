"""Agent 身份 ID 计算规则测试。"""

from __future__ import annotations

import pytest

from manager_server.core.user_console.agent_identity import (
    AGENT_ID_LENGTH,
    build_agent_id,
    build_agent_name,
    try_build_agent_identity,
)

pytestmark = pytest.mark.unit

_RUNTIME = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
_AGENT = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
_AGENT_OTHER = "16fd2706-8baf-433b-82eb-8c7fada847da"


def test_agent_id_matches_fixed_vectors() -> None:
    assert build_agent_id(_RUNTIME, _AGENT) == "EyMFedy4mZ0b8btF1gxJFjsq4FOl7Pj6m2EYP3Ecmu7"
    assert (
        build_agent_id(_RUNTIME, _AGENT_OTHER)
        == "EyMFedy4mZ0b8btF1gxJFgn49UE0rvnKBhnBACe3wCY"
    )
    assert build_agent_id("", _AGENT) == "0000000000000000000003n9R8RlRyqVJBUQUv6toL1"


def test_agent_id_is_reproducible() -> None:
    assert build_agent_id(_RUNTIME, _AGENT) == build_agent_id(_RUNTIME, _AGENT)


def test_agent_id_normalizes_case_whitespace_and_dashes() -> None:
    assert build_agent_id(f"  {_RUNTIME.upper()} ", _AGENT.upper()) == build_agent_id(_RUNTIME, _AGENT)
    assert build_agent_id(_RUNTIME.replace("-", ""), _AGENT.replace("-", "")) == build_agent_id(
        _RUNTIME, _AGENT
    )


def test_agent_id_distinguishes_pairs() -> None:
    ids = {
        build_agent_id(_RUNTIME, _AGENT),
        build_agent_id(_RUNTIME, _AGENT_OTHER),
        build_agent_id(_AGENT, _RUNTIME),
        build_agent_id("", _AGENT),
    }
    assert len(ids) == 4


def test_agent_id_length_and_charset() -> None:
    value = build_agent_id(_RUNTIME, _AGENT)
    assert len(value) == AGENT_ID_LENGTH == 43
    assert value.isalnum()
    assert value.isascii()


def test_agent_name_prefixes_agent_id() -> None:
    agent_id = build_agent_id(_RUNTIME, _AGENT)
    assert build_agent_name(agent_id) == f"agent_{agent_id}"


def test_try_build_agent_identity_returns_pair() -> None:
    agent_id = build_agent_id(_RUNTIME, _AGENT)
    assert try_build_agent_identity(_RUNTIME, _AGENT) == (agent_id, f"agent_{agent_id}")


@pytest.mark.parametrize("runtime, agent", [("", ""), (_RUNTIME, ""), ("legacy-tpl", _AGENT)])
def test_try_build_agent_identity_rejects_unusable_ids(runtime: str, agent: str) -> None:
    assert try_build_agent_identity(runtime, agent) is None


def test_build_agent_id_rejects_non_uuid() -> None:
    with pytest.raises(ValueError):
        build_agent_id("legacy-tpl", _AGENT)
