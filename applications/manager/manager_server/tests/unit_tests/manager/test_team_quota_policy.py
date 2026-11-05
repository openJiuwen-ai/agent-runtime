# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Manager team-quota policy projection and startup compensation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from manager_server.core.quota import team_quota_policy as quota_policy
from manager_server.core.quota.team_quota_policy import TeamQuotaPolicyService


class _Handler:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    async def list_records(
        self,
        _table: str,
        _filters: dict[str, Any],
        *,
        limit: int,
        offset: int,
    ) -> list[Any]:
        _ = (limit, offset)
        return list(self.rows)

    async def get(self, _table: str, _filters: dict[str, Any]) -> None:
        return None

    async def create(self, _table: str, row: dict[str, Any]) -> dict[str, Any]:
        self.created.append(dict(row))
        return dict(row)

    async def update(
        self,
        _table: str,
        _filters: dict[str, Any],
        row: dict[str, Any],
    ) -> dict[str, Any]:
        self.updated.append(dict(row))
        return dict(row)


def _row(*, policy_id: str, cluster_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        cluster_id=cluster_id,
        policy_id=policy_id,
        policy_name="team-quota",
        policy_desc=None,
        match_expr=[],
        priority=0,
        max_teams_per_user=5,
        max_teammates_per_team=5,
        max_teams_per_org=0,
        source="manual",
        enabled=True,
        created_at="2026-11-04T00:00:00Z",
        updated_at="2026-11-04T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_upsert_pushes_before_persisting(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str, dict[str, Any]]] = []

    async def _push(
        cluster_id: str,
        method: str,
        path: str,
        body: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls.append((cluster_id, method, path, body))
        return {"success_flag": True}

    monkeypatch.setattr(quota_policy, "gateway_request", _push)
    handler = _Handler()
    saved = await TeamQuotaPolicyService(handler).upsert(
        cluster_id="cluster-a",
        policy_id="policy-a",
        max_teams_per_user=3,
        max_teammates_per_team=4,
    )

    assert calls == [
        (
            "cluster-a",
            "PUT",
            "/api/v1/team-quota/policies",
            {
                "policy_id": "policy-a",
                "policy_name": "team-quota",
                "policy_desc": None,
                "match_expr": [],
                "priority": 0,
                "max_teams_per_user": 3,
                "max_teammates_per_team": 4,
                "max_teams_per_org": 0,
                "source": "manual",
                "enabled": True,
            },
        )
    ]
    assert saved["max_teammates_per_team"] == 4
    assert handler.created and handler.created[0]["policy_id"] == "policy-a"


@pytest.mark.asyncio
async def test_resync_replays_every_cluster_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def _push(
        cluster_id: str,
        method: str,
        path: str,
        body: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert method == "PUT"
        assert path == "/api/v1/team-quota/policies"
        calls.append((cluster_id, body["policy_id"]))
        if cluster_id == "cluster-bad":
            raise RuntimeError("gateway unavailable")
        return {"success_flag": True}

    monkeypatch.setattr(quota_policy, "gateway_request", _push)
    handler = _Handler(
        [
            _row(policy_id="policy-a", cluster_id="cluster-a"),
            _row(policy_id="policy-bad", cluster_id="cluster-bad"),
            _row(policy_id="policy-c", cluster_id="cluster-c"),
        ]
    )
    result = await TeamQuotaPolicyService(handler).resync_all_to_gateway()

    assert result == {"total": 3, "synced": 2, "failed": 1}
    assert calls == [
        ("cluster-a", "policy-a"),
        ("cluster-bad", "policy-bad"),
        ("cluster-c", "policy-c"),
    ]


@pytest.mark.asyncio
async def test_resync_skips_rows_without_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _push(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("rows without cluster_id must not be pushed")

    monkeypatch.setattr(quota_policy, "gateway_request", _push)
    handler = _Handler([_row(policy_id="policy-orphan", cluster_id="")])
    result = await TeamQuotaPolicyService(handler).resync_all_to_gateway()

    assert result == {"total": 1, "synced": 0, "failed": 1}
