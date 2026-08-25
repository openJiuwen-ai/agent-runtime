"""FastAPI 应用工厂。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from manager_server import __version__
from manager_server.infrastructure.db import create_db_handler, database_config_summary
from manager_server.infrastructure.logger import configure_logging, get_logger
from manager_server.models.table_init import init_all_tables
from manager_server.routers.register import router_register
from manager_server.schedulers.heartbeat_scanner import run_heartbeat_scan_loop

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging()
    db_handler = create_db_handler()
    application.state.db_handler = db_handler
    await db_handler.init_database()
    await db_handler.connect()
    await init_all_tables(db_handler)
    # 身份/账号种子已移至独立认证服务(jiuwenclaw_identity);管理库不再播种用户/组织。
    # 加载/生成 Manager 签名密钥对（Ed25519），供握手下发公钥与下发加签使用。
    from manager_server.security.keys import get_or_create_manager_signing_key
    from manager_server.security.sign_provider import set_manager_signing_key

    set_manager_signing_key(await get_or_create_manager_signing_key(db_handler))
    stop = asyncio.Event()
    scan_task = asyncio.create_task(run_heartbeat_scan_loop(stop, db_handler))
    _log.info(
        "startup",
        version=__version__,
        db=database_config_summary(),
    )
    yield
    stop.set()
    scan_task.cancel()
    try:
        await scan_task
    except asyncio.CancelledError:
        pass
    await db_handler.disconnect()
    _log.info("shutdown")


def create_app() -> FastAPI:
    application = FastAPI(
        title="manager-server",
        description="JiuwenClaw EE 管理平面（Claw Manager）",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    router_register(application)
    return application


app = create_app()
