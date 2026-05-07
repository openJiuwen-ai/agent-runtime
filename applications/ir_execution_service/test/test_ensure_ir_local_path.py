# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""手动测试 runtime_support.ir_resolver.ensure_ir_root（二级缓存）。

在 applications/ir_execution_service 下执行：
  uv run python ir_execution_service/test/test_ensure_ir_local_path.py

- 通过 mock OBS 拉取函数，验证内存 LRU 命中（第二次调用不再触发 OBS 读取）。
- 顺带校验空路径、含 .. 等非法 ir_path 的 HTTPException（由 ensure_ir_root 负责）。

若需测真实 OBS 下载，请先配置 .env 中 OBS 段与 LOWCODE_IR_OBS_BUCKET，并填入存在的 object key。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from openjiuwen_runtime.foundation.log import get_logger

_LOG = get_logger(__name__)

_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent

from ..runtime_support.ir_resolver import ensure_ir_root


def _load_dotenv_optional() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = _SERVICE_ROOT / ".env"
    if env.is_file():
        load_dotenv(env)


async def _test_memory_lru_hit() -> None:
    # 强制开启内存缓存，关闭 redis（保证行为可控）
    import os

    os.environ["LOWCODE_IR_MEMORY_CACHE_ENABLED"] = "true"
    os.environ["LOWCODE_IR_MEMORY_LRU_MAX"] = "8"
    os.environ["LOWCODE_IR_MEMORY_TTL_SECONDS"] = "300"
    os.environ["LOWCODE_IR_REDIS_CACHE_ENABLED"] = "false"

    # 仅用于 dedup token 的 bucket 名（此用例不会真实访问 OBS）
    os.environ["LOWCODE_IR_OBS_BUCKET"] = "unit-test-bucket"

    calls: list[str] = []

    async def _fake_read_obs_bytes(bucket: str, object_key: str) -> bytes:
        calls.append(f"{bucket}/{object_key}")
        return b'{"ok": true, "key": "wf_demo"}'

    rel = "my_space/wf_demo.json"
    with patch(
        "ir_execution_service.runtime_support.ir_cache_fetch._read_obs_bytes",
        new=_fake_read_obs_bytes,
    ):
        r1 = await ensure_ir_root(rel)
        assert r1.get("ok") is True
        assert len(calls) == 1, calls

        # 第二次应命中内存 LRU，不再触发 _read_obs_bytes
        r2 = await ensure_ir_root(rel)
        assert r2.get("ok") is True
        assert len(calls) == 1, calls

    _LOG.info("[ok] memory lru hit: calls=%s", calls)


async def _test_validation_errors() -> None:
    import os

    os.environ["LOWCODE_IR_OBS_BUCKET"] = "b"

    for label, ir_path, expect_substr in (
        ("empty", "", "empty"),
        ("dotdot", "a/../b.json", ".."),
        ("only_slash", "/", "invalid"),
    ):
        try:
            await ensure_ir_root(ir_path)
        except HTTPException as e:
            assert e.status_code == 400, (label, e.status_code)
            detail = str(e.detail).lower()
            assert expect_substr in detail, (label, e.detail)
            _LOG.info("[ok] validation %s: %s", label, e.detail)
        else:
            raise AssertionError(f"{label}: expected HTTPException")


async def _run_download_smoke(ir_path: str) -> None:
    """可选：缓存不存在时走 OBS（需完整 .env 与网络）。"""
    _load_dotenv_optional()
    root = await ensure_ir_root(ir_path)
    _LOG.info("[ok] download: top-level keys=%s", sorted(root.keys()))


async def _main() -> None:
    await _test_memory_lru_hit()
    await _test_validation_errors()
    _LOG.info("ensure_ir_root 本地分支测试全部通过。")

    # 取消注释并填入真实 OBS 对象键可测下载链路：
    # await _run_download_smoke("your/prefix/workflow.json")


if __name__ == "__main__":
    asyncio.run(_main())
