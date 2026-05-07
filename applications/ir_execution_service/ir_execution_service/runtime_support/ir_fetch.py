# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""IR 拉取与解析：对象键 → 本地缓存路径（按需从 OBS 下载）、workflow/agent 判定、HTTP 异常映射。

供 `invoke_api` / `stream_api` 共用，不依赖具体 HTTP 处理形态（JSON vs SSE）。

并发：同一缓存目标文件使用独立锁文件（`{完整路径}.lock`），粒度为「该 IR 缓存文件」全路径，
与目录无关；多进程（Gunicorn）与本机多协程均互斥下载与落盘。下载先写 `.part` 再 `os.replace` 原子替换。

缓存上限：LOWCODE_IR_CACHE_MAX_FILES（默认 100，0 表示不限制）；超过时按 mtime（近似下载时间）
删除最旧的一半文件。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from filelock import FileLock

from .http_response_contract import LowcodeApiResponseCode
from .studio_secrets import resolve_secret_env


def _cache_max_files() -> int:
    raw = (os.environ.get("LOWCODE_IR_CACHE_MAX_FILES") or "").strip()
    if not raw:
        return 100
    try:
        return max(0, int(raw))
    except ValueError:
        return 100


def _fetch_lock_timeout_s() -> float:
    raw = (os.environ.get("LOWCODE_IR_FETCH_LOCK_TIMEOUT_S") or "").strip()
    if not raw:
        return 300.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 300.0


def _evict_cache_if_needed(cache_root: Path, max_files: int) -> None:
    """缓存目录下普通文件数超过 max_files 时，按 mtime 删除最旧的一半（不含 .lock / .part）。"""
    if max_files <= 0:
        return
    files: list[tuple[float, Path]] = []
    for p in cache_root.rglob("*"):
        if not p.is_file():
            continue
        name = p.name
        if name.endswith(".lock") or name.endswith(".part"):
            continue
        try:
            files.append((p.stat().st_mtime, p))
        except OSError:
            continue
    n = len(files)
    if n <= max_files:
        return
    files.sort(key=lambda x: x[0])
    n_remove = n // 2
    for _, path in files[:n_remove]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def detect_executable_kind(ir_root: dict[str, Any]) -> str:
    """根据 IR JSON 区分 workflow 与 agent。"""
    from ..dsl_workflow_dependency_loader import looks_like_dsl_workflow_export, unwrap_workflow_document

    if not isinstance(ir_root, dict):
        raise HTTPException(status_code=400, detail="IR root must be a JSON object")
    if looks_like_dsl_workflow_export(ir_root):
        return "workflow"
    inner = unwrap_workflow_document(ir_root)
    if isinstance(inner.get("nodes"), list) and isinstance(inner.get("edges"), list):
        return "workflow"
    if isinstance(ir_root.get("agent"), dict):
        return "agent"
    raise HTTPException(
        status_code=400,
        detail="IR is neither workflow (components+connections or nodes+edges) nor agent (agent object)",
    )


def lowcode_code_from_http_exception(exc: HTTPException) -> tuple[LowcodeApiResponseCode, str]:
    """将 IR 下载/路径类 HTTPException 映射到 LowcodeApiResponseCode。"""
    detail = str(exc.detail) if exc.detail is not None else ""
    lowered = detail.lower()
    if exc.status_code == 502:
        return LowcodeApiResponseCode.IR_DOWNLOAD_FAILED, detail
    if exc.status_code == 400:
        if "ir_path" in lowered or "object key" in lowered or "download directory" in lowered:
            return LowcodeApiResponseCode.INVALID_IR_PATH, detail
        return LowcodeApiResponseCode.INVALID_PARAM, detail
    if exc.status_code == 500:
        obs_hints = ("obs", "bucket", "lowcode_ir", "configured")
        if any(h in lowered for h in obs_hints):
            return LowcodeApiResponseCode.DEPENDENCY_ERROR, detail
        return LowcodeApiResponseCode.INTERNAL_ERROR, detail
    return LowcodeApiResponseCode.INTERNAL_ERROR, detail


async def ensure_ir_local_path(ir_path: str) -> Path:
    """将请求里的 ir_path（OBS 对象键）解析为本机可读 JSON 路径，必要时从 OBS 下载。"""
    ir_path_stripped = (ir_path or "").strip()
    if not ir_path_stripped:
        raise HTTPException(status_code=400, detail="ir_path is empty")

    obs_bucket_name = (os.environ.get("LOWCODE_IR_OBS_BUCKET") or "").strip()
    if not obs_bucket_name:
        raise HTTPException(status_code=500, detail="LOWCODE_IR_OBS_BUCKET is not configured")

    download_dir_config = (os.environ.get("LOWCODE_IR_DOWNLOAD_DIR") or "").strip()
    if not download_dir_config:
        raise HTTPException(status_code=500, detail="LOWCODE_IR_DOWNLOAD_DIR is not configured")

    cache_root_dir = Path(download_dir_config).expanduser().resolve()
    cache_root_dir.mkdir(parents=True, exist_ok=True)

    object_key = ir_path_stripped.replace("\\", "/").lstrip("/")
    key_segments = [segment for segment in object_key.split("/") if segment and segment != "."]
    if any(segment == ".." for segment in key_segments):
        raise HTTPException(status_code=400, detail="ir_path must not contain '..'")
    if not key_segments:
        raise HTTPException(status_code=400, detail="invalid ir_path as object key")

    cached_json_path = cache_root_dir.joinpath(*key_segments).resolve()
    if not str(cached_json_path).startswith(str(cache_root_dir)):
        raise HTTPException(status_code=400, detail="invalid ir_path after normalization")

    if cached_json_path.exists():
        return cached_json_path

    from openjiuwen.core.foundation.store.object.aioboto_storage_client import AioBotoClient

    lock_timeout = _fetch_lock_timeout_s()
    lock_file_path = str(cached_json_path) + ".lock"
    lock = FileLock(lock_file_path, timeout=lock_timeout)
    acquired = False
    try:
        await asyncio.to_thread(lock.acquire)
        acquired = True
        if cached_json_path.exists():
            return cached_json_path

        cached_json_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = cached_json_path.with_name(cached_json_path.name + ".part")
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass

        storage = AioBotoClient(
            server=(os.environ.get("OBS_SERVER") or "").strip() or None,
            access_key_id=resolve_secret_env("OBS_ACCESS_KEY_ID", ""),
            secret_access_key=resolve_secret_env("OBS_SECRET_ACCESS_KEY", ""),
            region_name=(os.environ.get("OBS_REGION") or "").strip() or None,
        )
        ok = await storage.download_file(
            bucket_name=obs_bucket_name,
            object_name=object_key,
            file_path=part_path,
        )
        if not ok:
            try:
                part_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise HTTPException(status_code=502, detail=f"failed to download ir from obs: {object_key}")

        await asyncio.to_thread(os.replace, str(part_path), str(cached_json_path))
    finally:
        if acquired:
            await asyncio.to_thread(lock.release, force=False)

    max_files = _cache_max_files()
    if max_files > 0:
        await asyncio.to_thread(_evict_cache_if_needed, cache_root_dir, max_files)

    return cached_json_path
