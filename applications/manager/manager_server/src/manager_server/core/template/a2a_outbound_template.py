"""Manager-owned outbound A2A Agent catalog CRUD."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from sqlalchemy.exc import IntegrityError

from manager_server.core.template.a2a_discovery import (
    A2ADiscoveryError,
    _normalize_url,
    _validate_target,
    critical_identity,
    delete_candidate,
    fetch_agent_card,
    get_candidate,
)
from manager_server.core.template.a2a_discovery_settings import A2ADiscoverySettingsService
from manager_server.core.template.push_template_to_gateway import (
    delete_template_on_referencing_gateways,
    sync_new_a2a_outbound_to_gateways,
    update_template_on_referencing_gateways,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.models.template_models import (
    A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF,
    A2A_OUTBOUND_TEMPLATE_TABLE_DEF,
)
from manager_server.schemas.template_schemas import (
    A2AOutboundTemplateCreateBody,
    A2ADiscoverySettingsBody,
    A2AOutboundTemplateEditOut,
    A2AOutboundTemplateListQuery,
    A2AOutboundTemplateOut,
    A2AOutboundTemplateUpdateBody,
)

_TABLE = A2A_OUTBOUND_TEMPLATE_TABLE_DEF.table_name
_POLICY_TABLE = A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
_REFERENCE_SCAN_PAGE_SIZE = 500
_ALLOWED_SORT_FIELDS = frozenset({"template_name", "source_url", "enabled", "updated_at"})


def _registration_key(source_url: str, card_path: str) -> str:
    digest = hashlib.sha256(f"{source_url}\n{card_path}".encode()).hexdigest()
    return f"sha256:{digest}"


def _normalize_tags(value: Any) -> list[str] | None:
    if value is None or isinstance(value, list):
        return value
    return list(value) if value else None


def _row_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "template_id": str(row.template_id),
        "template_name": row.template_name,
        "description": row.description,
        "a2a_tags": _normalize_tags(row.a2a_tags),
        "source_url": row.source_url,
        "card_path": row.card_path,
        "agent_card": row.agent_card,
        "card_fingerprint": row.card_fingerprint,
        "card_revision": row.card_revision,
        "selected_interface": row.selected_interface,
        "credential_configured": bool(row.credential),
        "connect_timeout_seconds": row.connect_timeout_seconds,
        "sync_wait_seconds": row.sync_wait_seconds,
        "enabled": row.enabled,
        "pending_revision": row.pending_revision,
        "last_checked_at": iso_datetime(row.last_checked_at),
        "last_error_code": row.last_error_code,
        "last_error_summary": row.last_error_summary,
        "data": row.data,
        "created_at": iso_datetime(row.created_at),
        "updated_at": iso_datetime(row.updated_at),
    }


def row_to_out(row: Any) -> A2AOutboundTemplateOut:
    return A2AOutboundTemplateOut(**_row_payload(row))


def row_to_edit_out(row: Any) -> A2AOutboundTemplateEditOut:
    return A2AOutboundTemplateEditOut(**_row_payload(row), credential=row.credential)


def row_to_sync(row: Any) -> dict[str, Any]:
    payload = _row_payload(row)
    for key in (
        "id",
        "created_at",
        "credential_configured",
        "pending_revision",
        "last_checked_at",
        "last_error_code",
        "last_error_summary",
    ):
        payload.pop(key, None)
    credential = str(getattr(row, "credential", None) or "")
    payload["credential"] = (
        {"operation": "replace", "value": credential} if credential else {"operation": "clear"}
    )
    return payload


def _row_to_update_sync(row: Any, *, credential_operation: str = "keep") -> dict[str, Any]:
    payload = row_to_sync(row)
    payload.pop("template_id", None)
    if credential_operation == "keep":
        payload["credential"] = {"operation": "keep"}
    elif credential_operation == "clear":
        payload["credential"] = {"operation": "clear"}
    return payload


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    fields = [
        row.template_id,
        row.template_name,
        row.description or "",
        row.source_url,
        *(_normalize_tags(row.a2a_tags) or []),
    ]
    return any(needle in str(value).lower() for value in fields)


def _member_template_ids(row: Any) -> list[str]:
    raw = getattr(row, "member_template_ids", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [str(value) for value in raw]


class A2AOutboundTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(self, body: A2AOutboundTemplateCreateBody) -> A2AOutboundTemplateOut:
        card = await get_candidate(self._handler, body.discovery_id)
        existing = await self._handler.list_records(_TABLE, {}, limit=_LIST_ALL_CAP, offset=0)
        if any(
            (row.source_url == card.source_url and row.card_path == card.card_path)
            or (row.selected_interface or {}).get("url") == card.selected_interface["url"]
            for row in existing
        ):
            raise A2ADiscoveryError("AGENT_ALREADY_REGISTERED", "A2A Agent is already registered")
        now = utc_now()
        values = body.model_dump(exclude={"discovery_id"})
        credential = (values.get("credential") or "").strip()
        values["credential"] = credential or None
        values.update(
            {
                "template_id": new_uuid4(),
                "source_url": card.source_url,
                "card_path": card.card_path,
                "registration_key": _registration_key(card.source_url, card.card_path),
                "agent_card": card.agent_card,
                "card_fingerprint": card.card_fingerprint,
                "selected_interface": card.selected_interface,
                "card_revision": 1,
                "pending_revision": None,
                "last_checked_at": now,
                "last_error_code": None,
                "last_error_summary": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        try:
            created = await self._handler.create(_TABLE, values)
        except IntegrityError as exc:
            raise A2ADiscoveryError(
                "AGENT_ALREADY_REGISTERED", "A2A Agent is already registered"
            ) from exc
        await delete_candidate(self._handler, body.discovery_id)
        await sync_new_a2a_outbound_to_gateways(self._handler, str(created.template_id))
        return row_to_out(created)

    async def refresh(self, template_id: str) -> A2AOutboundTemplateOut | None:
        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None
        now = utc_now()
        try:
            settings = await A2ADiscoverySettingsService(self._handler).get()
            card = await fetch_agent_card(
                existing.source_url,
                existing.card_path,
                allow_http=settings.allow_http,
                allow_loopback=settings.allow_loopback,
                allow_private_network=settings.allow_private_network,
                allow_public_http=settings.allow_public_http,
            )
        except A2ADiscoveryError as exc:
            row = await self._handler.update(
                _TABLE,
                {"template_id": template_id},
                {
                    "last_checked_at": now,
                    "last_error_code": exc.code,
                    "last_error_summary": exc.summary,
                    "updated_at": now,
                },
            )
            raise A2ADiscoveryError(exc.code, exc.summary) from exc
        pending = {
            "source_url": card.source_url,
            "card_path": card.card_path,
            "card_fingerprint": card.card_fingerprint,
            "agent_card": card.agent_card,
            "selected_interface": card.selected_interface,
        }
        updates: dict[str, Any] = {
            "last_checked_at": now,
            "last_error_code": None,
            "last_error_summary": None,
            "updated_at": now,
        }
        old_card = existing.agent_card or {}
        old_interface = existing.selected_interface or {}
        if critical_identity(old_card, old_interface) != critical_identity(
            card.agent_card, card.selected_interface
        ):
            updates["pending_revision"] = pending
        else:
            changed = card.card_fingerprint != existing.card_fingerprint
            updates.update(
                {
                    "agent_card": card.agent_card,
                    "card_fingerprint": card.card_fingerprint,
                    "selected_interface": card.selected_interface,
                    "card_revision": existing.card_revision + int(changed),
                    "pending_revision": None,
                }
            )
        row = await self._handler.update(_TABLE, {"template_id": template_id}, updates)
        if row is not None and "agent_card" in updates:
            await update_template_on_referencing_gateways(
                self._handler, "a2a_outbound_templates", template_id, _row_to_update_sync(row)
            )
        return row_to_out(row)

    async def disable_disallowed(self, settings: A2ADiscoverySettingsBody) -> int:
        disabled_rows: list[Any] = []
        offset = 0
        while True:
            rows = await self._handler.list_records(
                _TABLE,
                {},
                limit=_REFERENCE_SCAN_PAGE_SIZE,
                offset=offset,
                order_by=[("id", False)],
            )
            for existing in rows:
                if not existing.enabled:
                    continue
                _, _, card_url = _normalize_url(existing.source_url, existing.card_path)
                interface_url = str((existing.selected_interface or {}).get("url") or "")
                try:
                    for url in (card_url, interface_url):
                        await _validate_target(
                            url,
                            allow_http=settings.allow_http,
                            allow_loopback=settings.allow_loopback,
                            allow_private_network=settings.allow_private_network,
                            allow_public_http=settings.allow_public_http,
                        )
                except A2ADiscoveryError as exc:
                    if exc.code != "DISCOVERY_BLOCKED":
                        continue
                    row = await self._handler.update(
                        _TABLE,
                        {"template_id": existing.template_id},
                        {
                            "enabled": False,
                            "last_error_code": exc.code,
                            "last_error_summary": "disabled by discovery network settings",
                            "updated_at": utc_now(),
                        },
                    )
                    if row is not None:
                        disabled_rows.append(row)
            if len(rows) < _REFERENCE_SCAN_PAGE_SIZE:
                break
            offset += len(rows)
        sync_errors: list[Exception] = []
        for row in disabled_rows:
            try:
                await update_template_on_referencing_gateways(
                    self._handler,
                    "a2a_outbound_templates",
                    str(row.template_id),
                    _row_to_update_sync(row),
                )
            except Exception as exc:  # keep disabling the remaining affected Agents
                sync_errors.append(exc)
        if sync_errors:
            raise sync_errors[0]
        return len(disabled_rows)

    async def confirm_revision(
        self, template_id: str, *, accept: bool
    ) -> A2AOutboundTemplateOut | None:
        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None
        pending = existing.pending_revision
        if not isinstance(pending, dict):
            raise ValueError("no pending A2A Agent Card revision")  # noqa: TRY004
        updates: dict[str, Any] = {"pending_revision": None, "updated_at": utc_now()}
        if accept:
            updates.update(
                {
                    "source_url": pending["source_url"],
                    "card_path": pending["card_path"],
                    "card_fingerprint": pending["card_fingerprint"],
                    "agent_card": pending["agent_card"],
                    "selected_interface": pending["selected_interface"],
                    "card_revision": existing.card_revision + 1,
                }
            )
        row = await self._handler.update(_TABLE, {"template_id": template_id}, updates)
        if accept and row is not None:
            await update_template_on_referencing_gateways(
                self._handler, "a2a_outbound_templates", template_id, _row_to_update_sync(row)
            )
        return row_to_out(row)

    async def get(self, template_id: str) -> A2AOutboundTemplateOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        return row_to_out(row) if row is not None else None

    async def get_for_edit(self, template_id: str) -> A2AOutboundTemplateEditOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        return row_to_edit_out(row) if row is not None else None

    async def list_templates(self, query: A2AOutboundTemplateListQuery) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        order_by = resolve_order_by(
            query.sort_by, query.sort_order, allowed_sort_fields=_ALLOWED_SORT_FIELDS
        )
        search = (query.search or "").strip()
        if search:
            rows = await self._handler.list_records(
                _TABLE, filters, limit=_LIST_ALL_CAP, offset=0, order_by=order_by
            )
            items = [
                row_to_out(row).model_dump(mode="json")
                for row in rows
                if _matches_search(row, search)
            ]
            total = len(items)
            offset = (query.page - 1) * query.page_size
            items = items[offset : offset + query.page_size]
        else:
            offset = (query.page - 1) * query.page_size
            rows = await self._handler.list_records(
                _TABLE, filters, limit=query.page_size, offset=offset, order_by=order_by
            )
            items = [row_to_out(row).model_dump(mode="json") for row in rows]
            total = await self._handler.count_records(_TABLE, filters)
        return {"items": items, "total": total, "page": query.page, "page_size": query.page_size}

    async def update(
        self, template_id: str, body: A2AOutboundTemplateUpdateBody
    ) -> A2AOutboundTemplateOut | None:
        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None
        updates = body.model_dump(exclude_unset=True)
        clear_credential = updates.pop("clear_credential", False)
        credential_operation = "keep"
        if "credential" in updates:
            credential = (updates.pop("credential") or "").strip()
            if credential:
                updates["credential"] = credential
                credential_operation = "replace"
        if clear_credential:
            updates["credential"] = None
            credential_operation = "clear"
        if not updates:
            return row_to_out(existing)
        updates["updated_at"] = utc_now()
        row = await self._handler.update(_TABLE, {"template_id": template_id}, updates)
        if row is not None:
            await update_template_on_referencing_gateways(
                self._handler,
                "a2a_outbound_templates",
                template_id,
                _row_to_update_sync(row, credential_operation=credential_operation),
            )
        return row_to_out(row) if row is not None else None

    async def delete(self, template_id: str) -> bool:
        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return False
        offset = 0
        while True:
            policies = await self._handler.list_records(
                _POLICY_TABLE,
                {},
                limit=_REFERENCE_SCAN_PAGE_SIZE,
                offset=offset,
                order_by=[("id", False)],
            )
            if any(template_id in _member_template_ids(row) for row in policies):
                raise ValueError("a2a outbound template is referenced by an access policy")
            if len(policies) < _REFERENCE_SCAN_PAGE_SIZE:
                break
            offset += len(policies)
        await delete_template_on_referencing_gateways(
            self._handler, "a2a_outbound_templates", template_id
        )
        return await self._handler.delete(_TABLE, {"template_id": template_id})
