# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
本地冒烟：
1) openjiuwen 的 AioBotoClient：upload_file + download_file
2) runtime_support.ir_resolver.ensure_ir_root：用刚上传的 object key 走一遍二级缓存读取逻辑（不落盘）

以下常量请与本机 applications/ir_execution_service/.env 中 OBS 段保持一致（勿将含密钥的修改提交到公共仓库）。

用法：
  cd applications/ir_execution_service
  uv run python test/obs_upload_download_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path

from openjiuwen_runtime.foundation.log import get_logger

_LOG = get_logger(__name__)

# --- 从 .env 手工同步的配置（与 LOWCODE_IR_OBS_BUCKET、OBS_* 一致）---
OBS_ACCESS_KEY_ID = ""
OBS_SECRET_ACCESS_KEY = ""
OBS_SERVER = ""
OBS_REGION: str | None = None  # .env 里为空则保持 None
LOWCODE_IR_OBS_BUCKET = ""
# -------------------------------------------------------------------


def _apply_obs_env_to_process() -> None:
    """供 ensure_ir_root 内部读取 OBS 与（可选）Redis 配置。"""
    os.environ["OBS_ACCESS_KEY_ID"] = OBS_ACCESS_KEY_ID
    os.environ["OBS_SECRET_ACCESS_KEY"] = OBS_SECRET_ACCESS_KEY
    os.environ["OBS_SERVER"] = OBS_SERVER
    if OBS_REGION:
        os.environ["OBS_REGION"] = OBS_REGION
    else:
        os.environ.pop("OBS_REGION", None)
    os.environ["LOWCODE_IR_OBS_BUCKET"] = LOWCODE_IR_OBS_BUCKET


async def _main() -> None:
    from openjiuwen.core.foundation.store.object.aioboto_storage_client import AioBotoClient

    client = AioBotoClient(
        server=OBS_SERVER,
        access_key_id=OBS_ACCESS_KEY_ID,
        secret_access_key=OBS_SECRET_ACCESS_KEY,
        region_name=OBS_REGION,
    )

    test_dir = Path(__file__).resolve().parent
    test_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    object_key = f"ir_execution_service_smoke/{token}.json"
    upload_path = test_dir / f".obs_upload_{token}.json"
    download_path = test_dir / f".obs_download_{token}.json"
    payload = (f'{{"ok": true, "token": "{token}"}}\n').encode("utf-8")
    upload_path.write_bytes(payload)

    ok_up = await client.upload_file(
        bucket_name=LOWCODE_IR_OBS_BUCKET,
        object_name=object_key,
        file_path=upload_path,
    )
    if not ok_up:
        _LOG.error("upload_file 返回 False")
        raise SystemExit(10)

    if download_path.exists():
        download_path.unlink(missing_ok=True)

    ok_down = await client.download_file(
        bucket_name=LOWCODE_IR_OBS_BUCKET,
        object_name=object_key,
        file_path=download_path,
    )
    if not ok_down:
        _LOG.error("download_file 返回 False")
        raise SystemExit(11)

    if download_path.read_bytes() != payload:
        _LOG.error("下载内容与上传不一致")
        raise SystemExit(12)

    _LOG.info("AioBotoClient upload_file + download_file OK")
    _LOG.info("- bucket: %s", LOWCODE_IR_OBS_BUCKET)
    _LOG.info("- object_key: %s", object_key)

    # --- ensure_ir_root：依赖进程环境变量 + 无参 AioBotoClient ---
    _apply_obs_env_to_process()
    # 为了测试稳定：开启内存缓存、关闭 redis
    os.environ["LOWCODE_IR_MEMORY_CACHE_ENABLED"] = "true"
    os.environ["LOWCODE_IR_MEMORY_LRU_MAX"] = "32"
    os.environ["LOWCODE_IR_MEMORY_TTL_SECONDS"] = "300"
    os.environ["LOWCODE_IR_REDIS_CACHE_ENABLED"] = "false"

    from fastapi import HTTPException

    from ..runtime_support.ir_resolver import ensure_ir_root

    try:
        ir_root = await ensure_ir_root(object_key)
    except HTTPException as e:
        _LOG.error("ensure_ir_root 失败: %s %s", e.status_code, e.detail)
        raise SystemExit(16) from e
    if not (isinstance(ir_root, dict) and ir_root.get("ok") is True and ir_root.get("token") == token):
        _LOG.error("ensure_ir_root 返回内容与预期不一致: %s", ir_root)
        raise SystemExit(15)

    _LOG.info("ensure_ir_root OK")
    _LOG.info("- keys: %s", sorted(ir_root.keys()))


if __name__ == "__main__":
    asyncio.run(_main())
