# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""user_preference_default：先推 Gateway 再写镜像；无 DELETE。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import ManagerApiHarness

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_upsert_pushes_gateway_then_creates_row():
    from manager_server.core.application_config.user_preference_default import (
        UserPreferenceDefaultService,
    )

    handler = AsyncMock()
    handler.get = AsyncMock(return_value=None)
    created = MagicMock(
        id=1,
        jiuwenclaw_id="jid-1",
        preferred_language="en",
        extras=None,
        created_at=None,
        updated_at=None,
    )
    handler.create = AsyncMock(return_value=created)

    with patch(
        "manager_server.core.application_config.user_preference_default.gateway_request",
        new_callable=AsyncMock,
        return_value={"success_flag": True},
    ) as gw_mock:
        svc = UserPreferenceDefaultService(handler)
        data = await svc.upsert("jid-1", preferred_language="en")

    gw_mock.assert_awaited_once_with(
        "jid-1",
        "PUT",
        "/api/v1/user-preference-default",
        {"preferred_language": "en"},
    )
    handler.create.assert_awaited_once()
    assert data["preferred_language"] == "en"
    assert data["jiuwenclaw_id"] == "jid-1"


@pytest.mark.asyncio
async def test_upsert_updates_existing_mirror_row():
    from manager_server.core.application_config.user_preference_default import (
        UserPreferenceDefaultService,
    )

    handler = AsyncMock()
    existing = MagicMock(
        id=1,
        jiuwenclaw_id="jid-1",
        preferred_language="zh",
        extras=None,
        created_at=None,
        updated_at=None,
    )
    updated = MagicMock(
        id=1,
        jiuwenclaw_id="jid-1",
        preferred_language="en",
        extras=None,
        created_at=None,
        updated_at=None,
    )
    handler.get = AsyncMock(return_value=existing)
    handler.update = AsyncMock(return_value=updated)
    handler.create = AsyncMock()

    with patch(
        "manager_server.core.application_config.user_preference_default.gateway_request",
        new_callable=AsyncMock,
        return_value={"success_flag": True},
    ) as gw_mock:
        svc = UserPreferenceDefaultService(handler)
        data = await svc.upsert("jid-1", preferred_language="en")

    gw_mock.assert_awaited_once()
    handler.update.assert_awaited_once()
    handler.create.assert_not_awaited()
    assert data["preferred_language"] == "en"


@pytest.mark.asyncio
async def test_upsert_extras_unset_does_not_send_or_overwrite():
    from manager_server.core.application_config.user_preference_default import (
        UserPreferenceDefaultService,
    )

    handler = AsyncMock()
    existing = MagicMock(
        id=1,
        jiuwenclaw_id="jid-1",
        preferred_language="zh",
        extras={"keep": True},
        created_at=None,
        updated_at=None,
    )
    updated = MagicMock(
        id=1,
        jiuwenclaw_id="jid-1",
        preferred_language="en",
        extras={"keep": True},
        created_at=None,
        updated_at=None,
    )
    handler.get = AsyncMock(return_value=existing)
    handler.update = AsyncMock(return_value=updated)

    with patch(
        "manager_server.core.application_config.user_preference_default.gateway_request",
        new_callable=AsyncMock,
        return_value={"success_flag": True},
    ) as gw_mock:
        svc = UserPreferenceDefaultService(handler)
        await svc.upsert("jid-1", preferred_language="en")

    assert gw_mock.await_args.args[3] == {"preferred_language": "en"}
    updates = handler.update.await_args.args[2]
    assert "extras" not in updates


@pytest.mark.asyncio
async def test_upsert_extras_null_clears_and_pushes():
    from manager_server.core.application_config.user_preference_default import (
        UserPreferenceDefaultService,
    )

    handler = AsyncMock()
    existing = MagicMock(
        id=1,
        jiuwenclaw_id="jid-1",
        preferred_language="zh",
        extras={"old": 1},
        created_at=None,
        updated_at=None,
    )
    updated = MagicMock(
        id=1,
        jiuwenclaw_id="jid-1",
        preferred_language="zh",
        extras=None,
        created_at=None,
        updated_at=None,
    )
    handler.get = AsyncMock(return_value=existing)
    handler.update = AsyncMock(return_value=updated)

    with patch(
        "manager_server.core.application_config.user_preference_default.gateway_request",
        new_callable=AsyncMock,
        return_value={"success_flag": True},
    ) as gw_mock:
        svc = UserPreferenceDefaultService(handler)
        data = await svc.upsert("jid-1", preferred_language="zh", extras=None)

    assert gw_mock.await_args.args[3] == {
        "preferred_language": "zh",
        "extras": None,
    }
    assert handler.update.await_args.args[2]["extras"] is None
    assert data["extras"] is None


@pytest.mark.asyncio
async def test_upsert_extras_object_overwrites():
    from manager_server.core.application_config.user_preference_default import (
        UserPreferenceDefaultService,
    )

    handler = AsyncMock()
    handler.get = AsyncMock(return_value=None)
    created = MagicMock(
        id=2,
        jiuwenclaw_id="jid-1",
        preferred_language="en",
        extras={"k": "v"},
        created_at=None,
        updated_at=None,
    )
    handler.create = AsyncMock(return_value=created)

    with patch(
        "manager_server.core.application_config.user_preference_default.gateway_request",
        new_callable=AsyncMock,
        return_value={"success_flag": True},
    ) as gw_mock:
        svc = UserPreferenceDefaultService(handler)
        data = await svc.upsert(
            "jid-1", preferred_language="en", extras={"k": "v"}
        )

    assert gw_mock.await_args.args[3] == {
        "preferred_language": "en",
        "extras": {"k": "v"},
    }
    assert handler.create.await_args.args[1]["extras"] == {"k": "v"}
    assert data["extras"] == {"k": "v"}


@pytest.mark.asyncio
async def test_upsert_rejects_invalid_language_before_gateway():
    from manager_server.core.application_config.user_preference_default import (
        UserPreferenceDefaultService,
    )

    handler = AsyncMock()
    with patch(
        "manager_server.core.application_config.user_preference_default.gateway_request",
        new_callable=AsyncMock,
    ) as gw_mock:
        svc = UserPreferenceDefaultService(handler)
        with pytest.raises(ValueError, match="preferred_language"):
            await svc.upsert("jid-1", preferred_language="fr")
    gw_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_skips_mdb_when_gateway_fails():
    from manager_server.core.application_config.user_preference_default import (
        UserPreferenceDefaultService,
    )

    handler = AsyncMock()
    with patch(
        "manager_server.core.application_config.user_preference_default.gateway_request",
        new_callable=AsyncMock,
        side_effect=RuntimeError("gw down"),
    ):
        svc = UserPreferenceDefaultService(handler)
        with pytest.raises(ValueError, match="failed to sync to gateway"):
            await svc.upsert("jid-1", preferred_language="zh")
    handler.create.assert_not_awaited()
    handler.update.assert_not_awaited()


def test_row_to_dict_tolerates_missing_id_fields():
    from manager_server.core.application_config.user_preference_default import (
        _row_to_dict,
    )

    row = SimpleNamespace(
        preferred_language="en",
        extras=None,
        created_at=None,
        updated_at=None,
    )
    data = _row_to_dict(row)
    assert data["id"] is None
    assert data["jiuwenclaw_id"] is None
    assert data["preferred_language"] == "en"


@pytest.mark.asyncio
async def test_rest_get_returns_404_when_missing(manager_api: ManagerApiHarness):
    jid = await manager_api.create_instance(name="ut-pref-get-404")
    resp = await manager_api.http.get(
        manager_api.instances_url(f"/{jid}/user-preference-default")
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rest_put_then_get_round_trip(manager_api: ManagerApiHarness):
    jid = await manager_api.create_instance(name="ut-pref-put-get")
    put_resp = await manager_api.http.put(
        manager_api.instances_url(f"/{jid}/user-preference-default"),
        json={"preferred_language": "en"},
    )
    assert put_resp.status_code == 200
    put_data = put_resp.json()["data"]
    assert put_data["preferred_language"] == "en"
    assert put_data["jiuwenclaw_id"] == jid

    get_resp = await manager_api.http.get(
        manager_api.instances_url(f"/{jid}/user-preference-default")
    )
    assert get_resp.status_code == 200
    get_data = get_resp.json()["data"]
    assert get_data["preferred_language"] == "en"
    assert get_data["jiuwenclaw_id"] == jid
