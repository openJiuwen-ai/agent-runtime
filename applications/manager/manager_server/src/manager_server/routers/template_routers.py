"""模板 CRUD API：model_template、extension_config_template、skill_prebuilt_template、
permissions_template、mcp_template、service_config_template（全局；服务配置同步 Runtime，其余可下发 Gateway）、agent_template、
service_config_container（容器模板，供运行时模板绑定）。
"""

from __future__ import annotations

from typing import Annotated
import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from openjiuwen_runtime.foundation.db.handler import DBHandler
from pydantic import BaseModel, ConfigDict, Field

from manager_server.core.template.agent_template import (
    AgentTemplateService,
    RegistrationUnavailable,
    TemplateMutationBusy,
)
from manager_server.core.template.a2a_access_policy_template import (
    A2AAccessPolicyTemplateService,
)
from manager_server.core.template.a2a_outbound_template import A2AOutboundTemplateService
from manager_server.core.template.a2a_discovery import A2ADiscoveryError, create_candidate
from manager_server.core.template.a2a_discovery_settings import A2ADiscoverySettingsService
from manager_server.core.template.embedding_template import (
    EmbeddingTemplateService,
)
from manager_server.core.template.expert_package_template import ExpertPackageTemplateService
from manager_server.core.template.extension_config_template import (
    ExtensionConfigTemplateService,
)
from manager_server.core.template.model_template import ModelTemplateService
from manager_server.core.template.mcp_template import McpTemplateService
from manager_server.core.template.permissions_template import (
    PermissionsTemplateService,
)
from manager_server.core.template.service_config_container import (
    ServiceConfigContainerService,
)
from manager_server.core.template.service_config_template import (
    ServiceConfigTemplateService,
)
from manager_server.core.template.deployment_defaults import (
    DeploymentDefaultsChanged,
    DefaultContainerConflict,
    deployment_defaults_status,
    initialize_deployment_defaults,
    preview_deployment_defaults,
    restore_deployment_defaults,
    preview_default_containers,
    restore_default_container,
)
from manager_server.core.template.skill_prebuilt_template import (
    SkillPrebuiltTemplateService,
)
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import get_db_handler
from manager_server.integrations.keycloak.agent_clients import RegistrationConflict, RegistrationError
from manager_server.routers.auth_guards import require_admin
from manager_server.schemas.common_schemas import ResponseModel
from manager_server.schemas.template_schemas import (
    A2AAccessPolicyTemplateCreateBody,
    A2AAccessPolicyTemplateListQuery,
    A2AAccessPolicyTemplateUpdateBody,
    A2AOutboundTemplateCreateBody,
    A2AOutboundDiscoveryBody,
    A2AConfirmRevisionBody,
    A2ADiscoverySettingsBody,
    A2AOutboundTemplateListQuery,
    A2AOutboundTemplateUpdateBody,
    AgentTemplateCreateBody,
    AgentTemplateListQuery,
    AgentTemplateUpdateBody,
    EmbeddingTemplateCreateBody,
    EmbeddingTemplateListQuery,
    EmbeddingTemplateUpdateBody,
    ExpertPackageTemplateCreateBody,
    ExpertPackageTemplateListQuery,
    ExpertPackageTemplateUpdateBody,
    ExtensionConfigTemplateCreateBody,
    ExtensionConfigTemplateListQuery,
    ExtensionConfigTemplateUpdateBody,
    ModelTemplateCreateBody,
    ModelTemplateListQuery,
    ModelTemplateUpdateBody,
    McpTemplateCreateBody,
    McpTemplateListQuery,
    McpTemplateUpdateBody,
    PermissionsTemplateCreateBody,
    PermissionsTemplateListQuery,
    PermissionsTemplateUpdateBody,
    ServiceConfigContainerCreateBody,
    ContainerDeploymentDefaultsRestoreBody,
    ServiceConfigContainerListQuery,
    ServiceConfigContainerUpdateBody,
    ServiceConfigTemplateCreateBody,
    DeploymentDefaultsRestoreBody,
    ServiceConfigTemplateListQuery,
    ServiceConfigTemplateUpdateBody,
    SkillPrebuiltTemplateCreateBody,
    SkillPrebuiltTemplateListQuery,
    SkillPrebuiltTemplateUpdateBody,
    TemplateIdPath,
)

templates_router = APIRouter()
a2a_templates_router = APIRouter(dependencies=[Depends(require_admin)])


def _a2a_outbound_template_svc(handler: DBHandler) -> A2AOutboundTemplateService:
    return A2AOutboundTemplateService(handler)


def _a2a_access_policy_template_svc(handler: DBHandler) -> A2AAccessPolicyTemplateService:
    return A2AAccessPolicyTemplateService(handler)


def _model_template_svc(handler: DBHandler) -> ModelTemplateService:
    return ModelTemplateService(handler)


def _embedding_template_svc(handler: DBHandler) -> EmbeddingTemplateService:
    return EmbeddingTemplateService(handler)


def _extension_config_template_svc(handler: DBHandler) -> ExtensionConfigTemplateService:
    return ExtensionConfigTemplateService(handler)


def _skill_prebuilt_template_svc(handler: DBHandler) -> SkillPrebuiltTemplateService:
    return SkillPrebuiltTemplateService(handler)


def _expert_package_template_svc(handler: DBHandler) -> ExpertPackageTemplateService:
    return ExpertPackageTemplateService(handler)


def _permissions_template_svc(handler: DBHandler) -> PermissionsTemplateService:
    return PermissionsTemplateService(handler)


def _mcp_template_svc(handler: DBHandler) -> McpTemplateService:
    return McpTemplateService(handler)


def _service_config_template_svc(handler: DBHandler) -> ServiceConfigTemplateService:
    return ServiceConfigTemplateService(handler)


def _service_config_container_svc(handler: DBHandler) -> ServiceConfigContainerService:
    return ServiceConfigContainerService(handler)


def _agent_template_svc(handler: DBHandler) -> AgentTemplateService:
    return AgentTemplateService(handler)


# --- a2a_outbound_template ---


@a2a_templates_router.get("/a2a-discovery-settings", response_model=ResponseModel)
async def get_a2a_discovery_settings(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    data = await A2ADiscoverySettingsService(handler).get()
    return ResponseModel(code=200, message="success", data=data.model_dump())


@a2a_templates_router.put("/a2a-discovery-settings", response_model=ResponseModel)
async def update_a2a_discovery_settings(
    body: A2ADiscoverySettingsBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    service = A2ADiscoverySettingsService(handler)
    data = await service.update(body)
    errors: list[Exception] = []
    try:
        await _a2a_outbound_template_svc(handler).disable_disallowed(data)
    except Exception as exc:
        errors.append(exc)
    try:
        await service.sync(data)
    except Exception as exc:
        errors.append(exc)
    if errors:
        raise HTTPException(
            status_code=502,
            detail="Network settings saved, but Gateway synchronization is incomplete. Save again to retry.",
        ) from errors[0]
    return ResponseModel(code=200, message="success", data=data.model_dump())


@a2a_templates_router.post(
    "/a2a-outbound-templates",
    response_model=ResponseModel,
)
async def create_a2a_outbound_template(
    body: A2AOutboundTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    """Register a server-discovered A2A Agent candidate."""
    try:
        data = await _a2a_outbound_template_svc(handler).create(body)
    except (A2ADiscoveryError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@a2a_templates_router.post("/a2a-outbound-discoveries", response_model=ResponseModel)
async def discover_a2a_outbound_template(
    body: A2AOutboundDiscoveryBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        data = await create_candidate(handler, body.url, body.card_path)
    except A2ADiscoveryError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": exc.summary}
        ) from exc
    return ResponseModel(code=200, message="success", data=data)


@a2a_templates_router.get("/a2a-outbound-templates", response_model=ResponseModel)
async def list_a2a_outbound_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[A2AOutboundTemplateListQuery, Query()],
):
    data = await _a2a_outbound_template_svc(handler).list_templates(query)
    return ResponseModel(code=200, message="success", data=data)


@a2a_templates_router.get(
    "/a2a-outbound-templates/{template_id}/edit", response_model=ResponseModel
)
async def edit_a2a_outbound_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    row = await _a2a_outbound_template_svc(handler).get_for_edit(template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="a2a outbound template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@a2a_templates_router.get("/a2a-outbound-templates/{template_id}", response_model=ResponseModel)
async def get_a2a_outbound_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    row = await _a2a_outbound_template_svc(handler).get(template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="a2a outbound template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@a2a_templates_router.patch("/a2a-outbound-templates/{template_id}", response_model=ResponseModel)
async def update_a2a_outbound_template(
    template_id: TemplateIdPath,
    body: A2AOutboundTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    row = await _a2a_outbound_template_svc(handler).update(template_id, body)
    if row is None:
        raise HTTPException(status_code=404, detail="a2a outbound template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@a2a_templates_router.delete("/a2a-outbound-templates/{template_id}", response_model=ResponseModel)
async def delete_a2a_outbound_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        deleted = await _a2a_outbound_template_svc(handler).delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="a2a outbound template not found")
    return ResponseModel(code=200, message="success", data={"deleted": True})


@a2a_templates_router.post(
    "/a2a-outbound-templates/{template_id}:refresh", response_model=ResponseModel
)
async def refresh_a2a_outbound_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        row = await _a2a_outbound_template_svc(handler).refresh(template_id)
    except A2ADiscoveryError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": exc.summary}
        ) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="a2a outbound template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@a2a_templates_router.post(
    "/a2a-outbound-templates/{template_id}:confirm-revision",
    response_model=ResponseModel,
)
async def confirm_a2a_outbound_template_revision(
    template_id: TemplateIdPath,
    body: A2AConfirmRevisionBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        row = await _a2a_outbound_template_svc(handler).confirm_revision(
            template_id, accept=body.accept
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="a2a outbound template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


# --- a2a_access_policy_template ---


@a2a_templates_router.post("/a2a-access-policies", response_model=ResponseModel)
async def create_a2a_access_policy(
    body: A2AAccessPolicyTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        data = await _a2a_access_policy_template_svc(handler).create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@a2a_templates_router.get("/a2a-access-policies", response_model=ResponseModel)
async def list_a2a_access_policies(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[A2AAccessPolicyTemplateListQuery, Query()],
):
    data = await _a2a_access_policy_template_svc(handler).list_templates(query)
    return ResponseModel(code=200, message="success", data=data)


@a2a_templates_router.get("/a2a-access-policies/{policy_id}", response_model=ResponseModel)
async def get_a2a_access_policy(
    policy_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    row = await _a2a_access_policy_template_svc(handler).get(policy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="a2a access policy not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@a2a_templates_router.patch("/a2a-access-policies/{policy_id}", response_model=ResponseModel)
async def update_a2a_access_policy(
    policy_id: TemplateIdPath,
    body: A2AAccessPolicyTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        row = await _a2a_access_policy_template_svc(handler).update(policy_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="a2a access policy not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@a2a_templates_router.delete("/a2a-access-policies/{policy_id}", response_model=ResponseModel)
async def delete_a2a_access_policy(
    policy_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        deleted = await _a2a_access_policy_template_svc(handler).delete(policy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="a2a access policy not found")
    return ResponseModel(code=200, message="success", data={"deleted": True})


# --- agent_template ---


templates_router.include_router(a2a_templates_router)


@templates_router.get("/agent-templates/", response_model=ResponseModel)
async def list_agent_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[AgentTemplateListQuery, Query()],
):
    return ResponseModel(
        code=200, message="success", data=await _agent_template_svc(handler).list(query)
    )


@templates_router.post("/agent-templates/", response_model=ResponseModel)
async def create_agent_template(
    body: AgentTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        return ResponseModel(
            code=200, message="success", data=await _agent_template_svc(handler).create(body)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@templates_router.get(
    "/agent-templates/{template_id}",
    response_model=ResponseModel,
)
async def get_agent_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    row = await _agent_template_svc(handler).get(template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="agent_template not found")
    return ResponseModel(code=200, message="success", data=row)


class AgentClientRegistrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_id: str = Field(min_length=1, max_length=255)


@templates_router.post("/agent-templates/{template_id}/keycloak-client", response_model=ResponseModel)
async def register_agent_client(
    template_id: TemplateIdPath,
    body: AgentClientRegistrationBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    if not settings.agent_client_registration_enabled:
        raise HTTPException(status_code=404, detail="Agent client registration is disabled")
    try:
        binding = await _agent_template_svc(handler).register_client(template_id, body.client_id)
    except RegistrationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except RegistrationConflict as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from None
    except RegistrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    if binding is None:
        raise HTTPException(status_code=404, detail="agent_template not found")
    return ResponseModel(code=200, message="success", data=binding)


@templates_router.patch(
    "/agent-templates/{template_id}",
    response_model=ResponseModel,
)
async def update_agent_template(
    template_id: TemplateIdPath,
    body: AgentTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        row = await _agent_template_svc(handler).update(template_id, body)
    except TemplateMutationBusy as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from None
    except RegistrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="agent_template not found")
    return ResponseModel(code=200, message="success", data=row)


@templates_router.delete(
    "/agent-templates/{template_id}",
    response_model=ResponseModel,
)
async def delete_agent_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        ok = await _agent_template_svc(handler).delete(template_id)
    except TemplateMutationBusy as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from None
    except RegistrationUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Enable and configure Keycloak registration before deleting a bound template",
        ) from None
    except RegistrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
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


# --- embedding_template ---


@templates_router.post("/embedding-templates", response_model=ResponseModel)
async def create_embedding_template(
    body: EmbeddingTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _embedding_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/embedding-templates", response_model=ResponseModel)
async def list_embedding_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[EmbeddingTemplateListQuery, Query()],
):
    svc = _embedding_template_svc(handler)
    try:
        data = await svc.list_templates(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get("/embedding-templates/{template_id}", response_model=ResponseModel)
async def get_embedding_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _embedding_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="embedding template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.patch("/embedding-templates/{template_id}", response_model=ResponseModel)
async def update_embedding_template(
    template_id: TemplateIdPath,
    body: EmbeddingTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _embedding_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="embedding template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete("/embedding-templates/{template_id}", response_model=ResponseModel)
async def delete_embedding_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _embedding_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="embedding template not found")
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


@templates_router.get("/extension-config-templates/{template_id}", response_model=ResponseModel)
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


@templates_router.patch("/extension-config-templates/{template_id}", response_model=ResponseModel)
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


@templates_router.delete("/extension-config-templates/{template_id}", response_model=ResponseModel)
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


# --- skill_prebuilt_template ---


@templates_router.post("/skill-prebuilt-templates", response_model=ResponseModel)
async def create_skill_prebuilt_template(
    body: SkillPrebuiltTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_prebuilt_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/skill-prebuilt-templates", response_model=ResponseModel)
async def list_skill_prebuilt_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[SkillPrebuiltTemplateListQuery, Query()],
):
    svc = _skill_prebuilt_template_svc(handler)
    try:
        data = await svc.list_templates(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get(
    "/skill-prebuilt-templates/{template_id}", response_model=ResponseModel
)
async def get_skill_prebuilt_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_prebuilt_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="skill prebuilt template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.patch(
    "/skill-prebuilt-templates/{template_id}", response_model=ResponseModel
)
async def update_skill_prebuilt_template(
    template_id: TemplateIdPath,
    body: SkillPrebuiltTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_prebuilt_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="skill prebuilt template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete(
    "/skill-prebuilt-templates/{template_id}", response_model=ResponseModel
)
async def delete_skill_prebuilt_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _skill_prebuilt_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="skill prebuilt template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )


# --- expert_package_template ---


@templates_router.post("/expert-package-templates", response_model=ResponseModel)
async def create_expert_package_template(
    body: ExpertPackageTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _expert_package_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/expert-package-templates", response_model=ResponseModel)
async def list_expert_package_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[ExpertPackageTemplateListQuery, Query()],
):
    svc = _expert_package_template_svc(handler)
    try:
        data = await svc.list_templates(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get(
    "/expert-package-templates/{template_id}", response_model=ResponseModel
)
async def get_expert_package_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _expert_package_template_svc(handler)
    row = await svc.get(template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="expert package template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.patch(
    "/expert-package-templates/{template_id}", response_model=ResponseModel
)
async def update_expert_package_template(
    template_id: TemplateIdPath,
    body: ExpertPackageTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _expert_package_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="expert package template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete(
    "/expert-package-templates/{template_id}", response_model=ResponseModel
)
async def delete_expert_package_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _expert_package_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="expert package template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )


# --- permissions_template ---


@templates_router.post("/permissions-templates", response_model=ResponseModel)
async def create_permissions_template(
    body: PermissionsTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _permissions_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/permissions-templates", response_model=ResponseModel)
async def list_permissions_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[PermissionsTemplateListQuery, Query()],
):
    svc = _permissions_template_svc(handler)
    try:
        data = await svc.list_templates(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get("/permissions-templates/{template_id}", response_model=ResponseModel)
async def get_permissions_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _permissions_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="permissions template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.patch("/permissions-templates/{template_id}", response_model=ResponseModel)
async def update_permissions_template(
    template_id: TemplateIdPath,
    body: PermissionsTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _permissions_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="permissions template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete("/permissions-templates/{template_id}", response_model=ResponseModel)
async def delete_permissions_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _permissions_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="permissions template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )


# --- mcp_template ---


@templates_router.post("/mcp-templates", response_model=ResponseModel)
async def create_mcp_template(
    body: McpTemplateCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _mcp_template_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/mcp-templates", response_model=ResponseModel)
async def list_mcp_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[McpTemplateListQuery, Query()],
):
    svc = _mcp_template_svc(handler)
    try:
        data = await svc.list_templates(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get("/mcp-templates/{template_id}", response_model=ResponseModel)
async def get_mcp_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _mcp_template_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="mcp template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.patch("/mcp-templates/{template_id}", response_model=ResponseModel)
async def update_mcp_template(
    template_id: TemplateIdPath,
    body: McpTemplateUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _mcp_template_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="mcp template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete("/mcp-templates/{template_id}", response_model=ResponseModel)
async def delete_mcp_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _mcp_template_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="mcp template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )


# --- deployment generated runtime config discovery ---

@templates_router.get("/service-config-templates/agentserver-discovery", response_model=ResponseModel)
async def discover_agentserver_config():
    """Read deployment-generated agentserver.json on the Manager host.

    The path is explicit through AGENTSERVER_JSON_PATH or MANAGER_DEPLOY_TOOL_DIR;
    the browser never needs access to the server filesystem.
    """
    candidates = []
    configured = os.getenv("AGENTSERVER_JSON_PATH")
    tool_dir = os.getenv("MANAGER_DEPLOY_TOOL_DIR")
    if configured:
        candidates.append(Path(configured))
    if tool_dir:
        candidates.append(Path(tool_dir) / "conf" / "agentserver.json")
    for path in candidates:
        try:
            if path.is_file():
                return ResponseModel(code=200, message="success", data={"found": True, "source": str(path), "config": json.loads(path.read_text(encoding="utf-8"))})
        except (OSError, json.JSONDecodeError) as exc:
            return ResponseModel(code=200, message="config_error", data={"found": False, "source": str(path), "error": str(exc)})
    return ResponseModel(code=200, message="not_found", data={"found": False, "configured": bool(candidates)})


# --- service_config_template ---


@templates_router.get("/service-config-templates/deployment-defaults", response_model=ResponseModel)
async def get_deployment_defaults(handler: Annotated[DBHandler, Depends(get_db_handler)]):
    return ResponseModel(code=200, message="success", data=await deployment_defaults_status(handler))


@templates_router.post("/service-config-templates/deployment-defaults/initialize", response_model=ResponseModel)
async def prepare_deployment_defaults(handler: Annotated[DBHandler, Depends(get_db_handler)]):
    return ResponseModel(code=200, message="success", data=await initialize_deployment_defaults(handler))


@templates_router.get("/service-config-templates/deployment-defaults/preview", response_model=ResponseModel)
async def get_deployment_defaults_preview():
    return ResponseModel(code=200, message="success", data=preview_deployment_defaults())


@templates_router.post("/service-config-templates/deployment-defaults/restore", response_model=ResponseModel)
async def save_deployment_defaults_copy(
    body: DeploymentDefaultsRestoreBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        saved = await restore_deployment_defaults(handler, body)
    except DeploymentDefaultsChanged as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=saved.model_dump())


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


@templates_router.get("/service-config-templates/{template_id}", response_model=ResponseModel)
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


@templates_router.patch("/service-config-templates/{template_id}", response_model=ResponseModel)
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


@templates_router.delete("/service-config-templates/{template_id}", response_model=ResponseModel)
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


# --- service_config_container（容器模板）---


@templates_router.get("/container-templates/deployment-defaults/preview", response_model=ResponseModel)
async def get_default_container_preview():
    return ResponseModel(code=200, message="success", data=preview_default_containers())


@templates_router.post("/container-templates/deployment-defaults/restore", response_model=ResponseModel)
async def save_default_container_copy(
    body: ContainerDeploymentDefaultsRestoreBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        saved = await restore_default_container(handler, body)
    except (DeploymentDefaultsChanged, DefaultContainerConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=saved.model_dump())


@templates_router.post("/container-templates", response_model=ResponseModel)
async def create_container_template(
    body: ServiceConfigContainerCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_config_container_svc(handler)
    try:
        data = await svc.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data.model_dump())


@templates_router.get("/container-templates", response_model=ResponseModel)
async def list_container_templates(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[ServiceConfigContainerListQuery, Query()],
):
    svc = _service_config_container_svc(handler)
    try:
        data = await svc.list_containers(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@templates_router.get("/container-templates/{template_id}", response_model=ResponseModel)
async def get_container_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_config_container_svc(handler)
    try:
        row = await svc.get(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="container template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.patch("/container-templates/{template_id}", response_model=ResponseModel)
async def update_container_template(
    template_id: TemplateIdPath,
    body: ServiceConfigContainerUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_config_container_svc(handler)
    try:
        row = await svc.update(template_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="container template not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@templates_router.delete("/container-templates/{template_id}", response_model=ResponseModel)
async def delete_container_template(
    template_id: TemplateIdPath,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _service_config_container_svc(handler)
    try:
        ok = await svc.delete(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="container template not found")
    return ResponseModel(
        code=200, message="success", data={"deleted": True, "template_id": template_id}
    )
