# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""启动前环境准备：不为进程加载 .env 文件，仅根据类型键写入默认并校验必填项。

外部部署请自行 export 或注入环境变量；仓库内 .env 仅作样例参考。
"""

from __future__ import annotations

import os

from .runtime_env import llm_api_key_env_var_name

_PREPARED = False

# 与样例 .env 对齐：三类 type 未设置时写入进程环境，便于后续分支判断。
_TYPE_DEFAULTS: dict[str, str] = {
    "DB_TYPE": "mysql",
    "KV_STORE_TYPE": "redis",
    "INDEX_MANAGER_TYPE": "milvus",
}

_DEFAULT_MEMORY_SCOPE_ID = "ir_agent_runner_memory"


def _blank(name: str) -> bool:
    return not (os.environ.get(name) or "").strip()


def _strip(name: str, default: str = "") -> str:
    v = (os.environ.get(name) or default).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].strip()
    return v


def _setdefault_env(key: str, value: str) -> None:
    if _blank(key):
        os.environ[key] = value


def apply_runtime_type_and_optional_defaults() -> None:
    """对未设置的 type 及少量可选键写入默认值（与样例 .env 一致）。"""
    for key, value in _TYPE_DEFAULTS.items():
        _setdefault_env(key, value)

    if _blank("MEMORY_SCOPE_ID"):
        os.environ["MEMORY_SCOPE_ID"] = _DEFAULT_MEMORY_SCOPE_ID

    _setdefault_env("DEFAULT_LLM_MODEL_PROVIDER", "OpenAI")

    db_type = _strip("DB_TYPE", "mysql").lower()
    if db_type == "mysql":
        _setdefault_env("DB_PORT", "3306")
    elif db_type == "sqlite":
        _setdefault_env("SQLITE_DB_PATH", "data/databases")
        _setdefault_env("OPS_SQLITE_DB", "ops.db")
        _setdefault_env("AGENT_SQLITE_DB", "agent.db")

    kv_type = _strip("KV_STORE_TYPE", "redis").lower()
    if kv_type == "redis":
        _setdefault_env("REDIS_HOST", "localhost")
        if _blank("REDIS_PORT"):
            os.environ["REDIS_PORT"] = "6379"

    index_type = _strip("INDEX_MANAGER_TYPE", "milvus").lower()
    if index_type == "milvus":
        _setdefault_env("MILVUS_HOST", "localhost")
        if _blank("MILVUS_PORT"):
            os.environ["MILVUS_PORT"] = "19530"
    elif index_type == "chroma":
        _setdefault_env("MEMORY_DATA_PATH", "memory-data")


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


def _collect_code_sandbox_missing(missing: list[str]) -> None:
    if _blank("CODE_SANDBOX_URL"):
        missing.append("CODE_SANDBOX_URL")


def _collect_db_missing(missing: list[str]) -> None:
    """与 openjiuwen_studio.ops.config.Settings 一致：DB 连接项、OPS_DB_NAME、AGENT_DB_NAME、SQLite 路径与文件名。"""
    db_type = _strip("DB_TYPE").lower()
    if db_type == "mysql":
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
        missing.append(f"DB_TYPE 非法: {db_type!r}，应为 mysql 或 sqlite")


def _collect_kv_missing(missing: list[str]) -> None:
    kv_type = _strip("KV_STORE_TYPE").lower()
    if kv_type == "redis":
        if _blank("REDIS_URL") and _blank("REDIS_HOST"):
            missing.append("KV_STORE_TYPE=redis 时需要 REDIS_URL 或 REDIS_HOST")
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
        "LOWCODE_IR_DOWNLOAD_DIR",
    ):
        if _blank(key):
            missing.append(key)


def _collect_checkpointer_missing(missing: list[str]) -> None:
    disabled = _strip("CHECKPOINTER_DISABLED").lower() in {"1", "true", "yes", "on"}
    if disabled:
        return
    if _strip("CHECKPOINTER_REDIS_URL"):
        return
    if _blank("REDIS_HOST") and _blank("REDIS_URL"):
        missing.append(
            "未设置 CHECKPOINTER_DISABLED 且未设置 CHECKPOINTER_REDIS_URL 时，需要 REDIS_HOST 或 REDIS_URL 以供 Checkpointer"
        )


def validate_runtime_environment() -> None:
    """根据当前进程环境变量校验；缺项收集后一次性抛出 RuntimeError。"""
    missing: list[str] = []
    _collect_code_sandbox_missing(missing)
    _collect_llm_key_missing(missing)
    _collect_embed_missing(missing)
    _collect_db_missing(missing)
    _collect_kv_missing(missing)
    _collect_vector_missing(missing)
    _collect_obs_missing(missing)
    _collect_checkpointer_missing(missing)
    if missing:
        detail = "\n  - ".join([""] + missing)
        raise RuntimeError(
            "运行所需环境变量未就绪，请在进程环境中设置（勿依赖应用内 .env 自动加载）："
            f"{detail}"
        )


def prepare_runtime_environment() -> None:
    """幂等：写入类型默认值并校验必填项。应在导入依赖配置的模块之前调用。"""
    global _PREPARED
    if _PREPARED:
        return
    apply_runtime_type_and_optional_defaults()
    validate_runtime_environment()
    _PREPARED = True


def reset_runtime_environment_prepare_for_tests() -> None:
    """仅测试用：允许重复执行 prepare。"""
    global _PREPARED
    _PREPARED = False
