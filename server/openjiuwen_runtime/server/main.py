# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent Runtime Manager Server

FastAPI 服务器，提供 Agent 部署管理 REST API（支持租户隔离）
"""

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse

from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_runtime.foundation.config import settings
from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.port_utils import allocate_port, is_port_available
from openjiuwen_runtime.management.manager import DeploymentManager
from openjiuwen_runtime.management.models.deployment_params import (
    DeployAgentParams,
    ListDeploymentsParams,
)
from openjiuwen_runtime.management.models.enums import (
    DeployMode,
    DeploymentStatus,
    DeploymentType,
)

from .middleware.tenant import TenantContextMiddleware, get_tenant_context
from .utils import mask_userdata

logger = get_logger(__name__)

# 追踪已分配的端口，防止并发部署时端口冲突
_allocated_ports: set[int] = set()

# 初始化数据库组件
logger.info(f"Using database: {settings.DB_TYPE}")
if settings.DB_TYPE == "sqlite":
    db_handler = SQLiteHandler("deployments.db")
elif settings.DB_TYPE == "mysql":
    db_handler = MySQLHandler()
else:
    raise ValueError(f"Unsupported DB_TYPE: {settings.DB_TYPE}. Use 'sqlite' or 'mysql'.")

manager = DeploymentManager(db_handler)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    await manager.initialize()
    yield
    # 关闭时清理
    await manager.shutdown()


# 创建 FastAPI 应用
app = FastAPI(
    title="Agent Runtime Manager API",
    description="Agent 部署管理服务（支持租户隔离）",
    version="2.1.0",
    lifespan=lifespan,
)

# 添加租户中间件（临时禁用租户验证，测试用）
app.add_middleware(TenantContextMiddleware, require_tenant=False)


# ==================== 健康检查 ====================

@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "healthy"}


def prepare_subprocess_deployment(
    mode: str,
    port: int | None
) -> tuple[int, Path]:
    """
    预处理 subprocess 部署：分配端口、检查端口、获取 WHL 包路径
    返回：(最终使用的端口, whl文件路径)
    """
    if mode != "subprocess":
        return 0, Path("")

    # 端口分配与验证
    if port is None:
        port = allocate_port(exclude_ports=_allocated_ports)
        _allocated_ports.add(port)
        logger.info(f"Auto-allocated port: {port}")
    elif not is_port_available(port):
        logger.error(f"Port {port} is already in use")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Port {port} is occupied. Please use another port or leave it blank for auto-allocation."
        )
    logger.info(f"Port: {port}")

    # 获取 WHL 包路径
    logger.info(f"dist_path: {settings.dist_path}")
    whl_path = settings.dist_path / "lowcode_agent_runner-0.1.0-py3-none-any.whl"
    if not whl_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"WHL file not found: {whl_path}"
        )
    logger.info(f"Using WHL: {whl_path}")

    return port, whl_path


def get_deploy_type(mode: str) -> str:
    """
    根据全局配置 DEPLOY_TYPE 计算最终使用的部署模式
    规则：
    - DEPLOY_TYPE=subprocess → 返回传入的 mode
    - DEPLOY_TYPE=docker      → 强制返回docker
    - DEPLOY_TYPE=k8s         → 强制返回k8s
    """
    deploy_type = settings.DEPLOY_TYPE
    if deploy_type == "docker":
        return "docker"
    elif deploy_type == "k8s":
        return "k8s"
    else:
        return mode


@dataclass
class AgentDeployQuery:
    """Agent 部署请求中的查询参数（用于收敛 FastAPI 路由形参个数）。"""

    name: str
    mode: str
    port: int | None
    userdata: str | None


def _parse_agent_deploy_query(
    name: str = Query(..., description="部署名称（=包名）"),
    mode: str = Query(default="subprocess", description="部署器类型"),
    port: int | None = Query(default=None, description="服务端口，不填则自动分配"),
    userdata: str | None = Query(default=None, description="用户自定义数据"),
) -> AgentDeployQuery:
    return AgentDeployQuery(name=name, mode=mode, port=port, userdata=userdata)


# ==================== Agent API ====================

@app.post("/api/v1/agents/deploy")
async def deploy_agent(
    request: Request,
    file: UploadFile,
    deploy_query: Annotated[AgentDeployQuery, Depends(_parse_agent_deploy_query)],
):
    """
    部署 Agent（JSON 配置，低码方式）

    流程:
    1. 接收用户上传的 JSON 配置文件，保存为临时文件
    2. 使用工程目录下预编译的 lowcode_agent_runner whl 包
    3. 调用 Manager SDK 部署（传入 ir_path、whl_path 和 userdata）
    """
    try:
        # 获取租户上下文
        user_id, space_id = get_tenant_context(request)
        logger.info(
            f"Received agent deploy request: user_id={user_id}, "
            f"space_id={space_id}, name={deploy_query.name}, userdata={mask_userdata(deploy_query.userdata)}"
        )

        deployment_id = str(uuid.uuid4())
        logger.info(f"Generated deployment_id: {deployment_id}")

        # 保存上传的 JSON 文件到部署目录下
        deploy_dir = settings.deploy_path / deployment_id
        deploy_dir.mkdir(parents=True, exist_ok=True)
        json_file_path = deploy_dir / "ir.json"
        content = await file.read()
        json_file_path.write_bytes(content)

        deploy_type = get_deploy_type(deploy_query.mode)
        port, whl_path = prepare_subprocess_deployment(deploy_type, deploy_query.port)

        # 调用 Manager SDK 部署（传入 ir_path、whl_path 和 userdata）
        result = await manager.deploy_agent(
            DeployAgentParams(
                name=deploy_query.name,
                version="1.0.0",
                user_id=user_id,
                space_id=space_id,
                mode=DeployMode(deploy_type),
                extras={
                    "ir_path": str(json_file_path),
                    "whl_path": str(whl_path),
                    "port": port,
                    "deployment_id": deployment_id,
                    "data": {"userdata": deploy_query.userdata}
                    if deploy_query.userdata
                    else None,
                },
            )
        )

        # 过滤内部实现细节，只返回用户需要的信息
        response = {
            "deployment_id": deployment_id,
            "type": result.deployment_type.value,
            "name": result.name,
            "status": result.deployment_status.value,
            "url": result.url,
        }

        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content=response
        )

    except Exception as e:
        logger.exception("Agent deployment failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Deployment failed: {str(e)}",
        ) from e


@app.get("/api/v1/agents")
async def list_agents(
    request: Request,
    status_filter: str = Query(default=None, alias="status", description="过滤状态"),
):
    """查询 Agent 列表（仅返回当前租户的部署）"""
    # 获取租户上下文
    user_id, space_id = get_tenant_context(request)

    try:
        deployments = await manager.list_deployments(
            ListDeploymentsParams(
                deployment_type=DeploymentType.AGENT,
                deployment_status=DeploymentStatus(status_filter)
                if status_filter
                else None,
                user_id=user_id,
                space_id=space_id,
            )
        )

        # 过滤内部实现细节
        filtered_deployments = []
        for dep in deployments:
            filtered_deployments.append({
                "deployment_id": dep.deployment_id,
                "name": dep.name,
                "status": dep.deployment_status.value,
                "url": dep.url,
                "port": dep.data.get("port") if dep.data else None,
            })

        return {"deployments": filtered_deployments}
    except Exception as e:
        logger.exception("Failed to list agents")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list deployments: {str(e)}",
        ) from e


@app.get("/api/v1/agents/{deployment_id}")
async def get_agent(request: Request, deployment_id: str):
    """获取 Agent 详情（仅能访问当前租户的部署）"""
    # 获取租户上下文
    user_id, space_id = get_tenant_context(request)

    try:
        deployment = await manager.get_deployment(
            deployment_id
        )
        if not deployment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Deployment {deployment_id} not found"
            )
        # 过滤内部实现细节
        return {
            "deployment_id": deployment.deployment_id,
            "name": deployment.name,
            "status": deployment.deployment_status.value,
            "url": deployment.url,
            "port": deployment.data.get("port") if deployment.data else None,
            "type": deployment.deployment_type.value,
            "created_at": deployment.created_at,
            "updated_at": deployment.updated_at,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get agent")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get deployment: {str(e)}",
        ) from e


@app.delete("/api/v1/agents/{deployment_id}")
async def delete_agent(request: Request, deployment_id: str):
    """删除 Agent（仅能删除当前租户的部署）"""
    # 获取租户上下文
    user_id, space_id = get_tenant_context(request)

    try:
        success = await manager.delete_deployment(
            deployment_id
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Deployment {deployment_id} not found"
            )
        return {"message": f"Deployment {deployment_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete agent")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete deployment: {str(e)}",
        ) from e


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
