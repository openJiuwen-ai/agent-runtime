"""Identity 引用计数：准入绑定与 match_expr 授权范围。"""

from __future__ import annotations

import pytest

from manager_server.core.authz import identity_reference_count as svc
from manager_server.infrastructure.match_expr import (
    iter_equality_binds,
    iter_field_literals,
    match_expr_mentions_value,
)


def test_match_expr_mentions_group_in_and_eq() -> None:
    assert match_expr_mentions_value(
        "group_id in ('swarm', 'other')", field="group_id", literal="swarm"
    )
    assert match_expr_mentions_value(
        "group_id == 'swarm'", field="group_id", literal="swarm"
    )
    assert not match_expr_mentions_value(
        "group_id in ('other')", field="group_id", literal="swarm"
    )
    assert match_expr_mentions_value(
        "group_id not in ('swarm')", field="group_id", literal="swarm"
    )


def test_iter_equality_binds_ignores_negative() -> None:
    binds = list(iter_equality_binds("group_id not in ('swarm') or user_id == 'u1'"))
    assert ("user_id", "u1") in binds
    assert ("group_id", "swarm") not in binds


def test_iter_field_literals_includes_in_list() -> None:
    literals = list(
        iter_field_literals(
            "group_id in ['swarm', 'ops']",
            fields=frozenset({"group_id"}),
        )
    )
    assert ("group_id", "swarm") in literals
    assert ("group_id", "ops") in literals


@pytest.mark.asyncio
async def test_count_org_references_sums_sources() -> None:
    class _Row:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _Handler:
        async def list_records(self, table, filters, limit=0, offset=0, order_by=None):
            if table == "instance_grant":
                assert filters.get("subject_type") == "org"
                assert filters.get("subject_id") == "swarm"
                return [_Row(id=1), _Row(id=2)]
            if table == "instance_agent_resource":
                return [
                    _Row(id=1, match_expr="group_id in ('swarm')"),
                    _Row(id=2, match_expr="user_id == 'alice'"),
                ]
            if table == "instance_service_resource":
                return [_Row(id=1, match_expr="group_id == 'other'")]
            if table == "workspace_quota_policy":
                return [_Row(id=1, match_expr="group_id in ('swarm')")]
            if table == "team_quota_policy":
                return []
            return []

    result = await svc.count_org_references(_Handler(), "swarm")  # type: ignore[arg-type]
    assert result["instance_grants"] == 2
    assert result["agent_resources"] == 1
    assert result["service_resources"] == 0
    assert result["workspace_quota_policies"] == 1
    assert result["team_quota_policies"] == 0
    assert result["total"] == 4


@pytest.mark.asyncio
async def test_count_user_references_sums_sources() -> None:
    class _Row:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _Handler:
        async def list_records(self, table, filters, limit=0, offset=0, order_by=None):
            if table == "instance_grant":
                assert filters.get("subject_type") == "user"
                assert filters.get("subject_id") == "alice"
                return [_Row(id=1)]
            if table == "instance_agent_resource":
                return [
                    _Row(id=1, match_expr="user_id in ('alice', 'bob')"),
                    _Row(id=2, match_expr="group_id == 'swarm'"),
                ]
            if table == "instance_service_resource":
                return []
            if table == "workspace_quota_policy":
                return []
            if table == "team_quota_policy":
                return [_Row(id=1, match_expr="user_id == 'alice'")]
            return []

    result = await svc.count_user_references(_Handler(), "alice")  # type: ignore[arg-type]
    assert result["user_id"] == "alice"
    assert result["instance_grants"] == 1
    assert result["agent_resources"] == 1
    assert result["team_quota_policies"] == 1
    assert result["total"] == 3
