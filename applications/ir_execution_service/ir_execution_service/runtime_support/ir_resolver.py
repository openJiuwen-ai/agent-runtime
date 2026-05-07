# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

"""IR 解析门面：对象键 → IR 根对象（dict）、workflow/agent 判定、HTTP 异常映射。

该模块刻意保持“薄”：IR 内容缓存/OBS 拉取实现细节在 `ir_cache_fetch.py`。
"""

from typing import Any

from fastapi import HTTPException

from .http_response_contract import LowcodeApiResponseCode


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
        if "ir_path" in lowered or "object key" in lowered:
            return LowcodeApiResponseCode.INVALID_IR_PATH, detail
        return LowcodeApiResponseCode.INVALID_PARAM, detail
    if exc.status_code == 500:
        obs_hints = ("obs", "bucket", "lowcode_ir", "configured")
        if any(h in lowered for h in obs_hints):
            return LowcodeApiResponseCode.DEPENDENCY_ERROR, detail
        return LowcodeApiResponseCode.INTERNAL_ERROR, detail
    return LowcodeApiResponseCode.INTERNAL_ERROR, detail


async def ensure_ir_root(ir_path: str) -> dict[str, Any]:
    """将请求里的 ir_path（OBS 对象键）解析为 IR 根对象（dict），使用二级缓存（内存/Redis）避免反复下载。"""
    from .ir_cache_fetch import ensure_ir_root as _ensure_root

    return await _ensure_root(ir_path)

