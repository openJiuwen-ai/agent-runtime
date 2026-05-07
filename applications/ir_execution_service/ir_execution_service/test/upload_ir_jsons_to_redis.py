# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from openjiuwen_runtime.foundation.log import get_logger

_LOG = get_logger(__name__)

UPLOAD_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _clean_env_value(name: str, default: str = "") -> str:
    v = (os.environ.get(name) or default).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].strip()
    return v


def _normalize_object_key(ir_path: str) -> str:
    ir_path_stripped = (ir_path or "").strip()
    if not ir_path_stripped:
        raise ValueError("ir_path is empty")
    object_key = ir_path_stripped.replace("\\", "/").lstrip("/")
    key_segments = [segment for segment in object_key.split("/") if segment and segment != "."]
    if any(segment == ".." for segment in key_segments):
        raise ValueError("ir_path must not contain '..'")
    if not key_segments:
        raise ValueError("invalid ir_path as object key")
    return object_key


def _dedup_token(bucket: str, object_key: str) -> str:
    return hashlib.sha256(f"{bucket}\n{object_key}".encode("utf-8")).hexdigest()


def _ir_redis_key_root() -> str:
    raw = _clean_env_value("LOWCODE_IR_REDIS_KEY_PREFIX", "ir_exec").strip()
    if not raw:
        raw = "ir_exec"
    return raw.strip(":").strip()


def _data_key(dedup: str) -> str:
    return f"{_ir_redis_key_root()}:data:{dedup}"


def _redis_ttl_seconds() -> int:
    raw = (os.environ.get("LOWCODE_IR_REDIS_TTL_SECONDS") or "86400").strip()
    try:
        return max(60, int(raw))
    except ValueError:
        return 86400


def _iter_json_files(root: Path) -> list[Path]:
    return sorted([p for p in root.glob("*.json") if p.is_file()])


def _load_raw_json(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("IR root must be a JSON object")
    return raw


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError as e:
        raise ImportError("缺少 python-dotenv，请在本目录执行: uv sync") from e

    for env_path in (_PROJECT_ROOT / ".env", UPLOAD_DIR / ".env"):
        if env_path.is_file():
            load_dotenv(env_path)
            break


def main() -> int:
    _load_env()

    root = UPLOAD_DIR
    if not root.is_dir():
        _LOG.error("directory not found: %s", root)
        return 2

    redis_url = (os.environ.get("LOWCODE_DEFAULT_REDIS_URL") or "").strip()
    if not redis_url:
        _LOG.error("missing env LOWCODE_DEFAULT_REDIS_URL")
        return 2

    bucket = (os.environ.get("LOWCODE_IR_OBS_BUCKET") or "").strip()
    if not bucket:
        _LOG.error("missing env LOWCODE_IR_OBS_BUCKET")
        return 2

    try:
        import redis  # type: ignore
    except Exception as exc:
        _LOG.error("missing dependency 'redis': %s", exc)
        return 2

    files = _iter_json_files(root)
    if not files:
        _LOG.error("no json files found under: %s", root)
        return 2

    ttl = _redis_ttl_seconds()

    r = redis.from_url(redis_url, decode_responses=True)
    ok = 0
    for p in files:
        # 本脚本约定：ir_path == 文件名（与调用 ensure_ir_root(ir_path) 的入参一致）
        ir_path = p.name
        try:
            object_key = _normalize_object_key(ir_path)
            dedup = _dedup_token(bucket, object_key)
            key = _data_key(dedup)
            raw_json = _load_raw_json(p)
        except Exception as exc:
            _LOG.warning("skip %s: %s", p.name, exc)
            continue

        r.set(key, raw_json, ex=int(ttl))
        ok += 1
        _LOG.info("uploaded: file=%s ir_path=%s redis_key=%s", p.name, object_key, key)

    if ok <= 0:
        return 1
    _LOG.info("done: %s/%s uploaded", ok, len(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

