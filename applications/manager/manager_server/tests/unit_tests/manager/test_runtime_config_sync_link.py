"""Runtime config sync must target the Runtime belonging to this instance."""

from types import SimpleNamespace

import pytest

from manager_server.manager_config_push.endpoint import require_runtime_endpoint


class _DB:
    async def get(self, table: str, filters: dict):
        assert table == "instance_info"
        if filters["jiuwenclaw_id"] == "jid-a":
            return SimpleNamespace(runtime_host="https://runtime-a:8091/")
        return None


@pytest.mark.asyncio
async def test_resolve_runtime_endpoint_uses_instance_row(monkeypatch) -> None:
    monkeypatch.setattr("manager_server.infrastructure.db.get_db_handler", lambda: _DB())
    assert await require_runtime_endpoint("jid-a") == "https://runtime-a:8091"


@pytest.mark.asyncio
async def test_resolve_runtime_endpoint_rejects_missing_instance(monkeypatch) -> None:
    monkeypatch.setattr("manager_server.infrastructure.db.get_db_handler", lambda: _DB())
    with pytest.raises(ValueError, match="instance not found"):
        await require_runtime_endpoint("missing")
