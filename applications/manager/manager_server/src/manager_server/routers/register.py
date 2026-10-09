from __future__ import annotations

import os

from fastapi import APIRouter, Depends, FastAPI

from .application_config_routers import application_config_router
from .approval_routers import approval_router
from .auth_guards import get_current_user, require_admin
from .authz_routers import authz_router
from .import_export_routers import import_export_router
from .instance_access_routers import gateway_lookup_router, instance_grant_router
from .instance_resource_routers import instance_resource_router
from .instance_routers import instance_router
from .quota_routers import quota_router
from .template_routers import templates_router
from .user_console_routers import public_user_console_router, user_console_router

INSTANCES_PREFIX = "/instances"


def _build_api_router() -> APIRouter:
    # Public exceptions must be mounted separately: a child route cannot remove
    # its parent's dependencies. Business APIs require login by default.
    root_router = APIRouter()
    v1_router = APIRouter(prefix="/v1", dependencies=[Depends(get_current_user)])
    # Permission-based APIs are siblings of the legacy admin subtree, otherwise
    # they would accidentally require BOTH a permission and an admin role.
    v1_router.include_router(authz_router, tags=["Authorization"])
    v1_router.include_router(approval_router, tags=["Approvals"])
    # 用户控制台：当前用户可见 Agent + 用户面选路。
    v1_router.include_router(user_console_router, prefix="/user-console", tags=["User Console"])
    admin_router = APIRouter(dependencies=[Depends(require_admin)])
    admin_router.include_router(templates_router, tags=["Templates"])
    admin_router.include_router(import_export_router, tags=["Import Export"])
    admin_router.include_router(instance_resource_router, tags=["Instance Resource"])
    admin_router.include_router(
        instance_router,
        prefix=INSTANCES_PREFIX,
        tags=["Instances"],
    )
    admin_router.include_router(
        application_config_router,
        prefix=INSTANCES_PREFIX,
        tags=["Application Config"],
    )
    v1_router.include_router(
        quota_router,
        prefix=INSTANCES_PREFIX,
        tags=["Quota"],
    )

    # 实例准入：用户/组织 ↔ 实例授权（instance_grant）。
    admin_router.include_router(
        instance_grant_router,
        prefix=INSTANCES_PREFIX,
        tags=["Instance Access"],
    )
    # 反查：一批实体各授权了哪些实例。
    admin_router.include_router(gateway_lookup_router, tags=["Instance Access"])
    v1_router.include_router(admin_router)
    root_router.include_router(
        public_user_console_router, prefix="/v1/user-console", tags=["User Console"]
    )

    @root_router.get("/health", tags=["System"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @root_router.get("/manager-ws/status", tags=["System"])
    async def manager_ws_status() -> dict:
        """兼容旧前端：配置下发已改为 HTTP，WS 服务已移除。"""
        return {
            "enabled": False,
            "running": False,
            "registered_jiuwenclaw_ids": [],
            "pid": os.getpid(),
            "transport": "http",
        }

    root_router.include_router(v1_router)
    return root_router


# Build once, then include without mutating it. Keep the existing module export
# while allowing multiple application factories without duplicate routes.
api_router = _build_api_router()


def router_register(app: FastAPI) -> None:
    app.include_router(api_router, prefix="/api")

    @app.get("/", tags=["System"])
    async def root() -> dict[str, str]:
        return {
            "message": "JiuwenClaw Manager API",
            "docs": "/docs",
            "health": "/api/health",
        }
