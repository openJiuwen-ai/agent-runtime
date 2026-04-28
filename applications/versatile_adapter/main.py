#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

import uvicorn
from loguru import logger

from config import get_settings


def main() -> None:
    settings = get_settings()

    logger.info(f"启动 {settings.adapter_app_name}...")
    logger.info(f"监听地址: {settings.adapter_fastapi_host}:{settings.adapter_fastapi_port}")
    logger.info(f"调试模式: {settings.adapter_fastapi_debug}")
    logger.info(f"Worker 数量: {settings.adapter_fastapi_workers}")

    uvicorn.run(
        "app:app",
        host=settings.adapter_fastapi_host,
        port=settings.adapter_fastapi_port,
        workers=settings.adapter_fastapi_workers if not settings.adapter_fastapi_debug else 1,
        reload=settings.adapter_fastapi_debug,
        log_level=settings.adapter_log_level.lower(),
        loop="auto",
    )


if __name__ == "__main__":
    main()
