# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""启动前环境准备：不为进程加载 .env 文件，仅校验必填项。

外部部署请自行 export 或注入环境变量；仓库内 .env 仅作样例参考。
"""

from __future__ import annotations

import os

from .runtime_env import llm_api_key_env_var_name

_PREPARED = False


def _blank(name: str) -> bool:
    return not (os.environ.get(name) or "").strip()


def _strip(name: str, default: str = "") -> str:
    v = (os.environ.get(name) or default).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].strip()
    return v


def _collect_llm_key_missing(missing: list[str]) -> None:
    model_name = _strip("DEFAULT_LLM_MODEL_NAME")
    api_base = _strip("DEFAULT_LLM_API_BASE")
    provider = _strip("DEFAULT_LLM_MODEL_PROVIDER")
    api_key = _strip("DEFAULT_LLM_API_KEY")
    if not provider:
        missing.append("DEFAULT_LLM_MODEL_PROVIDER")
    if not model_name:
        missing.append("DEFAULT_LLM_MODEL_NAME")
    if not api_base:
        missing.append("DEFAULT_LLM_API_BASE")
    if not api_key and api_base:
        env_key = llm_api_key_env_var_name(api_base)
        if "<SLUG_FROM_BASE_URL>" not in env_key:
            api_key = (os.environ.get(env_key) or "").strip()
    if not api_key:
        missing.append(
            "DEFAULT_LLM_API_KEY"
        )


def _collect_embed_missing(missing: list[str]) -> None:
    if not _strip("EMBED_MODEL_NAME"):
        missing.append("EMBED_MODEL_NAME")
    if not _strip("EMBED_BASE_URL"):
        missing.append("EMBED_BASE_URL")
    if not _strip("EMBED_API_KEY"):
        missing.append("EMBED_API_KEY")


def _collect_db_missing(missing: list[str]) -> None:
    """与 openjiuwen_studio.ops.config.Settings 一致：DB 连接项、OPS_DB_NAME、AGENT_DB_NAME、SQLite 路径与文件名。"""
    db_type = _strip("DB_TYPE").lower()
    if db_type in {"mysql", "gaussdb", "opengauss"}:
        for key in ("DB_HOST", "DB_PORT", "DB_USER", "OPS_DB_NAME", "AGENT_DB_NAME"):
            if not _strip(key):
                missing.append(key)
        if "DB_PASSWORD" not in os.environ:
            missing.append("DB_PASSWORD")
    elif db_type == "sqlite":
        for key in ("SQLITE_DB_PATH", "OPS_SQLITE_DB", "AGENT_SQLITE_DB"):
            if not _strip(key):
                missing.append(key)
    else:
        missing.append(f"DB_TYPE 非法: {db_type!r}，应为 mysql、sqlite、gaussdb 或 opengauss")


def _collect_kv_missing(missing: list[str]) -> None:
    kv_type = _strip("KV_STORE_TYPE").lower()
    if kv_type == "redis":
        # 记忆引擎 KV：只允许使用 MEMORY_REDIS_URL（不允许回退/替补）。
        if not _strip("MEMORY_REDIS_URL"):
            missing.append("KV_STORE_TYPE=redis 时需要 MEMORY_REDIS_URL（记忆引擎专用）")
    elif kv_type == "inmemory":
        return
    elif kv_type in ("db", "sql", "sqlite", "mysql"):
        return
    else:
        missing.append(f"KV_STORE_TYPE 非法: {kv_type!r}")


def _collect_vector_missing(missing: list[str]) -> None:
    index_type = _strip("INDEX_MANAGER_TYPE").lower()
    if index_type == "milvus":
        if not _strip("MILVUS_HOST"):
            missing.append("INDEX_MANAGER_TYPE=milvus 时需要 MILVUS_HOST")
        if not _strip("MILVUS_PORT"):
            missing.append("INDEX_MANAGER_TYPE=milvus 时需要 MILVUS_PORT")
    elif index_type == "chroma":
        if not _strip("MEMORY_DATA_PATH"):
            missing.append("INDEX_MANAGER_TYPE=chroma 时需要 MEMORY_DATA_PATH")
    else:
        missing.append(f"INDEX_MANAGER_TYPE 非法: {index_type!r}，应为 milvus 或 chroma")


def _collect_obs_missing(missing: list[str]) -> None:
    for key in (
        "OBS_ACCESS_KEY_ID",
        "OBS_SECRET_ACCESS_KEY",
        "OBS_SERVER",
        "OBS_REGION",
        "LOWCODE_IR_OBS_BUCKET",
    ):
        if _blank(key):
            missing.append(key)


def validate_runtime_environment() -> None:
    """根据当前进程环境变量校验；缺项收集后一次性抛出 RuntimeError。"""
    missing: list[str] = []
    if _blank("LOWCODE_IR_EXECUTION_SERVICE_VERSION"):
        missing.append("LOWCODE_IR_EXECUTION_SERVICE_VERSION（与 pyproject 发布版本对齐，由部署注入）")
    if _blank("CODE_SANDBOX_URL"):
        missing.append("CODE_SANDBOX_URL")

    # 默认 LLM 配置：无论是否开启记忆引擎，都要求具备（服务编译/执行 workflow/agent 也需要）
    _collect_llm_key_missing(missing)

    # 记忆引擎相关依赖：仅当开关开启时才校验。
    # 默认关闭（未设置 IR_ENABLE_AGENT_MEMORY 时按 false 处理）。
    mem_enabled = _strip("IR_ENABLE_AGENT_MEMORY", "false").lower() not in {"0", "false", "no", "off"}
    if mem_enabled:
        _collect_embed_missing(missing)
        _collect_db_missing(missing)
        _collect_kv_missing(missing)
        _collect_vector_missing(missing)

    # 默认/基础 Redis：IR 二级缓存 + 对话上下文都依赖；通过前缀隔离避免冲突。
    if not _strip("LOWCODE_DEFAULT_REDIS_URL"):
        missing.append("LOWCODE_DEFAULT_REDIS_URL")

    _collect_obs_missing(missing)

    # Checkpointer：只允许使用 CHECKPOINTER_REDIS_URL（不允许回退/替补）。
    checkpointer_disabled = _strip("CHECKPOINTER_DISABLED").lower() in {"1", "true", "yes", "on"}
    if not checkpointer_disabled and not _strip("CHECKPOINTER_REDIS_URL"):
        missing.append("未设置 CHECKPOINTER_DISABLED 时，需要 CHECKPOINTER_REDIS_URL（检查点专用）")

    if missing:
        detail = "\n  - ".join([""] + missing)
        raise RuntimeError(
            "运行所需环境变量未就绪，请在进程环境中设置（勿依赖应用内 .env 自动加载）："
            f"{detail}"
        )


def prepare_runtime_environment() -> None:
    """幂等：仅校验必填项。应在导入依赖配置的模块之前调用。"""
    global _PREPARED
    if _PREPARED:
        return
    validate_runtime_environment()
    _PREPARED = True


def reset_runtime_environment_prepare_for_tests() -> None:
    """仅测试用：允许重复执行 prepare。"""
    global _PREPARED
    _PREPARED = False
