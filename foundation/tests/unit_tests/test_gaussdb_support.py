# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import builtins
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from sqlalchemy.ext.asyncio import create_async_engine


class TestGaussDBSupport(TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[3]
        self.foundation_root = self.repo_root / "foundation"
        self.server_root = self.repo_root / "server"
        self.management_root = self.repo_root / "management"
        self.ir_execution_root = self.repo_root / "applications" / "ir_execution_service"
        self._original_env = os.environ.copy()

        os.environ.update(
            {
                "DB_TYPE": "gaussdb",
                "DB_HOST": "127.0.0.1",
                "DB_PORT": "5432",
                "DB_USER": "gauss_user",
                "DB_PASSWORD": "p@ss",
                "DB_NAME": "runtime_db",
                "AGENT_DB_NAME": "agent_db",
                "OPS_DB_NAME": "ops_db",
                "IP": "127.0.0.1",
            }
        )

        for extra_path in (
            self.foundation_root,
            self.server_root,
            self.management_root,
            self.ir_execution_root,
        ):
            extra_path_str = str(extra_path)
            if extra_path_str not in sys.path:
                sys.path.insert(0, extra_path_str)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._original_env)

        for module_name in [
            "openjiuwen_runtime.foundation.config",
            "openjiuwen_runtime.foundation.db",
            "openjiuwen_runtime.foundation.db.dialects",
            "openjiuwen_runtime.foundation.db.dialects.gaussdb_asyncgaussdb",
            "openjiuwen_runtime.foundation.db.gaussdb_handler",
            "openjiuwen_runtime.server.main",
            "openjiuwen_runtime.management",
            "runtime_support.gaussdb_sqlalchemy_dialect",
            "runtime_support.memory_engine_start",
            "runtime_support.runtime_env_prepare",
        ]:
            sys.modules.pop(module_name, None)

    def _patch_async_gaussdb_missing(self):
        original_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "async_gaussdb" or name.startswith("async_gaussdb."):
                raise ModuleNotFoundError("No module named 'async_gaussdb'", name="async_gaussdb")
            return original_import(name, globals, locals, fromlist, level)

        return mock.patch("builtins.__import__", side_effect=guarded_import)

    def _evict_async_gaussdb_modules(self):
        removed_modules = {}
        for module_name in list(sys.modules):
            if module_name == "async_gaussdb" or module_name.startswith("async_gaussdb."):
                removed_modules[module_name] = sys.modules.pop(module_name)
        return removed_modules

    def test_management_exports_gaussdb_handler(self):
        management_module = importlib.import_module("openjiuwen_runtime.management")

        self.assertEqual(management_module.GaussDBHandler.__name__, "GaussDBHandler")

    def test_server_selects_gaussdb_handler(self):
        server_main = importlib.import_module("openjiuwen_runtime.server.main")

        self.assertEqual(type(server_main.db_handler).__name__, "GaussDBHandler")
        self.assertEqual(
            server_main.db_handler.database_url,
            "gaussdb+async_gaussdb://gauss_user:p%40ss@127.0.0.1:5432/runtime_db",
        )

    def test_server_selects_gaussdb_handler_for_opengauss(self):
        os.environ["DB_TYPE"] = "opengauss"
        for module_name in [
            "openjiuwen_runtime.foundation.config",
            "openjiuwen_runtime.foundation.db.gaussdb_handler",
            "openjiuwen_runtime.server.main",
        ]:
            sys.modules.pop(module_name, None)

        server_main = importlib.import_module("openjiuwen_runtime.server.main")

        self.assertEqual(type(server_main.db_handler).__name__, "GaussDBHandler")

    def test_runtime_support_generates_gauss_urls_for_gaussdb_and_opengauss(self):
        memory_engine_start = importlib.import_module("runtime_support.memory_engine_start")

        sync_url = memory_engine_start.get_database_url()
        async_url = memory_engine_start.get_async_database_url(sync_url)
        self.assertEqual(sync_url, "gaussdb://gauss_user:p%40ss@127.0.0.1:5432/agent_db")
        self.assertEqual(async_url, "gaussdb+async_gaussdb://gauss_user:p%40ss@127.0.0.1:5432/agent_db")

        os.environ["DB_TYPE"] = "opengauss"
        sync_url = memory_engine_start.get_database_url()
        async_url = memory_engine_start.get_async_database_url(sync_url)
        self.assertEqual(sync_url, "gaussdb://gauss_user:p%40ss@127.0.0.1:5432/agent_db")
        self.assertEqual(async_url, "gaussdb+async_gaussdb://gauss_user:p%40ss@127.0.0.1:5432/agent_db")

    def test_runtime_env_prepare_sets_default_port_for_opengauss(self):
        os.environ["DB_TYPE"] = "opengauss"

        runtime_env_prepare = importlib.import_module("runtime_support.runtime_env_prepare")

        # Some dependency imports may materialize DB_PORT from external env/.env.
        # Remove it immediately before applying defaults to verify opengauss fallback.
        os.environ.pop("DB_PORT", None)
        runtime_env_prepare.apply_runtime_type_and_optional_defaults()

        self.assertEqual(os.environ["DB_PORT"], "5432")

    def test_custom_dialect_supports_opengauss_alias(self):
        gauss_dialect = importlib.import_module("openjiuwen_runtime.foundation.db.dialects.gaussdb_asyncgaussdb")
        gauss_dialect.ensure_gaussdb_dialect_registered()

        engine = create_async_engine("opengauss+async_gaussdb://gauss_user:p%40ss@127.0.0.1:5432/runtime_db")
        try:
            self.assertEqual(type(engine.dialect).__name__, "PGDialect_async_gaussdb")
            self.assertEqual(engine.dialect.driver, "async_gaussdb")
        finally:
            import asyncio

            asyncio.run(engine.dispose())

    def test_custom_dialect_parses_real_gaussdb_version_string(self):
        gauss_dialect = importlib.import_module("openjiuwen_runtime.foundation.db.dialects.gaussdb_asyncgaussdb")

        class FakeConnection:
            def exec_driver_sql(self, _sql: str):
                return SimpleNamespace(
                    scalar=lambda: "gaussdb (GaussDB Kernel 505.2.1.SPC0600 build 2aa20d4e) compiled at 2025-05-31 22:48:04 commit 10460 last mr 23863 release"
                )

        dialect = gauss_dialect.PGDialect_async_gaussdb()
        version = dialect._get_server_version_info(FakeConnection())

        self.assertEqual(version, (11, 0))

    def test_server_mysql_import_does_not_require_async_gaussdb(self):
        os.environ["DB_TYPE"] = "mysql"
        removed_modules = self._evict_async_gaussdb_modules()
        for module_name in [
            "openjiuwen_runtime.foundation.config",
            "openjiuwen_runtime.foundation.db.gaussdb_handler",
            "openjiuwen_runtime.server.main",
        ]:
            sys.modules.pop(module_name, None)

        try:
            with self._patch_async_gaussdb_missing():
                server_main = importlib.import_module("openjiuwen_runtime.server.main")
        finally:
            sys.modules.update(removed_modules)

        self.assertEqual(type(server_main.db_handler).__name__, "MySQLHandler")

    def test_memory_engine_start_mysql_path_does_not_require_async_gaussdb(self):
        os.environ["DB_TYPE"] = "mysql"
        removed_modules = self._evict_async_gaussdb_modules()
        for module_name in [
            "runtime_support.gaussdb_sqlalchemy_dialect",
            "runtime_support.memory_engine_start",
        ]:
            sys.modules.pop(module_name, None)

        try:
            with self._patch_async_gaussdb_missing():
                memory_engine_start = importlib.import_module("runtime_support.memory_engine_start")
                sync_url = memory_engine_start.get_database_url()
                async_url = memory_engine_start.get_async_database_url(sync_url)
        finally:
            sys.modules.update(removed_modules)

        self.assertEqual(sync_url, "mysql+pymysql://gauss_user:p%40ss@127.0.0.1:5432/agent_db?charset=utf8mb4")
        self.assertEqual(async_url, "mysql+aiomysql://gauss_user:p%40ss@127.0.0.1:5432/agent_db?charset=utf8mb4")

    def test_gaussdb_handler_raises_helpful_error_when_driver_missing(self):
        removed_modules = self._evict_async_gaussdb_modules()
        sys.modules.pop("openjiuwen_runtime.foundation.db.gaussdb_handler", None)

        try:
            with self._patch_async_gaussdb_missing():
                gaussdb_handler = importlib.import_module("openjiuwen_runtime.foundation.db.gaussdb_handler")
                with self.assertRaises(ModuleNotFoundError) as exc:
                    gaussdb_handler.GaussDBHandler()
        finally:
            sys.modules.update(removed_modules)

        self.assertIn("openjiuwen-runtime-foundation[gaussdb]", str(exc.exception))
        self.assertIn("async-gaussdb>=0.30.4", str(exc.exception))