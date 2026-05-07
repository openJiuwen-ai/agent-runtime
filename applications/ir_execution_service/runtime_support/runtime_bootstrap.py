# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""一次性启动：校验环境变量、初始化 LongTermMemory、可选 Redis checkpointer。"""

from __future__ import annotations

import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig, CheckpointerFactory

from .memory_engine_start import MemoryEngineManager
from .runtime_env import clean_env_value, get_bool_env, get_int_env
from .runtime_env_prepare import prepare_runtime_environment

_runtime_ready = False
_runtime_lock = asyncio.Lock()
_checkpointer_initialized = False


async def init_redis_checkpointer() -> None:
    global _checkpointer_initialized
    if _checkpointer_initialized:
        return
    if get_bool_env("CHECKPOINTER_DISABLED", False):
        logger.info("Checkpointer: CHECKPOINTER_DISABLED set, using SDK default (in-memory).")
        _checkpointer_initialized = True
        return

    import openjiuwen.extensions.checkpointer.redis.checkpointer  # noqa: F401

    explicit = clean_env_value("CHECKPOINTER_REDIS_URL")
    url = explicit or MemoryEngineManager.build_redis_url()
    conf: dict = {"connection": {"url": url}}

    ttl_min = get_int_env("CHECKPOINTER_DEFAULT_TTL_MINUTES", 0)
    if ttl_min > 0:
        conf["ttl"] = {
            "default_ttl": float(ttl_min),
            "refresh_on_read": get_bool_env("CHECKPOINTER_REFRESH_TTL_ON_READ", False),
        }

    cp = await CheckpointerFactory.create(CheckpointerConfig(type="redis", conf=conf))
    CheckpointerFactory.set_default_checkpointer(cp)
    safe = url.split("@")[-1] if "@" in url else url
    logger.info("Checkpointer: Redis default set (url tail %s).", safe)
    _checkpointer_initialized = True


async def ensure_runtime_ready() -> None:
    global _runtime_ready
    if _runtime_ready:
        return

    async with _runtime_lock:
        if _runtime_ready:
            return
        prepare_runtime_environment()
        await MemoryEngineManager.init()
        await init_redis_checkpointer()
        _runtime_ready = True
