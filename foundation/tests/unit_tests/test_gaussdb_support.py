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
        self.management_root = self.repo_root / "management"
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
            self.management_root,
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
            "openjiuwen_runtime.management",
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