"""FastAPI 应用工厂。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from manager_server import __version__
from manager_server.core.deployment.service import (
    run_deployment_executor_loop,
    sync_defaults_from_snapshot,
)
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import create_db_handler, database_config_summary
from manager_server.infrastructure.logger import configure_logging, get_logger
from manager_server.models.table_init import init_all_tables
from manager_server.routers.register import router_register
from manager_server.schedulers.approval_expirer import run_approval_expirer_loop
from manager_server.schedulers.heartbeat_scanner import run_heartbeat_scan_loop
from manager_server.security.keys import get_or_create_manager_signing_key
from manager_server.security.sign_provider import set_manager_signing_key
from openjiuwen_runtime.service.bootstrap import build_redis_client

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging()
    db_handler = create_db_handler()
    await db_handler.init_database()
    await db_handler.connect()
    await init_all_tables(db_handler)
    # 身份/账号种子已移至独立认证服务(jiuwenclaw_identity);管理库不再播种用户/组织。
    # 加载/生成 Manager 签名密钥对（Ed25519），供握手下发公钥与下发加签使用。
    set_manager_signing_key(await get_or_create_manager_signing_key(db_handler))

    # Redis：心跳巡检选主的分布式锁
    redis_client = build_redis_client(settings)

    # manager up 注入的 .env.custom 快照 → 刷新部署默认参数（开箱即用；未注入则跳过）
    await sync_defaults_from_snapshot(db_handler)

    stop = asyncio.Event()
    scan_task = asyncio.create_task(run_heartbeat_scan_loop(stop, db_handler, redis_client))
    expire_task = asyncio.create_task(
        run_approval_expirer_loop(stop, db_handler, redis_client)
    )
    # 管理面启动部署：后台认领 pending 部署任务（子进程代跑 deploy.sh）
    deploy_task = asyncio.create_task(
        run_deployment_executor_loop(stop, db_handler, redis_client)
    )
    _log.info(
        "startup",
        version=__version__,
        db=database_config_summary(),
    )
    yield
    stop.set()
    scan_task.cancel()
    expire_task.cancel()
    deploy_task.cancel()
    for task in (scan_task, expire_task, deploy_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await db_handler.disconnect()
    _log.info("shutdown")


def cors_middleware_kwargs(raw_origins: str) -> dict[str, Any]:
    """来源白名单 → CORSMiddleware 参数。

    通配源与携带凭证互斥：CORS 规范不允许 ``Access-Control-Allow-Origin: *`` 与
    ``Access-Control-Allow-Credentials`` 同现，否则 Starlette 会把任意请求的
    Origin 原样回显并附凭证头。空配置 = 只允许同源（受支持拓扑经 manager-web
    单源反代，浏览器不跨源），因此也无需携带凭证。
    """
    origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    allow_credentials = bool(origins)
    if "*" in origins:
        allow_credentials = False
        _log.warning("cors_wildcard_disables_credentials", origins=origins)
    return {
        "allow_origins": origins,
        "allow_credentials": allow_credentials,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }


def create_app() -> FastAPI:
    application = FastAPI(
        title="manager-server",
        description="JiuwenClaw EE 管理平面（Claw Manager）",
        version=__version__,
        lifespan=lifespan,
        # 关闭尾斜杠自动 307：经 manager-web / Ingress 反代时 Location 会泄漏集群内网 DNS
        redirect_slashes=False,
    )
    application.add_middleware(
        CORSMiddleware,
        **cors_middleware_kwargs(settings.cors_allow_origins),
    )
    router_register(application)
    return application


app = create_app()
