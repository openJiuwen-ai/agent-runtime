"""
EDPAgent 配置读取（替代旧版 pydantic-settings）。

变量命名对齐 PLANNING_AGENT_MODEL_* 格式，与 .env 保持一致。
支持自定义 LLM 请求头：token / userId / 额外 JSON header。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel

# 加载 a2a_service/.env 到 os.environ，让 os.getenv 能读到 PLANNING_AGENT_MODEL_* 等变量
# 优先级：CONFIG_PATH > a2a_service/.env（默认）
_CONFIG_PATH = os.environ.get("CONFIG_PATH")
if _CONFIG_PATH:
    _ENV_FILE = Path(_CONFIG_PATH)
else:
    # 本模块位置：applications/a2a_service/agents/EDPAgent/config.py
    # a2a_service/.env 在上三级目录
    _ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE, override=False)


class DPASettings(BaseModel):
    # ── LLM ─────────────────────────────────────────────────────────────────
    llm_provider: str = "OpenAI"
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_model_name: str = ""
    llm_verify_ssl: bool = False
    llm_timeout: float = 120.0
    custom_headers: Optional[dict[str, Any]] = None

    # ── Redis（Checkpointer）────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_checkpointer_ttl_minutes: int = 60

    # ── DPA Agent ───────────────────────────────────────────────────────────
    dpa_agent_id: str = "edp_agent"
    dpa_agent_name: str = "EDP Agent"
    dpa_max_iterations: int = 30

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            pwd = quote_plus(self.redis_password)
            return f"redis://:{pwd}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


def _build_custom_headers() -> Optional[dict[str, Any]]:
    """
    从环境变量构建自定义 header 字典。

    token / userId 的 header 名必须通过 _HEADER 变量显式指定，不提供默认值。
    EXTRA_HEADERS 支持 JSON 格式注入任意额外 header。
    """
    headers: dict[str, Any] = {}

    token = os.getenv("PLANNING_AGENT_MODEL_TOKEN", "")
    if token:
        token_header = os.getenv("PLANNING_AGENT_MODEL_TOKEN_HEADER", "")
        if not token_header:
            raise ValueError(
                "PLANNING_AGENT_MODEL_TOKEN is set but PLANNING_AGENT_MODEL_TOKEN_HEADER is missing. "
                "Please set PLANNING_AGENT_MODEL_TOKEN_HEADER to the header name required by your gateway."
            )
        headers[token_header] = token

    user_id = os.getenv("PLANNING_AGENT_MODEL_USER_ID", "")
    if user_id:
        user_id_header = os.getenv("PLANNING_AGENT_MODEL_USER_ID_HEADER", "")
        if not user_id_header:
            raise ValueError(
                "PLANNING_AGENT_MODEL_USER_ID is set but PLANNING_AGENT_MODEL_USER_ID_HEADER is missing. "
                "Please set PLANNING_AGENT_MODEL_USER_ID_HEADER to the header name required by your gateway."
            )
        headers[user_id_header] = user_id

    extra_raw = os.getenv("PLANNING_AGENT_MODEL_EXTRA_HEADERS", "")
    if extra_raw:
        try:
            extra = json.loads(extra_raw)
            if isinstance(extra, dict):
                headers.update(extra)
            else:
                logger.warning(
                    "[DPA] PLANNING_AGENT_MODEL_EXTRA_HEADERS must be a JSON object, skipping"
                )
        except json.JSONDecodeError as e:
            logger.warning(f"[DPA] Invalid JSON in PLANNING_AGENT_MODEL_EXTRA_HEADERS: {e}, skipping")

    return headers or None


def _infer_provider(api_base: str) -> str:
    """由 base_url 推导 provider。"""
    if "dashscope" in api_base or "aliyun" in api_base:
        return "DashScope"
    if "siliconflow" in api_base:
        return "SiliconFlow"
    return "OpenAI"


@lru_cache
def get_settings() -> DPASettings:
    """从环境变量构建 DPASettings。"""
    api_base = os.getenv("PLANNING_AGENT_MODEL_BASE_URL", "")

    try:
        timeout = float(os.getenv("PLANNING_AGENT_MODEL_TIMEOUT", "120"))
    except ValueError:
        timeout = 120.0

    return DPASettings(
        llm_provider=_infer_provider(api_base),
        llm_api_base=api_base,
        llm_api_key=os.getenv("PLANNING_AGENT_MODEL_API_KEY", ""),
        llm_model_name=os.getenv("PLANNING_AGENT_MODEL_NAME", ""),
        llm_verify_ssl=os.getenv("SKILL_LLM_TLS_VERIFY", "false").lower() == "true",
        llm_timeout=timeout,
        custom_headers=_build_custom_headers(),
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=int(os.getenv("REDIS_PORT", "6379")),
        redis_db=int(os.getenv("REDIS_DB", "0")),
        redis_password=os.getenv("REDIS_PASSWORD", ""),
        redis_checkpointer_ttl_minutes=int(
            os.getenv("REDIS_CHECKPOINTER_TTL_MINUTES", "60")
        ),
        dpa_agent_id=os.getenv("DPA_AGENT_ID", "edp_agent"),
        dpa_agent_name=os.getenv("DPA_AGENT_NAME", "EDP Agent"),
        dpa_max_iterations=int(os.getenv("DPA_MAX_ITERATIONS", "30")),
    )
