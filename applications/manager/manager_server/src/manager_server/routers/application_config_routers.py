"""应用配置 API：日志、记忆、审计，以及实例级工作区配额。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler
from pydantic import BaseModel, ConfigDict, Field

from manager_server.core.application_config.task_memory_config import (TaskMemoryConfigService,
                                                                           TaskMemoryUpsertParams)

from manager_server.core.application_config.log_masking_rule import (
    LogMaskingRuleService,
)
from manager_server.core.application_config.logging_config import LoggingConfigService
from manager_server.core.application_config.memory_config import MemoryConfigService
from manager_server.core.application_config.audit_log_config import AuditLogConfigService
from manager_server.core.application_config.workspace_quota_policy import (
    WorkspaceQuotaPolicyService,
)
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.deps import AdminUser
from manager_server.schemas.application_config_schemas import (
    LogMaskingRuleCreateBody,
    LogMaskingRuleListQuery,
    LogMaskingRuleUpdateBody,
    WorkspaceQuotaPolicyCreateBody,
    WorkspaceQuotaPolicyListQuery,
    WorkspaceQuotaPolicyPatchBody,
)
from manager_server.schemas.common_schemas import ResponseModel

application_config_router = APIRouter()


def _task_memory_config_svc(handler: DBHandler) -> TaskMemoryConfigService:
    return TaskMemoryConfigService(handler)


def _log_masking_rule_svc(handler: DBHandler) -> LogMaskingRuleService:
    return LogMaskingRuleService(handler)


def _logging_config_svc(handler: DBHandler) -> LoggingConfigService:
    return LoggingConfigService(handler)


def _memory_config_svc(handler: DBHandler) -> MemoryConfigService:
    return MemoryConfigService(handler)


def _audit_log_config_svc(handler: DBHandler) -> AuditLogConfigService:
    return AuditLogConfigService(handler)


@application_config_router.get(
    "/{jiuwenclaw_id}/log-masking-rules",
    response_model=ResponseModel,
)
async def list_log_masking_rules(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[LogMaskingRuleListQuery, Query()],
):
    svc = _log_masking_rule_svc(handler)
    try:
        data = await svc.list(jiuwenclaw_id, query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.get(
    "/{jiuwenclaw_id}/log-masking-rules/{rule_id}",
    response_model=ResponseModel,
)
async def get_log_masking_rule(
    jiuwenclaw_id: str,
    rule_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _log_masking_rule_svc(handler)
    try:
        row = await svc.get(jiuwenclaw_id, rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="log masking rule not found")
    return ResponseModel(code=200, message="success", data=row.model_dump(mode="json"))


@application_config_router.post(
    "/{jiuwenclaw_id}/log-masking-rules",
    response_model=ResponseModel,
)
async def create_log_masking_rule(
    jiuwenclaw_id: str,
    body: LogMaskingRuleCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _log_masking_rule_svc(handler)
    try:
        row = await svc.create(jiuwenclaw_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=row.model_dump(mode="json"))


@application_config_router.patch(
    "/{jiuwenclaw_id}/log-masking-rules/{rule_id}",
    response_model=ResponseModel,
)
async def patch_log_masking_rule(
    jiuwenclaw_id: str,
    rule_id: str,
    body: LogMaskingRuleUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _log_masking_rule_svc(handler)
    try:
        row = await svc.update(jiuwenclaw_id, rule_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="log masking rule not found")
    return ResponseModel(code=200, message="success", data=row.model_dump(mode="json"))


@application_config_router.delete(
    "/{jiuwenclaw_id}/log-masking-rules/{rule_id}",
    response_model=ResponseModel,
)
async def delete_log_masking_rule(
    jiuwenclaw_id: str,
    rule_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _log_masking_rule_svc(handler)
    try:
        await svc.delete(jiuwenclaw_id, rule_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success")


class LoggingConfigUpsertRequest(BaseModel):
    level: str = Field(default="INFO", max_length=16)
    console_level: str | None = Field(default=None, max_length=16)
    gateway: str | None = Field(default=None, max_length=16)
    channel: str | None = Field(default=None, max_length=16)
    agent_server: str | None = Field(default=None, max_length=16)
    full: str | None = Field(default=None, max_length=16)


@application_config_router.put(
    "/{jiuwenclaw_id}/logging", response_model=ResponseModel
)
async def upsert_logging_config(
    jiuwenclaw_id: str,
    body: LoggingConfigUpsertRequest,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _logging_config_svc(handler)
    try:
        data = await svc.upsert(
            jiuwenclaw_id=jiuwenclaw_id,
            level=body.level,
            console_level=body.console_level,
            gateway=body.gateway,
            channel=body.channel,
            agent_server=body.agent_server,
            full=body.full,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.get(
    "/{jiuwenclaw_id}/logging", response_model=ResponseModel
)
async def get_logging_config(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _logging_config_svc(handler)
    data = await svc.get(jiuwenclaw_id=jiuwenclaw_id)
    if data is None:
        raise HTTPException(status_code=404, detail="logging config not found")
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.delete(
    "/{jiuwenclaw_id}/logging", response_model=ResponseModel
)
async def delete_logging_config(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _logging_config_svc(handler)
    try:
        await svc.delete(jiuwenclaw_id=jiuwenclaw_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success")


class TaskMemoryUpsertRequest(BaseModel):
    enabled: bool = Field(default=False)
    llm_model: str = Field(default="", max_length=256)
    embedding_model: str = Field(default="", max_length=256)
    api_key: str = Field(default="", max_length=512)
    api_base: str = Field(default="", max_length=1024)
    retrieval_algo: str | None = Field(default=None, max_length=64)
    summary_algo: str | None = Field(default=None, max_length=64)


@application_config_router.put(
    "/{jiuwenclaw_id}/task-memory", response_model=ResponseModel
)
async def upsert_task_memory(
    jiuwenclaw_id: str,
    body: TaskMemoryUpsertRequest,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _task_memory_config_svc(handler)
    try:
        data = await svc.upsert(
            jiuwenclaw_id=jiuwenclaw_id,
            params=TaskMemoryUpsertParams(
                enabled=body.enabled,
                llm_model=body.llm_model,
                embedding_model=body.embedding_model,
                api_key=body.api_key,
                api_base=body.api_base,
                retrieval_algo=body.retrieval_algo,
                summary_algo=body.summary_algo,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.get(
    "/{jiuwenclaw_id}/task-memory", response_model=ResponseModel
)
async def get_task_memory(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _task_memory_config_svc(handler)
    try:
        data = await svc.get(jiuwenclaw_id=jiuwenclaw_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.delete(
    "/{jiuwenclaw_id}/task-memory", response_model=ResponseModel
)
async def delete_task_memory(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _task_memory_config_svc(handler)
    try:
        await svc.delete(jiuwenclaw_id=jiuwenclaw_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success")


class MemoryConfigUpsertRequest(BaseModel):
    body: dict = Field(
        ...,
        description=(
            "memory 段配置，结构与 config.yaml::memory 一致（mode/engine/"
            "forbidden_memory_definition/external）"
        ),
    )


@application_config_router.put(
    "/{jiuwenclaw_id}/memory", response_model=ResponseModel
)
async def upsert_memory_config(
    jiuwenclaw_id: str,
    body: MemoryConfigUpsertRequest,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _memory_config_svc(handler)
    try:
        data = await svc.upsert(jiuwenclaw_id=jiuwenclaw_id, body=body.body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.get(
    "/{jiuwenclaw_id}/memory", response_model=ResponseModel
)
async def get_memory_config(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _memory_config_svc(handler)
    data = await svc.get(jiuwenclaw_id=jiuwenclaw_id)
    if data is None:
        raise HTTPException(status_code=404, detail="memory config not found")
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.delete(
    "/{jiuwenclaw_id}/memory", response_model=ResponseModel
)
async def delete_memory_config(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _memory_config_svc(handler)
    try:
        await svc.delete(jiuwenclaw_id=jiuwenclaw_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success")


class AuditLogUpsertRequest(BaseModel):
    """§5.2 payload 作请求根对象；service 不接收（进程本地项）。"""

    model_config = ConfigDict(extra="forbid")

    format: dict[str, Any] = Field(..., description="字段清单 format")
    otel: dict[str, Any] | None = Field(default=None)
    ntp: dict[str, Any] | None = Field(default=None)
    data_center: str | None = Field(default=None, max_length=32)
    system_code: str | None = Field(default=None, max_length=64)
    node: str | None = Field(default=None, max_length=128)


@application_config_router.put(
    "/{jiuwenclaw_id}/audit-log", response_model=ResponseModel
)
async def upsert_audit_log_config(
    jiuwenclaw_id: str,
    body: AuditLogUpsertRequest,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _audit_log_config_svc(handler)
    payload = body.model_dump(exclude_unset=True)
    try:
        data = await svc.upsert(jiuwenclaw_id=jiuwenclaw_id, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.get(
    "/{jiuwenclaw_id}/audit-log", response_model=ResponseModel
)
async def get_audit_log_config(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _audit_log_config_svc(handler)
    data = await svc.get(jiuwenclaw_id=jiuwenclaw_id)
    if data is None:
        raise HTTPException(status_code=404, detail="audit log config not found")
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.delete(
    "/{jiuwenclaw_id}/audit-log", response_model=ResponseModel
)
async def delete_audit_log_config(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _audit_log_config_svc(handler)
    try:
        await svc.delete(jiuwenclaw_id=jiuwenclaw_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success")


def _workspace_quota_svc(handler: DBHandler) -> WorkspaceQuotaPolicyService:
    return WorkspaceQuotaPolicyService(handler)


def _actor(user: Any) -> str | None:
    uid = str(getattr(user, "user_id", "") or "").strip()
    return uid or None


@application_config_router.get(
    "/{jiuwenclaw_id}/workspace-quota/policies",
    response_model=ResponseModel,
)
async def list_workspace_quota_policies(
    jiuwenclaw_id: str,
    _admin: AdminUser,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[WorkspaceQuotaPolicyListQuery, Query()],
):
    data = await _workspace_quota_svc(handler).list_policies(
        cluster_id=jiuwenclaw_id,
        query=query,
    )
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.post(
    "/{jiuwenclaw_id}/workspace-quota/policies",
    response_model=ResponseModel,
)
async def create_workspace_quota_policy(
    jiuwenclaw_id: str,
    admin: AdminUser,
    body: WorkspaceQuotaPolicyCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        data = await _workspace_quota_svc(handler).create(
            cluster_id=jiuwenclaw_id,
            policy_name=body.policy_name,
            policy_desc=body.policy_desc,
            match_expr=body.match_expr,
            priority=body.priority,
            limit_bytes=body.limit_bytes,
            soft_percent=body.soft_percent,
            hard_percent=body.hard_percent,
            source_order_num=body.source_order_num,
            actor_id=_actor(admin),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.patch(
    "/{jiuwenclaw_id}/workspace-quota/policies/{policy_id}",
    response_model=ResponseModel,
)
async def patch_workspace_quota_policy(
    jiuwenclaw_id: str,
    policy_id: str,
    admin: AdminUser,
    body: WorkspaceQuotaPolicyPatchBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="at least one field is required")
    if "match_expr" in changes and changes["match_expr"] is None:
        raise HTTPException(status_code=400, detail="match_expr cannot be null")
    if "policy_name" in changes and changes["policy_name"] is None:
        raise HTTPException(status_code=400, detail="policy_name cannot be null")
    if "soft_percent" in changes and changes["soft_percent"] is None:
        raise HTTPException(status_code=400, detail="soft_percent cannot be null")
    if "hard_percent" in changes and changes["hard_percent"] is None:
        raise HTTPException(status_code=400, detail="hard_percent cannot be null")
    try:
        data = await _workspace_quota_svc(handler).update(
            policy_id,
            changes,
            actor_id=_actor(admin),
            cluster_id=jiuwenclaw_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="workspace quota policy not found")
    return ResponseModel(code=200, message="success", data=data)


@application_config_router.delete(
    "/{jiuwenclaw_id}/workspace-quota/policies/{policy_id}",
    response_model=ResponseModel,
)
async def delete_workspace_quota_policy(
    jiuwenclaw_id: str,
    policy_id: str,
    _admin: AdminUser,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        await _workspace_quota_svc(handler).delete(policy_id, cluster_id=jiuwenclaw_id)
    except ValueError as exc:
        message = str(exc)
        status = 404 if "not found" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    return ResponseModel(code=200, message="success", data=None)


@application_config_router.get(
    "/{jiuwenclaw_id}/workspace-quota/effective",
    response_model=ResponseModel,
)
async def get_workspace_quota_effective(
    jiuwenclaw_id: str,
    _admin: AdminUser,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    user_id: Annotated[str, Query(min_length=1)],
    group_id: Annotated[str, Query(min_length=1)],
    bot_id: Annotated[str, Query(min_length=1)],
):
    data = await _workspace_quota_svc(handler).effective(
        cluster_id=jiuwenclaw_id,
        user_id=user_id,
        group_id=group_id,
        bot_id=bot_id,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="no matching workspace quota policy")
    return ResponseModel(code=200, message="success", data=data)

