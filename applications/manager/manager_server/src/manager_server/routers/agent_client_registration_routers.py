"""Admin-only endpoints for optional Keycloak client registration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from openjiuwen_runtime.foundation.db.handler import DBHandler
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import get_db_handler
from manager_server.integrations.keycloak.agent_clients import (
    CLIENT_PREFIX, KeycloakAgentClients, RegistrationError, load_registration_config, load_registration_service,
)
from manager_server.models.template_models import AGENT_TEMPLATE_TABLE_DEF
from manager_server.routers.auth_guards import AdminUser

registration_router = APIRouter(prefix="/integrations/keycloak/agent-client-registrations")
_REFERENCE_SCAN_PAGE_SIZE = 500


class RegistrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registration_id: UUID
    template_name: str = Field(min_length=1, max_length=128)


def _service() -> KeycloakAgentClients:
    if not settings.agent_client_registration_enabled:
        raise HTTPException(status_code=404, detail="Agent 客户端注册未启用")
    try:
        return load_registration_service(settings)
    except RegistrationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@registration_router.get("/capabilities")
async def registration_capabilities(user: AdminUser, response: Response):
    response.headers["Cache-Control"] = "no-store"
    enabled = settings.agent_client_registration_enabled
    try:
        if enabled:
            await run_in_threadpool(load_registration_config, settings)
        return {"enabled": enabled, "configured": enabled}
    except RegistrationError as exc:
        return {"enabled": True, "configured": False, "reason": str(exc)}


@registration_router.post("")
async def register_agent_client(body: RegistrationBody, user: AdminUser, response: Response):
    response.headers["Cache-Control"] = "no-store"
    service = await run_in_threadpool(_service)
    name = body.template_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="请先填写模板名称")
    try:
        return await service.register(body.registration_id, name, user.user_id)
    except RegistrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc), headers={"Cache-Control": "no-store"}) from None


@registration_router.delete("/{registration_id}")
async def cancel_agent_client(
    registration_id: UUID, user: AdminUser,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    service = await run_in_threadpool(_service)
    client_id = CLIENT_PREFIX + registration_id.hex
    # The form's close request must never remove credentials already saved by
    # another tab, a successful save whose response was lost, or a manual copy.
    offset = 0
    while True:
        rows = await handler.list_records(
            AGENT_TEMPLATE_TABLE_DEF.table_name, {},
            limit=_REFERENCE_SCAN_PAGE_SIZE, offset=offset, order_by=[("id", False)],
        )
        for row in rows:
            data = getattr(row, "data", None)
            entries = data.get("custom_config") if isinstance(data, dict) else None
            if isinstance(entries, list) and any(isinstance(item, dict) and item.get("client_id") == client_id
                                                 for item in entries):
                raise HTTPException(status_code=409, detail="该 Client 已被 Agent 模板使用，不能清理")
        if len(rows) < _REFERENCE_SCAN_PAGE_SIZE:
            break
        offset += len(rows)
    try:
        return {"deleted": await service.cancel(registration_id, user.user_id)}
    except RegistrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
