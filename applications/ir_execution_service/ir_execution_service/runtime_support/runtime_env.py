# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""进程环境变量读取与 LLM_KEY__ 变量名推导。

本模块不调用 load_dotenv；由应用入口（如 ir_execution_service_app）在启动时加载 .env。
启动校验与默认值请调用 runtime_env_prepare.prepare_runtime_environment。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from .studio_secrets import resolve_secret_env

# Service root: .../applications/ir_execution_service
_APP_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE_PATH = _APP_ROOT / ".env"

LLM_API_KEY_ENV_PREFIX = "LLM_KEY__"


def load_runtime_env() -> Path:
    """兼容旧名：等价于 prepare_runtime_environment()，不再读取磁盘 .env。"""
    from .runtime_env_prepare import prepare_runtime_environment

    prepare_runtime_environment()
    return ENV_FILE_PATH


def clean_env_value(name: str, default: str = "") -> str:
    v = (os.environ.get(name) or default).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].strip()
    return v


def resolve_memory_scope_id(*, raw_memory_scope_id: str = "", default_memory_scope_id: str = "") -> str:
    """解析记忆隔离 scope id。

    优先级：
    1) raw_memory_scope_id（来自 agent/runtime 配置）
    2) 环境变量 MEMORY_SCOPE_ID
    3) default_memory_scope_id（调用方提供的默认）
    4) 兜底 ir_agent_runner_memory
    """
    raw = str(raw_memory_scope_id or "").strip()
    if raw:
        return raw
    env = clean_env_value("MEMORY_SCOPE_ID")
    if env:
        return env
    d = str(default_memory_scope_id or "").strip()
    return d if d else "ir_agent_runner_memory"


def get_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def get_bool_env(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name, "true" if default else "false") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name, str(default)) or "").strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def slug_from_llm_base_url(base_url: str) -> str:
    url = (base_url or "").strip().strip('"').strip("'")
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").replace(".", "_")
    path = (parsed.path or "").strip("/").replace("/", "_")
    parts = [part for part in (host, path) if part]
    raw = "_".join(parts) if parts else url
    return re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").upper()


def llm_api_key_env_var_name(base_url: str) -> str:
    slug = slug_from_llm_base_url(base_url)
    if not slug:
        return f"{LLM_API_KEY_ENV_PREFIX}<SLUG_FROM_BASE_URL>"
    return f"{LLM_API_KEY_ENV_PREFIX}{slug}"


def env_key_from_base_url(base_url: str) -> str:
    return llm_api_key_env_var_name(base_url)


def resolve_llm_api_key_from_env(base_url: str = "") -> str:
    env_key = llm_api_key_env_var_name(base_url)
    if "<SLUG_FROM_BASE_URL>" in env_key:
        return ""
    return resolve_secret_env(env_key, "")


def resolve_verify_ssl_from_env() -> bool:
    return get_bool_env("LLM_SSL_VERIFY", False)


def missing_api_key_message(base_url: str = "") -> str:
    return (
        f"请在环境变量中设置 DEFAULT_LLM_API_KEY，或设置 {llm_api_key_env_var_name(base_url)}（与 DEFAULT_LLM_API_BASE 对应）。"
    )
