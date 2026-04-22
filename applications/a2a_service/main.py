
from __future__ import annotations

import uvicorn
from loguru import logger

from config import get_settings


def main() -> None:
    settings = get_settings()

    logger.info(f"启动 {settings.app_name}...")
    logger.info(f"监听地址: {settings.fastapi_host}:{settings.fastapi_port}")
    logger.info(f"调试模式: {settings.fastapi_debug}")
    logger.info(f"Worker 数量: {settings.fastapi_workers}")

    uvicorn.run(
        "app:app",
        host=settings.fastapi_host,
        port=settings.fastapi_port,
        workers=settings.fastapi_workers if not settings.fastapi_debug else 1,
        reload=settings.fastapi_debug,
        log_level=settings.log_level.lower(),
        loop="auto",
    )


if __name__ == "__main__":
    main()
