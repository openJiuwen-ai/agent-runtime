# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""user_preference_default：先推 Gateway 再写镜像；无 DELETE。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
