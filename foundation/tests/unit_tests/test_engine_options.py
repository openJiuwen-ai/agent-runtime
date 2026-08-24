# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""engine_options 与各方言 handler 的查询超时。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from openjiuwen_runtime.foundation.db import engine_options as eo
from openjiuwen_runtime.foundation.db.gaussdb_handler import GaussDBHandler
from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
from openjiuwen_runtime.foundation.db.postgresql_handler import PostgreSQLHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler


class TestQueryTimeout(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("RUNTIME_DB_QUERY_TIMEOUT", None)

    def test_default_query_timeout_is_10s(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RUNTIME_DB_QUERY_TIMEOUT", None)
            self.assertEqual(eo.get_query_timeout_seconds(), 5.0)

    def test_env_disables_timeout(self) -> None:
        with mock.patch.dict(os.environ, {"RUNTIME_DB_QUERY_TIMEOUT": "0"}):
            self.assertIsNone(eo.get_query_timeout_seconds())

    def test_postgresql_init_sets_timeout_and_search_path(self) -> None:
        with mock.patch.dict(os.environ, {"RUNTIME_DB_QUERY_TIMEOUT": "5"}):
            handler = PostgreSQLHandler(schema="app")
        args = handler.connect_args
        self.assertEqual(args["command_timeout"], 5.0)
        self.assertEqual(args["server_settings"]["statement_timeout"], "5000")
        self.assertEqual(args["server_settings"]["search_path"], "app")
        timeout_only = handler._prepare_db_timeout_args()
        self.assertNotIn("search_path", timeout_only.get("server_settings", {}))

    def test_gaussdb_timeout_args(self) -> None:
        with mock.patch.dict(os.environ, {"RUNTIME_DB_QUERY_TIMEOUT": "5"}):
            args = GaussDBHandler._prepare_db_timeout_args(None)
        self.assertEqual(args["command_timeout"], 5.0)
        self.assertEqual(args["server_settings"]["statement_timeout"], "5000")
        self.assertNotIn("search_path", args.get("server_settings", {}))

    def test_mysql_init_sets_max_execution_time(self) -> None:
        with mock.patch.dict(os.environ, {"RUNTIME_DB_QUERY_TIMEOUT": "8"}):
            handler = MySQLHandler(
                host="127.0.0.1",
                port=3306,
                database="db",
                user="u",
                password="p",
            )
        args = handler.connect_args
        self.assertNotIn("read_timeout", args)
        self.assertNotIn("write_timeout", args)
        self.assertEqual(
            args["init_command"],
            "SET SESSION MAX_EXECUTION_TIME=8000",
        )

    def test_sqlite_init_sets_busy_timeout(self) -> None:
        with mock.patch.dict(os.environ, {"RUNTIME_DB_QUERY_TIMEOUT": "4"}):
            handler = SQLiteHandler(":memory:")
        self.assertEqual(handler.connect_args["timeout"], 4.0)

    def test_build_kwargs_does_not_inject_dialect_timeout(self) -> None:
        with mock.patch.dict(os.environ, {"RUNTIME_DB_QUERY_TIMEOUT": "10"}):
            kwargs = eo.build_async_engine_kwargs()
        self.assertEqual(kwargs["connect_args"], {})


if __name__ == "__main__":
    unittest.main()
