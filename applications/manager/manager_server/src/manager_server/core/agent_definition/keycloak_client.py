"""Agent ID owned Keycloak clients; only safe binding state leaves Manager."""

from __future__ import annotations

from asyncio import CancelledError, timeout
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import time
from typing import Any

from sqlalchemy import delete, select, update
from starlette.concurrency import run_in_threadpool

from manager_server.core.template.agent_template import (
    AgentTemplateService, TemplateMutationBusy, public_agent_template_data,
)
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.utils import new_uuid4, utc_now
from manager_server.integrations.keycloak.agent_clients import (
    RegistrationConflict, RegistrationError, load_registration_service,
)

TABLE = "agent_definition"
BINDING = "keycloak_client"
OPERATION = "_keycloak_operation"
ERROR = "_keycloak_error"
CLIENTS = "managed_agent_clients"
_log = get_logger(__name__)


class RegistrationUnavailable(RegistrationError):
    """The feature or its configuration is unavailable."""


def client_status(row: Any) -> dict:
    data = getattr(row, "data", None) or {}
    binding = data.get(BINDING) or {}
    operation = data.get(OPERATION) or {}
    return {
        "client_id": row.agent_id,
        "status": "bound" if binding else "unbound",
        "operation": operation.get("action") if operation.get("expires_at", 0) > time() else None,
        "error": data.get(ERROR),
    }


async def gateway_agent_data(handler, config_id: str, data: dict | None, *, overrides: dict | None = None):
    """Build a private dispatch map, separate from the saved custom JSON."""
    rows = await handler.list_records(TABLE, {"agent_config_template_id": config_id}, limit=100_000, offset=0)
    clients = {}
    for row in rows:
        binding = (row.data or {}).get(BINDING)
        if overrides is not None and row.agent_id in overrides:
            binding = overrides[row.agent_id]
        if binding:
            clients[row.agent_id] = {
                "runtime_template_id": row.runtime_template_id,
                "client_id": row.agent_id,
                "client_secret": binding["client_secret"],
            }
    if not clients and overrides is None:
        return public_agent_template_data(data)
    result = {key: value for key, value in (public_agent_template_data(data) or {}).items() if key != CLIENTS}
    # Include an empty map after unregistration so it replaces a previous dispatch.
    result[CLIENTS] = clients
    return result


@dataclass
class _Mutation:
    row: Any
    data: dict
    token: str
    completed: bool = False


class AgentClientService:
    def __init__(self, handler):
        self._h = handler

    async def get(self, agent_id: str):
        row = await self._h.get(TABLE, {"agent_id": agent_id})
        return client_status(row) if row is not None else None

    @asynccontextmanager
    async def _locked(self, agent_id):
        table = self._h.get_table(TABLE)
        match = table.c.agent_id == agent_id
        async with self._h.session_factory.begin() as session:
            if self._h.engine.dialect.name == "sqlite":
                await session.execute(update(table).where(match).values(updated_at=table.c.updated_at))
            row = (await session.execute(select(table).where(match).with_for_update())).one_or_none()
            yield session, table, row

    @asynccontextmanager
    async def _reserve(self, agent_id, action):
        operation = None
        async with self._locked(agent_id) as (session, table, row):
            if row is not None:
                data = dict(row.data or {})
                if (data.get(OPERATION) or {}).get("expires_at", 0) > time():
                    raise TemplateMutationBusy()
                data.pop(OPERATION, None)
                operation = _Mutation(row, data, new_uuid4())
                await session.execute(update(table).where(table.c.agent_id == agent_id).values(data={
                    **data, OPERATION: {"id": operation.token, "action": action, "expires_at": time() + 300},
                }))
        failed = False
        try:
            async with timeout(120):
                yield operation
        except (Exception, CancelledError) as exc:
            failed = True
            if operation:
                operation.data[ERROR] = {"action": action, "code": getattr(exc, "code", "request_failed")}
            if isinstance(exc, TimeoutError):
                raise RegistrationError("Operation timed out. Please retry.") from None
            if not isinstance(exc, (RegistrationError, CancelledError)):
                _log.warning("Agent client operation failed", agent_id=agent_id, action=action,
                             error_type=type(exc).__name__)
                raise RegistrationError("Agent client operation failed. Please retry.") from None
            raise
        finally:
            if operation is not None and not operation.completed:
                try:
                    await self._finish(operation)
                except Exception as exc:
                    _log.warning("Agent client reservation release failed", agent_id=agent_id,
                                 error_type=type(exc).__name__)
                    if not failed:
                        raise

    async def _finish(self, operation, *, data=None, delete_agent=False):
        async with self._locked(operation.row.agent_id) as (session, table, row):
            if row is None or (row.data or {}).get(OPERATION, {}).get("id") != operation.token:
                raise TemplateMutationBusy()
            match = table.c.agent_id == row.agent_id
            if delete_agent:
                await session.execute(delete(table).where(match))
            else:
                await session.execute(update(table).where(match).values(
                    data=operation.data if data is None else data, updated_at=utc_now(),
                ))
        operation.completed = True

    async def _service(self):
        try:
            return await run_in_threadpool(load_registration_service, settings)
        except RegistrationError as exc:
            error = RegistrationUnavailable(str(exc))
            error.code = "unconfigured"
            raise error from None

    async def _dispatch(self, row, binding, *, continue_on_error=False):
        from manager_server.core.template.push_template_to_gateway import (
            AGENT_TEMPLATES_KIND, update_template_on_referencing_gateways,
        )
        config = await self._h.get("agent_template", {"template_id": row.agent_config_template_id})
        if config is None:
            raise RegistrationError("Agent configuration template is unavailable")
        await update_template_on_referencing_gateways(
            self._h, AGENT_TEMPLATES_KIND, row.agent_config_template_id,
            {"data": config.data}, agent_client_overrides={row.agent_id: binding},
            continue_on_error=continue_on_error,
        )

    async def register(self, agent_id: str):
        async with self._reserve(agent_id, "register") as operation:
            if operation is None:
                return None
            row, data = operation.row, operation.data
            if data.get(BINDING):
                return client_status(row)
            # Config updates and other Agent IDs sharing this config must not overwrite dispatches.
            async with AgentTemplateService(self._h).reserve_template(row.agent_config_template_id) as config:
                if config is None:
                    raise RegistrationError("Agent configuration template is unavailable")
                service = await self._service()
                binding = await service.register(agent_id, agent_id, row.agent_name)
                try:
                    await self._dispatch(row, binding)
                    await self._finish(operation, data={**data, BINDING: binding, ERROR: None})
                except (Exception, CancelledError) as failure:
                    # Always attempt both compensations, even if restoring a Gateway fails.
                    for cleanup in (
                        self._dispatch(row, None, continue_on_error=True), service.delete(agent_id, binding),
                    ):
                        try:
                            async with timeout(60):
                                await cleanup
                        except Exception as exc:
                            _log.warning("Agent client rollback incomplete", agent_id=agent_id,
                                         error_type=type(exc).__name__)
                    if isinstance(failure, CancelledError):
                        raise
                    raise RegistrationError("Could not save or distribute Agent credentials. Please retry.") from None
        return await self.get(agent_id)

    async def remove(self, agent_id: str, *, delete_agent=False):
        async with self._reserve(agent_id, "delete") as operation:
            if operation is None:
                return False if delete_agent else None
            row, data = operation.row, operation.data
            async with AgentTemplateService(self._h).reserve_template(row.agent_config_template_id):
                binding = data.get(BINDING)
                if binding:
                    try:
                        service = await self._service()
                    except RegistrationUnavailable:
                        raise RegistrationConflict(
                            "Enable and configure Keycloak registration before deleting "
                            "this bound Agent or registration.",
                            code="bound_client_cleanup_unavailable",
                        ) from None
                    try:
                        await self._dispatch(row, None)
                        await service.delete(agent_id, binding)
                    except (Exception, CancelledError):
                        try:
                            async with timeout(60):
                                await self._dispatch(row, binding, continue_on_error=True)
                        except Exception as exc:
                            _log.warning("Agent client dispatch restore failed", agent_id=agent_id,
                                         error_type=type(exc).__name__)
                        raise
                await self._finish(operation, data={key: value for key, value in data.items()
                                                   if key not in {BINDING, ERROR}}, delete_agent=delete_agent)
        return True if delete_agent else await self.get(agent_id)
