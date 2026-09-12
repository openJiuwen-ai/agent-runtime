"""Runtime config sync must target the Runtime belonging to this instance."""

from types import SimpleNamespace

import pytest

from manager_server.core.instance_resource.runtime_config_sync import (
    resolve_runtime_endpoint,
)


class _DB:
    async def get(self, table: str, filters: dict):
        assert table == "instance_info"
        if filters["jiuwenclaw_id"] == "jid-a":
            return SimpleNamespace(runtime_config_host="https://runtime-a:8091/")
        return None


@pytest.mark.asyncio
async def test_resolve_runtime_endpoint_uses_instance_row() -> None:
    assert await resolve_runtime_endpoint(_DB(), "jid-a") == "https://runtime-a:8091"
