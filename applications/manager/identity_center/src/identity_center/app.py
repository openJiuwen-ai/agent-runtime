"""身份/认证服务 FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from identity_center import __version__
from identity_center.infrastructure.config import settings
from identity_center.infrastructure.db import create_db_handler, database_config_summary
from identity_center.infrastructure.logger import configure_logging, get_logger
from identity_center.models.table_init import init_all_tables
from identity_center.routers.register import router_register

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging()
    db_handler = create_db_handler()
    application.state.db_handler = db_handler
    await db_handler.init_database()
    await db_handler.connect()
    await init_all_tables(db_handler)

    from identity_center.core.auth import seed_defaults

    await seed_defaults(db_handler)

    # JWT 签名密钥:落库自举(缺失则生成 RSA-2048 落身份库)+ 填充进程缓存。
    # 多副本天然读同一行 → k8s 一致,无需 Secret 同步。
    from identity_center.security.jwt_keys import load_signing_key

    await load_signing_key(db_handler)

    _log.info(
        "startup",
        version=__version__,
        db=database_config_summary(),
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        access_ttl=settings.access_ttl_seconds,
    )
    yield
    await db_handler.disconnect()
    _log.info("shutdown")


def create_app() -> FastAPI:
    application = FastAPI(
        title="identity-center",
        description="JiuwenClaw 身份/认证服务（OAuth2 + JWT，独立身份库）",
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
