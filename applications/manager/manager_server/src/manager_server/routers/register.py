from __future__ import annotations

import os

from fastapi import APIRouter, FastAPI

from .application_config_routers import application_config_router
from .user_console_routers import user_console_router
from .instance_access_routers import gateway_lookup_router, instance_grant_router
from .instance_resource_routers import instance_resource_router
from .instance_routers import instance_router
from .template_routers import templates_router

api_router = APIRouter()

INSTANCES_PREFIX = "/instances"


def router_register(app: FastAPI) -> None:
    v1_router = APIRouter(prefix="/v1")
    # 用户控制台：当前用户可见 Agent。
    v1_router.include_router(user_console_router, prefix="/user-console", tags=["User Console"])
    v1_router.include_router(templates_router, tags=["Templates"])
    v1_router.include_router(instance_resource_router, tags=["Instance Resource"])
    v1_router.include_router(
        instance_router,
        prefix=INSTANCES_PREFIX,
        tags=["Instances"],
    )
    v1_router.include_router(
        application_config_router,
        prefix=INSTANCES_PREFIX,
        tags=["Application Config"],
    )

    # 实例准入：用户/组织 ↔ 实例授权（instance_grant）。
    v1_router.include_router(
        instance_grant_router,
        prefix=INSTANCES_PREFIX,
        tags=["Instance Access"],
    )
    # 反查：一批实体各授权了哪些实例。
    v1_router.include_router(gateway_lookup_router, tags=["Instance Access"])

    @api_router.get("/health", tags=["System"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @api_router.get("/manager-ws/status", tags=["System"])
    async def manager_ws_status() -> dict:
        """兼容旧前端：配置下发已改为 HTTP，WS 服务已移除。"""
        return {
            "enabled": False,
            "running": False,
            "registered_jiuwenclaw_ids": [],
            "pid": os.getpid(),
            "transport": "http",
        }

    api_router.include_router(v1_router)
    app.include_router(api_router, prefix="/api")

    @app.get("/", tags=["System"])
    async def root() -> dict[str, str]:
        return {
            "message": "JiuwenClaw Manager API",
            "docs": "/docs",
            "health": "/api/health",
        }
