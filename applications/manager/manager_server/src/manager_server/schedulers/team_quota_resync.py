# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Startup compensation for Manager team-quota policies.

Older MariaDB deployments could leave a policy only in Gateway-local SQLite.
After Gateway starts using the shared MariaDB tables, replay the Manager rows
so the missing projection is repaired without requiring an admin to re-save
every policy. Gateway upserts by ``policy_id``, so replay is idempotent.
"""

from __future__ import annotations

import asyncio

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.quota.team_quota_policy import TeamQuotaPolicyService
from manager_server.infrastructure.logger import get_logger

_log = get_logger(__name__)

_MAX_ATTEMPTS = 5
_INITIAL_DELAY_SEC = 1.0
_MAX_DELAY_SEC = 8.0


async def run_team_quota_resync(handler: DBHandler) -> None:
    """Replay all team-quota policies to Gateway, retrying transient failures."""
    service = TeamQuotaPolicyService(handler)
    delay = _INITIAL_DELAY_SEC
    total = 0
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            result = await service.resync_all_to_gateway()
        except Exception as exc:  # noqa: BLE001 - startup compensation is best effort
            _log.warning(
                "team_quota_resync_error attempt=%s error=%s",
                attempt,
                exc,
            )
        else:
            total = int(result.get("total") or 0)
            synced = int(result.get("synced") or 0)
            failed = int(result.get("failed") or 0)
            if failed == 0:
                _log.info(
                    "team_quota_resync_ok attempt=%s total=%s synced=%s",
                    attempt,
                    total,
                    synced,
                )
                return
            _log.warning(
                "team_quota_resync_partial attempt=%s total=%s synced=%s failed=%s",
                attempt,
                total,
                synced,
                failed,
            )
        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(delay)
            delay = min(delay * 2, _MAX_DELAY_SEC)
    _log.warning("team_quota_resync_gave_up attempts=%s total=%s", _MAX_ATTEMPTS, total)


__all__ = ["run_team_quota_resync"]
