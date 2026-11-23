"""Agent 模板 API：列表、新增、改名、删除（管理面 ``require_admin``）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.agent_definition.service import (
    AgentDefinitionExists,
    AgentDefinitionService,
    AgentNameDuplicated,
    TemplateUnavailable,
)
from manager_server.infrastructure.db import get_db_handler
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
    deleted = await AgentDefinitionService(handler).delete(agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="agent definition not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "agent_id": agent_id}
    )
