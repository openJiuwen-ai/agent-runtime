"""模板 CRUD API：model_template、extension_config_template、skill_whitelist_template、
service_config_template（全局模板，变更同步至所有已连接 Gateway）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.agent_template import AgentTemplateService
from manager_server.core.template.extension_config_template import (
    ExtensionConfigTemplateService,
)
from manager_server.core.template.model_template import ModelTemplateService
from manager_server.core.template.service_config_template import (
    ServiceConfigTemplateService,
)
from manager_server.core.template.skill_whitelist_template import (
    SkillWhitelistTemplateService,
)
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.deps import require_admin
from manager_server.schemas.common_schemas import ResponseModel
from manager_server.schemas.template_schemas import (
    AgentTemplateCreateBody,
    AgentTemplateListQuery,
    AgentTemplateUpdateBody,
    ExtensionConfigTemplateCreateBody,
    ExtensionConfigTemplateListQuery,
    ExtensionConfigTemplateUpdateBody,
    ModelTemplateCreateBody,
    ModelTemplateListQuery,
    ModelTemplateUpdateBody,
    ServiceConfigTemplateCreateBody,
    ServiceConfigTemplateListQuery,
    ServiceConfigTemplateUpdateBody,
    SkillWhitelistTemplateCreateBody,
    SkillWhitelistTemplateListQuery,
    SkillWhitelistTemplateUpdateBody,
    TemplateIdPath,
)

templates_router = APIRouter()


def _model_template_svc(handler: DBHandler) -> ModelTemplateService:
    return ModelTemplateService(handler)


def _extension_config_template_svc(handler: DBHandler) -> ExtensionConfigTemplateService:
    return ExtensionConfigTemplateService(handler)


def _skill_whitelist_template_svc(handler: DBHandler) -> SkillWhitelistTemplateService:
    return SkillWhitelistTemplateService(handler)


def _service_config_template_svc(handler: DBHandler) -> ServiceConfigTemplateService:
    return ServiceConfigTemplateService(handler)


def _agent_template_svc(handler: DBHandler) -> AgentTemplateService:
    return AgentTemplateService(handler)


# --- agent_template ---


@templates_router.get("/agent-templates/", response_model=ResponseModel, dependencies=[Depends(require_admin)])
async def list_agent_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[AgentTemplateListQuery, Query()],
):
    return ResponseModel(code=200, message="success", data=await _agent_template_svc(handler).list(query))


@templates_router.post("/agent-templates/", response_model=ResponseModel, dependencies=[Depends(require_admin)])
async def create_agent_template(
    body: AgentTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        return ResponseModel(code=200, message="success", data=await _agent_template_svc(handler).create(body))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@templates_router.get(
    "/agent-templates/{template_id}",
    response_model=ResponseModel,
    dependencies=[Depends(require_admin)],
)
async def get_agent_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    row = await _agent_template_svc(handler).get(template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="agent_template not found")
    return ResponseModel(code=200, message="success", data=row)


@templates_router.patch(
    "/agent-templates/{template_id}",
    response_model=ResponseModel,
    dependencies=[Depends(require_admin)],
)
async def update_agent_template(
    template_id: TemplateIdPath,
    body: AgentTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    row = await _agent_template_svc(handler).update(template_id, body)
    if row is None:
        raise HTTPException(status_code=404, detail="agent_template not found")
    return ResponseModel(code=200, message="success", data=row)


@templates_router.delete(
    "/agent-templates/{template_id}",
    response_model=ResponseModel,
    dependencies=[Depends(require_admin)],
)
async def delete_agent_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    ok = await _agent_template_svc(handler).delete(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="agent_template not found")
    return ResponseModel(code=200, message="success", data={"deleted": True})


# --- model_template ---


@templates_router.post("/model-templates", response_model=ResponseModel)
async def create_model_template(
    body: ModelTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _model_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/model-templates", response_model=ResponseModel)
async def list_model_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[ModelTemplateListQuery, Query()],
):
    svc = _model_template_svc(handler)
    try:
        data = await svc.list_templates(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get("/model-templates/{template_id}", response_model=ResponseModel)
async def get_model_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _model_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.patch("/model-templates/{template_id}", response_model=ResponseModel)
async def update_model_template(
    template_id: TemplateIdPath,
    body: ModelTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _model_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete("/model-templates/{template_id}", response_model=ResponseModel)
async def delete_model_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _model_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="model template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )


# --- extension_config_template ---


@templates_router.post("/extension-config-templates", response_model=ResponseModel)
async def create_extension_config_template(
    body: ExtensionConfigTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _extension_config_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/extension-config-templates", response_model=ResponseModel)
async def list_extension_config_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[ExtensionConfigTemplateListQuery, Query()],
):
    svc = _extension_config_template_svc(handler)
    try:
        data = await svc.list_templates(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get(
    "/extension-config-templates/{template_id}", response_model=ResponseModel
)
async def get_extension_config_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _extension_config_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="extension config template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.patch(
    "/extension-config-templates/{template_id}", response_model=ResponseModel
)
async def update_extension_config_template(
    template_id: TemplateIdPath,
    body: ExtensionConfigTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _extension_config_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="extension config template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete(
    "/extension-config-templates/{template_id}", response_model=ResponseModel
)
async def delete_extension_config_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _extension_config_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="extension config template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )


# --- skill_whitelist_template ---


@templates_router.post("/skill-whitelist-templates", response_model=ResponseModel)
async def create_skill_whitelist_template(
    body: SkillWhitelistTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_whitelist_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/skill-whitelist-templates", response_model=ResponseModel)
async def list_skill_whitelist_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[SkillWhitelistTemplateListQuery, Query()],
):
    svc = _skill_whitelist_template_svc(handler)
    try:
        data = await svc.list_templates(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get(
    "/skill-whitelist-templates/{template_id}", response_model=ResponseModel
)
async def get_skill_whitelist_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_whitelist_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="skill whitelist template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.patch(
    "/skill-whitelist-templates/{template_id}", response_model=ResponseModel
)
async def update_skill_whitelist_template(
    template_id: TemplateIdPath,
    body: SkillWhitelistTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_whitelist_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="skill whitelist template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete(
    "/skill-whitelist-templates/{template_id}", response_model=ResponseModel
)
async def delete_skill_whitelist_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_whitelist_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="skill whitelist template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )


# --- service_config_template ---


@templates_router.post("/service-config-templates", response_model=ResponseModel)
async def create_service_config_template(
    body: ServiceConfigTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_config_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/service-config-templates", response_model=ResponseModel)
async def list_service_config_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[ServiceConfigTemplateListQuery, Query()],
):
    svc = _service_config_template_svc(handler)
    try:
        data = await svc.list_templates(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get(
    "/service-config-templates/{template_id}", response_model=ResponseModel
)
async def get_service_config_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_config_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="service config template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.patch(
    "/service-config-templates/{template_id}", response_model=ResponseModel
)
async def update_service_config_template(
    template_id: TemplateIdPath,
    body: ServiceConfigTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_config_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="service config template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete(
    "/service-config-templates/{template_id}", response_model=ResponseModel
)
async def delete_service_config_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_config_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="service config template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )
