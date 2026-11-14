"""身份/认证服务 FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

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

    from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
    from identity_center.core.federation import IdentityFederationService

    if not isinstance(db_handler, SQLAlchemyHandler):
        raise RuntimeError("identity federation requires a SQLAlchemy database handler")
    federation_service = await IdentityFederationService.create(db_handler, settings)
    application.state.federation_service = federation_service

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
    await federation_service.close()
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
        title="identity-center",
        description="JiuwenClaw 身份/认证服务（OAuth2 + JWT，独立身份库）",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        **cors_middleware_kwargs(settings.cors_allow_origins),
    )
    router_register(application)
    return application


app = create_app()
