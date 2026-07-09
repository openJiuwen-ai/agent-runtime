# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""LongTermMemory 单例：向量存储、Embedding、作用域配置，从进程环境变量读取。

关系型库连接与 openjiuwen_studio.ops.config.Settings 一致：DB_TYPE、DB_HOST、DB_PORT、DB_USER、DB_PASSWORD，
库名使用 AGENT_DB_NAME（MySQL 连接串中的 database 段即该库名）。表名由 ORM/迁移在库内创建，不会出现在 URL 里。
当 DB_TYPE 为 gaussdb/opengauss 时，会先生成同步 DSN，再切换为自定义 SQLAlchemy 方言
gaussdb+async_gaussdb://...，底层驱动使用 async-gaussdb。
"""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
from urllib.parse import quote

from openjiuwen_runtime.foundation.db.engine_options import build_async_engine_kwargs
from openjiuwen_runtime.foundation.log import get_logger

_log = get_logger(__name__)
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.store import DbBasedKVStore, DefaultDbStore, create_vector_store
from openjiuwen.core.memory import LongTermMemory, MemoryEngineConfig
from openjiuwen.core.memory.config.config import MemoryScopeConfig
from openjiuwen.core.retrieval.common.config import EmbeddingConfig
from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
from openjiuwen_studio.core.manager.model_manager.utils.security_utils import SecurityUtils
from sqlalchemy.ext.asyncio import create_async_engine

from .runtime_env import (
    clean_env_value,
    get_bool_env,
    get_env,
    get_int_env,
    llm_api_key_env_var_name,
    resolve_llm_api_key_from_env,
)
from .runtime_env_prepare import prepare_runtime_environment
from .studio_secrets import resolve_secret_env

# Service root: .../applications/ir_execution_service
_APP_ROOT = Path(__file__).resolve().parent.parent.parent


def get_database_url() -> str:
    db_type = get_env("DB_TYPE", "mysql").lower()
    if db_type == "mysql":
        # userinfo 中 @ : / 等需百分号编码，否则密码含特殊字符时 URL 会解析错误
        user = quote(get_env("DB_USER", "root"), safe="")
        password = quote(resolve_secret_env("DB_PASSWORD", ""), safe="")
        host = get_env("DB_HOST", "localhost")
        port = get_int_env("DB_PORT", 3306)
        database = get_env("AGENT_DB_NAME", "openjiuwen_agent")
        return (
            f"mysql+pymysql://{user}:{password}@"
            f"{host}:{port}/{database}?charset=utf8mb4"
        )
    if db_type in {"gaussdb", "opengauss"}:
        user = quote(get_env("DB_USER", "root"), safe="")
        password = quote(get_env("DB_PASSWORD", ""), safe="")
        host = get_env("DB_HOST", "localhost")
        port = get_int_env("DB_PORT", 5432)
        database = get_env("AGENT_DB_NAME", "openjiuwen_agent")
        return f"gaussdb://{user}:{password}@{host}:{port}/{database}"
    if db_type == "sqlite":
        db_path = Path(get_env("SQLITE_DB_PATH", "data/databases"))
        return f"sqlite:///{db_path / get_env('AGENT_SQLITE_DB', 'agent.db')}"
    raise ValueError(f"Unsupported database type: {db_type!r}")


def get_async_database_url(sync_db_url: str) -> str:
    if "mysql+pymysql" in sync_db_url:
        return sync_db_url.replace("pymysql", "aiomysql")
    if sync_db_url.startswith("gaussdb://"):
        from .gaussdb_sqlalchemy_dialect import ensure_async_gaussdb_installed, ensure_gaussdb_dialect_registered

        ensure_async_gaussdb_installed()
        ensure_gaussdb_dialect_registered()
        return sync_db_url.replace("gaussdb://", "gaussdb+async_gaussdb://", 1)
    if sync_db_url.startswith("sqlite:///"):
        return sync_db_url.replace("sqlite:///", "sqlite+aiosqlite:///")
    raise ValueError(f"Unsupported database URL for async engine: {sync_db_url}")


class MemoryEngineManager:
    _instance: LongTermMemory | None = None

    @classmethod
    async def init(cls) -> LongTermMemory:
        if cls._instance is not None:
            return cls._instance

        prepare_runtime_environment()
        provider, model_name, api_base, api_key = cls._default_llm_from_env()
        cls._validate_memory_llm_env(
            provider=provider, model_name=model_name, api_base=api_base, api_key=api_key
        )

        data_dir = cls._resolve_memory_data_dir()
        cls._ensure_local_dirs_if_needed(data_dir)
        vector_store = cls._create_vector_store(data_dir)
        embedding_model = cls._create_embedding_model()

        sync_database_url = get_database_url()
        async_database_url = get_async_database_url(sync_database_url)

        db_store = DefaultDbStore(
            create_async_engine(async_database_url, **build_async_engine_kwargs())
        )
        kv_store = cls._create_kv_store(async_database_url)

        memory_engine = LongTermMemory()
        await memory_engine.register_store(
            kv_store=kv_store,
            db_store=db_store,
            vector_store=vector_store,
            embedding_model=embedding_model,
        )
        memory_engine.set_config(
            MemoryEngineConfig(
                default_model_cfg=ModelRequestConfig(model=model_name),
                default_model_client_cfg=ModelClientConfig(
                    client_provider=provider,
                    api_key=api_key,
                    api_base=api_base,
                    verify_ssl=get_bool_env("LLM_SSL_VERIFY", True),
                ),
                crypto_key=cls._decode_master_key(),
                input_msg_max_len=get_int_env("MEMORY_INPUT_MSG_MAX_LEN", 8192),
                single_turn_history_summary_max_token=get_int_env(
                    "MEMORY_SINGLE_TURN_HISTORY_SUMMARY_MAX_TOKEN", 128
                ),
            )
        )
        await cls._set_scope_config(
            memory_engine=memory_engine,
            provider=provider,
            model_name=model_name,
            api_base=api_base,
            api_key=api_key,
        )
        cls._instance = memory_engine
        _log.info("Memory engine ready (env validated)")
        return cls._instance

    @classmethod
    def get_instance(cls) -> LongTermMemory:
        if cls._instance is None:
            raise RuntimeError("MemoryEngine has not been initialized. Call 'init' first.")
        return cls._instance

    @staticmethod
    def _default_llm_from_env() -> tuple[str, str, str, str]:
        provider = clean_env_value("DEFAULT_LLM_MODEL_PROVIDER", "OpenAI")
        model_name = clean_env_value("DEFAULT_LLM_MODEL_NAME")
        api_base = clean_env_value("DEFAULT_LLM_API_BASE")
        api_key = resolve_secret_env("DEFAULT_LLM_API_KEY", "")
        if not api_key and api_base:
            api_key = resolve_llm_api_key_from_env(api_base)
        return provider, model_name, api_base, api_key

    @staticmethod
    def _validate_memory_llm_env(
        *, provider: str, model_name: str, api_base: str, api_key: str
    ) -> None:
        if not str(provider or "").strip():
            raise RuntimeError("Memory engine requires DEFAULT_LLM_MODEL_PROVIDER.")
        if not str(model_name or "").strip():
            raise RuntimeError("Memory engine requires DEFAULT_LLM_MODEL_NAME.")
        if not str(api_base or "").strip():
            raise RuntimeError("Memory engine requires DEFAULT_LLM_API_BASE.")
        if not str(api_key or "").strip():
            raise RuntimeError(
                "Memory engine requires DEFAULT_LLM_API_KEY or "
                + llm_api_key_env_var_name(api_base)
            )

    @staticmethod
    def _create_kv_store(async_database_url: str):
        kv_type = clean_env_value("KV_STORE_TYPE", "redis").lower()
        if kv_type == "inmemory":
            from openjiuwen.core.foundation.store import InMemoryKVStore

            _log.info("Memory engine KV: InMemoryKVStore")
            return InMemoryKVStore()
        if kv_type == "redis":
            return MemoryEngineManager._create_redis_kv_store()
        if kv_type in ("db", "sql", "sqlite", "mysql"):
            _log.info("Memory engine KV: DbBasedKVStore (same DSN as DB_TYPE)")
            return DbBasedKVStore(
                create_async_engine(async_database_url, **build_async_engine_kwargs())
            )
        raise ValueError(
            f"Unknown KV_STORE_TYPE={kv_type!r}; expected 'redis', 'inmemory', or 'db'."
        )

    @staticmethod
    def _create_redis_kv_store():
        from redis.asyncio import Redis
        from openjiuwen.extensions.store.kv.redis_store import RedisStore

        # 每个业务场景只用自己的 Redis URL；记忆引擎只读 MEMORY_REDIS_URL，不允许回退/替补。
        url = clean_env_value("MEMORY_REDIS_URL")
        if not url:
            raise RuntimeError("KV_STORE_TYPE=redis 时必须设置 MEMORY_REDIS_URL（记忆引擎专用）。")
        _log.info(
            "Memory engine KV: RedisStore (%s)",
            url.split("@")[-1] if "@" in url else url,
        )
        client = Redis.from_url(url, decode_responses=True)
        return RedisStore(redis=client)

    @staticmethod
    def _resolve_milvus_token() -> str | None:
        raw = resolve_secret_env("MILVUS_TOKEN", "").strip()
        if raw:
            return raw
        user = get_env("MILVUS_USER", "root").strip() or "root"
        password = resolve_secret_env("MILVUS_PASSWORD", "").strip()
        if not password:
            return None
        return f"{user}:{password}"

    @staticmethod
    def _resolve_memory_data_dir() -> Path:
        memory_data_path = Path(
            clean_env_value("MEMORY_DATA_PATH", "memory-data") or "memory-data"
        )
        if not memory_data_path.is_absolute():
            memory_data_path = _APP_ROOT / memory_data_path
        return memory_data_path

    @staticmethod
    def _ensure_local_dirs_if_needed(memory_data_dir: Path) -> None:
        """仅在确实需要本地落盘时创建目录，避免启动阶段无条件创建。

        - DB_TYPE=sqlite：需要 SQLITE_DB_PATH
        - INDEX_MANAGER_TYPE=chroma：需要 MEMORY_DATA_PATH（向量库持久化目录）
        """
        db_type = clean_env_value("DB_TYPE", "mysql").lower()
        index_manager_type = clean_env_value("INDEX_MANAGER_TYPE", "milvus").lower()

        if db_type == "sqlite":
            db_path = Path(get_env("SQLITE_DB_PATH", "data/databases"))
            if not db_path.is_absolute():
                db_path = _APP_ROOT / db_path
            db_path.mkdir(parents=True, exist_ok=True)

        if index_manager_type == "chroma":
            memory_data_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _create_vector_store(data_dir: Path):
        index_manager_type = clean_env_value("INDEX_MANAGER_TYPE", "milvus").lower()
        if index_manager_type == "milvus":
            milvus_uri = (
                f"http://{get_env('MILVUS_HOST', 'localhost')}:"
                f"{get_int_env('MILVUS_PORT', 19530)}"
            )
            _log.info("Creating Milvus vector store for memory engine")
            return create_vector_store(
                store_type="milvus",
                milvus_uri=milvus_uri,
                milvus_token=MemoryEngineManager._resolve_milvus_token(),
                alias="memory_milvus_connection",
            )
        if index_manager_type == "chroma":
            _log.info("Creating Chroma vector store for memory engine")
            return create_vector_store("chroma", persist_directory=str(data_dir))
        raise ValueError(
            f"Unknown vector db type: {index_manager_type!r}; expected 'chroma' or 'milvus'."
        )

    @staticmethod
    def _decode_master_key() -> bytes:
        if os.getenv("HUAWEICLOUD_KMS_ENABLED", "false").lower() == "true":
            try:
                mk = SecurityUtils(use_kms=True).get_initialized_master_key()
                return mk if mk else b""
            except Exception as e:
                _log.warning(
                    "KMS root key unavailable for memory crypto; memory field encryption disabled: %s",
                    e,
                )
                return b""
        encoded_key = get_env("SERVER_AES_MASTER_KEY_ENV") or get_env("SERVER_AES_MASTER_KEY")
        if not encoded_key:
            return b""
        try:
            return base64.b64decode(encoded_key)
        except binascii.Error:
            _log.warning("Invalid SERVER_AES master key value; memory encryption disabled.")
            return b""

    @staticmethod
    def _embed_env() -> tuple[str, str, str]:
        name = clean_env_value("EMBED_MODEL_NAME")
        base = clean_env_value("EMBED_BASE_URL")
        key = resolve_secret_env("EMBED_API_KEY", "")
        return name, base, key

    @staticmethod
    def _create_embedding_model():
        embed_model_name, embed_base_url, embed_api_key = MemoryEngineManager._embed_env()
        if not embed_model_name or not embed_base_url:
            raise RuntimeError(
                "Memory engine requires EMBED_MODEL_NAME and EMBED_BASE_URL in environment."
            )
        if not embed_api_key:
            raise RuntimeError("Memory engine embedding requires EMBED_API_KEY in environment.")

        return APIEmbedding(
            config=EmbeddingConfig(
                model_name=embed_model_name,
                base_url=embed_base_url,
                api_key=embed_api_key,
            )
        )

    @classmethod
    async def _set_scope_config(
        cls,
        *,
        memory_engine: LongTermMemory,
        provider: str,
        model_name: str,
        api_base: str,
        api_key: str,
    ) -> None:
        scope_id = clean_env_value("MEMORY_SCOPE_ID", "ir_agent_runner_memory").strip()
        if not scope_id:
            return

        en, eb, ek = cls._embed_env()
        scope_config = MemoryScopeConfig(
            model_cfg=ModelRequestConfig(model=model_name),
            model_client_cfg=ModelClientConfig(
                client_provider=provider,
                api_key=api_key,
                api_base=api_base,
                verify_ssl=get_bool_env("LLM_SSL_VERIFY", True),
            ),
            embedding_cfg=EmbeddingConfig(
                model_name=en,
                base_url=eb,
                api_key=ek,
            ),
        )
        await memory_engine.set_scope_config(scope_id, scope_config)
