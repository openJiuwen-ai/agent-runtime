"""Manager-owned A2A discovery network settings."""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.utils import utc_now
from manager_server.models.template_models import A2A_DISCOVERY_SETTINGS_TABLE_DEF
from manager_server.schemas.template_schemas import A2ADiscoverySettingsBody

_TABLE = A2A_DISCOVERY_SETTINGS_TABLE_DEF.table_name
_SETTINGS_ID = "global"


class A2ADiscoverySettingsService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def get(self) -> A2ADiscoverySettingsBody:
        row = await self._handler.get(_TABLE, {"settings_id": _SETTINGS_ID})
        if row is None:
            return A2ADiscoverySettingsBody(
                allow_http=False,
                allow_loopback=False,
                allow_private_network=False,
                allow_public_http=False,
            )
        return A2ADiscoverySettingsBody(
            allow_http=bool(row.allow_http),
            allow_loopback=bool(row.allow_loopback),
            allow_private_network=bool(row.allow_private_network),
            allow_public_http=bool(row.allow_public_http),
        )

    async def update(self, body: A2ADiscoverySettingsBody) -> A2ADiscoverySettingsBody:
        now = utc_now()
        values = body.model_dump()
        existing = await self._handler.get(_TABLE, {"settings_id": _SETTINGS_ID})
        if existing is None:
            await self._handler.create(
                _TABLE,
                {
                    "settings_id": _SETTINGS_ID,
                    **values,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        else:
            await self._handler.update(
                _TABLE,
                {"settings_id": _SETTINGS_ID},
                {**values, "updated_at": now},
            )
        return body

    async def sync(self, body: A2ADiscoverySettingsBody) -> None:
        # Retryable projection delivery; settings and local disabling are committed first.
        from manager_server.core.template.a2a_outbound_template import (
            _REFERENCE_SCAN_PAGE_SIZE, _row_to_update_sync,
        )
        from manager_server.core.template.push_template_to_gateway import update_template_on_referencing_gateways
        from manager_server.models.template_models import A2A_OUTBOUND_TEMPLATE_TABLE_DEF

        offset = 0
        errors: list[Exception] = []
        network_policy = body.model_dump()
        while True:
            rows = await self._handler.list_records(
                A2A_OUTBOUND_TEMPLATE_TABLE_DEF.table_name, {},
                limit=_REFERENCE_SCAN_PAGE_SIZE, offset=offset, order_by=[("id", False)],
            )
            for row in rows:
                try:
                    await update_template_on_referencing_gateways(
                        self._handler, "a2a_outbound_templates", str(row.template_id),
                        _row_to_update_sync(row), network_policy=network_policy,
                        continue_on_error=True,
                    )
                except Exception as exc:
                    errors.append(exc)
            if len(rows) < _REFERENCE_SCAN_PAGE_SIZE:
                break
            offset += len(rows)
        if errors:
            raise errors[0]
