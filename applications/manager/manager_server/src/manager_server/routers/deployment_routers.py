"""管理面启动部署 API（admin 子树：产品面 up + 幂等重跑升级，v1 仅本集群）。

执行链路见 ``core/deployment/service.py``：创建 pending 任务 → 后台执行循环认领 →
子进程代跑 deploy.sh（结构化进度/日志落库）→ 成功后自动注册实例。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.deployment.env_builder import (
    MANAGER_PLANE_IMAGE_KEYS,
    mask_sensitive_env,
)
from manager_server.core.deployment.service import (
    DeploymentError,
    DeploymentService,
)
from manager_server.core.deployment.toolkit import (
    check_tool_available,
    read_tool_version,
)
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.auth_guards import AdminUser
from manager_server.schemas.common_schemas import ResponseModel
from manager_server.schemas.deployment_schemas import (
    DeploymentCreateBody,
    DeploymentDefaultsUpdateBody,
    DeploymentListQuery,
)

deployment_router = APIRouter()


def _svc(handler: DBHandler) -> DeploymentService:
    return DeploymentService(handler)


@deployment_router.get("/defaults", response_model=ResponseModel)
async def get_deployment_defaults(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    """默认部署参数（密码掩码；管理面组件镜像不展示——部署功能只部署产品面）+ 工具版本 + 可用性。"""
    available, reason = check_tool_available()
    defaults = await _svc(handler).get_defaults()
    visible = {
        key: value
        for key, value in defaults.items()
        if key not in MANAGER_PLANE_IMAGE_KEYS
    }
    return ResponseModel(
        code=200,
        message="success",
        data={
            "env": mask_sensitive_env(visible),
            "tool_version": read_tool_version(),
            "deploy_enabled": bool(settings.deploy_enabled),
            "available": available,
            "unavailable_reason": reason,
        },
    )


@deployment_router.put("/defaults", response_model=ResponseModel)
async def update_deployment_defaults(
    body: DeploymentDefaultsUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    admin: AdminUser,
):
    """更新默认部署参数（白名单键；掩码值原样传回时不覆盖）。"""
    svc = _svc(handler)
    try:
        merged = await svc.update_defaults(body.env, actor=str(getattr(admin, "user_id", "") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(
        code=200, message="success", data={"env": mask_sensitive_env(merged)}
    )


@deployment_router.post("/", response_model=ResponseModel)
async def create_deployment(
    body: DeploymentCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    admin: AdminUser,
):
    """创建部署任务（同命名空间同时仅一个活跃任务；409 冲突 / 503 功能不可用）。"""
    svc = _svc(handler)
    try:
        data = await svc.create_task(body, actor=str(getattr(admin, "user_id", "") or ""))
    except DeploymentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@deployment_router.get("/", response_model=ResponseModel)
async def list_deployments(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[DeploymentListQuery, Query()],
):
    data = await _svc(handler).list_tasks(query)
    return ResponseModel(code=200, message="success", data=data)


@deployment_router.get("/{deployment_id}", response_model=ResponseModel)
async def get_deployment(
    deployment_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    task = await _svc(handler).get_task(deployment_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ResponseModel(code=200, message="success", data=task)


@deployment_router.get("/{deployment_id}/logs", response_model=ResponseModel)
async def get_deployment_logs(
    deployment_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
):
    """增量拉取任务日志（after_seq 游标）。"""
    data = await _svc(handler).list_logs(
        deployment_id, after_seq=after_seq, limit=limit
    )
    if data is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ResponseModel(code=200, message="success", data=data)


@deployment_router.post("/{deployment_id}/cancel", response_model=ResponseModel)
async def cancel_deployment(
    deployment_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    admin: AdminUser,
):
    """取消任务（running → 请求标记，runner SIGTERM 进程组；幂等冲突 409）。"""
    try:
        data = await _svc(handler).request_cancel(deployment_id)
    except DeploymentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)
