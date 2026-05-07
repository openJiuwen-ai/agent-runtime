# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Real database system tests for GaussDB/openGauss.

This suite is opt-in and skipped by default. It is intended for environments
with a reachable real database. Enable with:

    GAUSSDB_REAL_ST_ENABLED=1
"""

import os
import socket
import unittest
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# foundation settings require IP at import time.
os.environ.setdefault("IP", "127.0.0.1")

from openjiuwen_runtime.foundation.db.dialects import ensure_gaussdb_dialect_registered


def _pick_env(*names: str, default: str = "") -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return default


def _is_enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _check_tcp_connectivity(host: str, port: int, timeout_seconds: float = 5.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True, ""
    except OSError as exc:
        return False, str(exc)


class TestGaussDBRealDatabaseSystem(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        enabled = _pick_env("GAUSSDB_REAL_ST_ENABLED", default="0")
        if not _is_enabled(enabled):
            raise unittest.SkipTest("real-db ST is disabled; set GAUSSDB_REAL_ST_ENABLED=1 to enable")

        db_type = _pick_env("GAUSSDB_REAL_DB_TYPE", "DB_TYPE", default="gaussdb").lower()
        if db_type not in {"gaussdb", "opengauss"}:
            raise unittest.SkipTest(f"unsupported db type for this ST: {db_type!r}")

        host = _pick_env("GAUSSDB_REAL_DB_HOST", "DB_HOST")
        port = _pick_env("GAUSSDB_REAL_DB_PORT", "DB_PORT", default="5432")
        user = _pick_env("GAUSSDB_REAL_DB_USER", "DB_USER")
        password = _pick_env("GAUSSDB_REAL_DB_PASSWORD", "DB_PASSWORD")
        database = _pick_env("GAUSSDB_REAL_DB_NAME", "DB_NAME", "AGENT_DB_NAME")
        strict_network = _is_enabled(_pick_env("GAUSSDB_REAL_ST_STRICT_NETWORK", default="0"))

        missing = []
        for key, value in (
            ("HOST", host),
            ("PORT", port),
            ("USER", user),
            ("PASSWORD", password),
            ("DATABASE", database),
        ):
            if not value:
                missing.append(key)

        if missing:
            raise unittest.SkipTest(
                "real-db ST missing required env vars: " + ", ".join(missing)
            )

        try:
            port_int = int(port)
        except ValueError as exc:
            raise unittest.SkipTest(f"invalid database port: {port!r}") from exc

        reachable, error_message = _check_tcp_connectivity(host, port_int)
        if not reachable:
            detail = f"real-db ST cannot reach database endpoint {host}:{port_int}: {error_message}"
            if strict_network:
                raise RuntimeError(detail)
            raise unittest.SkipTest(detail)

        ensure_gaussdb_dialect_registered()

        user_enc = quote(user, safe="")
        password_enc = quote(password, safe="")
        cls._database = database
        cls._async_url = f"{db_type}+async_gaussdb://{user_enc}:{password_enc}@{host}:{port}/{database}"

    async def asyncSetUp(self):
        self.engine = create_async_engine(self._async_url, pool_pre_ping=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_can_connect_and_select_one(self):
        async with self.engine.connect() as conn:
            value = (await conn.execute(text("SELECT 1"))).scalar_one()
        self.assertEqual(value, 1)

    async def test_can_query_version_and_current_database(self):
        async with self.engine.connect() as conn:
            version = (await conn.execute(text("SELECT version()"))).scalar_one()
            current_db = (await conn.execute(text("SELECT current_database()"))).scalar_one()

        self.assertTrue(isinstance(version, str) and version)
        self.assertEqual(str(current_db), self._database)

    async def test_reflection_smoke_pg_catalog(self):
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                text(
                    """
                    SELECT a.attname
                    FROM pg_catalog.pg_attribute AS a
                    JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
                    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'pg_catalog'
                      AND c.relname = 'pg_class'
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                    ORDER BY a.attnum
                    """
                )
            )
            column_names = [str(name) for name in rows.scalars().all()]

        self.assertTrue(column_names)
        self.assertIn("relname", column_names)
