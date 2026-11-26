"""Agent 模板 API：列表、新增、改名、删除（管理面 ``require_admin``）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.agent_definition.service import (
    AgentDefinitionExists,
    AgentDefinitionService,
    AgentNameDuplicated,
    TemplateUnavailable,
)
from manager_server.infrastructure.db import get_db_handler
from manager_server.infrastructure.config import settings
from manager_server.core.agent_definition.keycloak_client import AgentClientService, RegistrationUnavailable
from manager_server.integrations.keycloak.agent_clients import RegistrationError, RegistrationConflict

from manager_server.schemas.agent_definition_schemas import (
    AgentDefinitionCreateBody,
    AgentDefinitionListQuery,
    AgentDefinitionRenameBody,
)
from manager_server.schemas.common_schemas import ResponseModel

agent_definition_router = APIRouter()


@agent_definition_router.get("/agent-definitions/", response_model=ResponseModel)
async def list_agent_definitions(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[AgentDefinitionListQuery, Query()],
):
    return ResponseModel(
        code=200,
        message="success",
        data=await AgentDefinitionService(handler).list(query),
    )


@agent_definition_router.post("/agent-definitions/", response_model=ResponseModel)
async def create_agent_definition(
    body: AgentDefinitionCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
) -> ResponseModel:
    try:
        data = await AgentDefinitionService(handler).create(
            body.agent_config_template_id,
            body.runtime_template_id,
            body.agent_name,
        )
    except AgentDefinitionExists:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "agent_id_duplicated",
                "message": "an agent template already exists for this template pair",
            },
        ) from None
    except AgentNameDuplicated as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "agent_name_duplicated", "message": str(exc)},
        ) from None
    except TemplateUnavailable as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "template_unavailable", "message": str(exc)},
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@agent_definition_router.patch(
    "/agent-definitions/{agent_id}", response_model=ResponseModel
)
async def rename_agent_definition(
    agent_id: str,
    body: AgentDefinitionRenameBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    if not body.agent_name:
        raise HTTPException(status_code=400, detail="agent_name must not be empty")
    try:
        result = await AgentDefinitionService(handler).rename(
            agent_id, body.agent_name
        )
    except AgentNameDuplicated as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "agent_name_duplicated", "message": str(exc)},
        ) from None
    if result is None:
        raise HTTPException(status_code=404, detail="agent definition not found")
    return ResponseModel(code=200, message="success", data=result)


@agent_definition_router.delete(
    "/agent-definitions/{agent_id}", response_model=ResponseModel
)
async def delete_agent_definition(
    agent_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        deleted = await AgentDefinitionService(handler).delete(agent_id)
    except RegistrationError as exc:
        raise _registration_error(exc) from None
    if not deleted:
        raise HTTPException(status_code=404, detail="agent definition not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "agent_id": agent_id}
    )


def _registration_error(exc):
    status = 503 if isinstance(exc, RegistrationUnavailable) else 409 if isinstance(exc, RegistrationConflict) else 502
    return HTTPException(status_code=status, detail={
        "code": getattr(exc, "code", "request_failed"), "message": str(exc),
    })


async def _client_action(handler, agent_id, action):
    if not settings.agent_client_registration_enabled:
        raise HTTPException(status_code=404, detail="Agent client registration is disabled")
    try:
        result = await getattr(AgentClientService(handler), action)(agent_id)
    except RegistrationError as exc:
        raise _registration_error(exc) from None
    if result is None:
        raise HTTPException(status_code=404, detail="agent definition not found")
    return ResponseModel(code=200, message="success", data=result)


@agent_definition_router.get("/agent-definitions/{agent_id}/keycloak-client", response_model=ResponseModel)
async def get_agent_client(agent_id: str, response: Response, handler: Annotated[DBHandler, Depends(get_db_handler)]):
    response.headers["Cache-Control"] = "no-store"
    return await _client_action(handler, agent_id, "get")


@agent_definition_router.post("/agent-definitions/{agent_id}/keycloak-client", response_model=ResponseModel)
async def register_agent_client(agent_id: str, handler: Annotated[DBHandler, Depends(get_db_handler)]):
    return await _client_action(handler, agent_id, "register")


@agent_definition_router.delete("/agent-definitions/{agent_id}/keycloak-client", response_model=ResponseModel)
async def unregister_agent_client(agent_id: str, handler: Annotated[DBHandler, Depends(get_db_handler)]):
    return await _client_action(handler, agent_id, "remove")
