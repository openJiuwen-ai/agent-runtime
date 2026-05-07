#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

# -*- coding: utf-8 -*-
"""手动测试 runtime_support.ir_fetch.ensure_ir_local_path。

在 applications/ir_execution_service 下执行：
  uv run python test/test_ensure_ir_local_path.py

- 使用临时目录作为 LOWCODE_IR_DOWNLOAD_DIR，预先写入缓存文件，验证「已存在则直接返回路径」分支（不访问 OBS）。
- 顺带校验空路径、含 .. 等非法 ir_path 的 HTTPException。

若需测真实 OBS 下载，请先配置 .env 中 LOWCODE_IR_* 与凭证，并删除对应缓存文件后，将脚本末尾的 _run_download_smoke 打开或自行传入存在的 object key。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

from openjiuwen_runtime.foundation.log import get_logger

_LOG = get_logger(__name__)

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.append(str(_SERVICE_ROOT))

from runtime_support.ir_fetch import ensure_ir_local_path


def _load_dotenv_optional() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = _SERVICE_ROOT / ".env"
    if env.is_file():
        load_dotenv(env)


async def _test_cache_hit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        os.environ["LOWCODE_IR_OBS_BUCKET"] = "not-used-if-cache-hit"
        os.environ["LOWCODE_IR_DOWNLOAD_DIR"] = str(tmp_path)

        rel = "my_space/wf_demo.json"
        cached = tmp_path / "my_space" / "wf_demo.json"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text('{"ok": true}', encoding="utf-8")

        got = await ensure_ir_local_path(rel)
        assert got.resolve() == cached.resolve(), (got, cached)
        assert got.read_text(encoding="utf-8") == '{"ok": true}'

        got2 = await ensure_ir_local_path("/my_space/wf_demo.json")
        assert got2.resolve() == cached.resolve()

        _LOG.info("[ok] cache hit: %s", got)


async def _test_validation_errors() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LOWCODE_IR_OBS_BUCKET"] = "b"
        os.environ["LOWCODE_IR_DOWNLOAD_DIR"] = str(Path(tmp))

        for label, ir_path, expect_substr in (
            ("empty", "", "empty"),
            ("dotdot", "a/../b.json", ".."),
            ("only_slash", "/", "invalid"),
        ):
            try:
                await ensure_ir_local_path(ir_path)
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
    p = await ensure_ir_local_path(ir_path)
    _LOG.info("[ok] download or cache: %s exists=%s", p, p.is_file())


async def _main() -> None:
    await _test_cache_hit()
    await _test_validation_errors()
    _LOG.info("ensure_ir_local_path 本地分支测试全部通过。")

    # 取消注释并填入真实 OBS 对象键可测下载链路：
    # await _run_download_smoke("your/prefix/workflow.json")


if __name__ == "__main__":
    asyncio.run(_main())
