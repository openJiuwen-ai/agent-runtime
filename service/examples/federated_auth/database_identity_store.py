# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SQLite-backed identity store for the runnable federated-auth example."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from .domain import ExternalIdentity, FederationConnection, LocalPrincipal
from .identity_store import FederatedIdentityStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS virtual_organizations (
    organization_id TEXT PRIMARY KEY,
    organization_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS federation_connections (
    connection_id TEXT PRIMARY KEY,
    issuer TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    default_role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (organization_id)
        REFERENCES virtual_organizations(organization_id)
);

CREATE TABLE IF NOT EXISTS virtual_users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    email TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS federated_identities (
    connection_id TEXT NOT NULL,
    issuer TEXT NOT NULL,
    external_subject TEXT NOT NULL,
    local_user_id TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    first_login_at TEXT NOT NULL,
    last_login_at TEXT NOT NULL,
    PRIMARY KEY (connection_id, issuer, external_subject),
    FOREIGN KEY (connection_id)
        REFERENCES federation_connections(connection_id),
    FOREIGN KEY (local_user_id)
        REFERENCES virtual_users(user_id)
);

CREATE TABLE IF NOT EXISTS organization_memberships (
    organization_id TEXT NOT NULL,
    local_user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, local_user_id),
    FOREIGN KEY (organization_id)
        REFERENCES virtual_organizations(organization_id),
    FOREIGN KEY (local_user_id)
        REFERENCES virtual_users(user_id)
);
"""


class DatabaseFederatedIdentityStore(FederatedIdentityStore):
    """Persist shadow organizations and users in one SQLite database file."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._initialized = False
        self._initialize_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Open the SQLite file and initialize its example schema once."""
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            database = await aiosqlite.connect(self._database_path)
            try:
                await _configure_database(database)
                await database.executescript(_SCHEMA)
                await database.commit()
                self._initialized = True
            finally:
                await database.close()

    async def resolve_or_create(
        self,
        connection: FederationConnection,
        identity: ExternalIdentity,
    ) -> LocalPrincipal:
        self.validate_binding(connection, identity)
        database = await self._open_database()

        try:
            async with self._write_lock:
                await database.execute("BEGIN IMMEDIATE")
                await self._ensure_connection(database, connection)
                principal = await self._resolve_or_create_user(
                    database,
                    connection,
                    identity,
                )
                await database.commit()
                return principal
        except Exception:
            if database.in_transaction:
                await database.rollback()
            raise
        finally:
            await database.close()

    async def find(
        self,
        *,
        connection_id: str,
        issuer: str,
        external_subject: str,
    ) -> LocalPrincipal | None:
        database = await self._open_database()
        try:
            cursor = await database.execute(
                """
                SELECT
                    u.user_id,
                    c.organization_id,
                    u.display_name,
                    u.email,
                    m.role
                FROM federated_identities AS i
                JOIN virtual_users AS u
                  ON u.user_id = i.local_user_id
                JOIN federation_connections AS c
                  ON c.connection_id = i.connection_id
                JOIN organization_memberships AS m
                  ON m.organization_id = c.organization_id
                 AND m.local_user_id = u.user_id
                WHERE i.connection_id = ?
                  AND i.issuer = ?
                  AND i.external_subject = ?
                """,
                (connection_id, issuer, external_subject),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return _principal_from_row(row) if row is not None else None
        finally:
            await database.close()

    async def close(self) -> None:
        """Connections are scoped to operations, so there is nothing to close."""

    async def _open_database(self) -> aiosqlite.Connection:
        await self.initialize()
        database = await aiosqlite.connect(self._database_path)
        await _configure_database(database)
        return database

    async def _ensure_connection(
        self,
        database: aiosqlite.Connection,
        connection: FederationConnection,
    ) -> None:
        now = _utc_now()
        await database.execute(
            """
            INSERT INTO virtual_organizations (
                organization_id,
                organization_name,
                created_at
            ) VALUES (?, ?, ?)
            ON CONFLICT(organization_id) DO UPDATE SET
                organization_name = excluded.organization_name
            """,
            (
                connection.organization_id,
                connection.organization_name,
                now,
            ),
        )

        cursor = await database.execute(
            """
            SELECT issuer, organization_id, default_role
            FROM federation_connections
            WHERE connection_id = ?
            """,
            (connection.connection_id,),
        )
        existing = await cursor.fetchone()
        await cursor.close()
        if existing is None:
            await database.execute(
                """
                INSERT INTO federation_connections (
                    connection_id,
                    issuer,
                    organization_id,
                    default_role,
                    created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    connection.connection_id,
                    connection.issuer,
                    connection.organization_id,
                    connection.default_role,
                    now,
                ),
            )
            return

        settings = (
            existing["issuer"],
            existing["organization_id"],
            existing["default_role"],
        )
        expected = (
            connection.issuer,
            connection.organization_id,
            connection.default_role,
        )
        if settings != expected:
            raise ValueError("connection_id is already bound to different settings")

    async def _resolve_or_create_user(
        self,
        database: aiosqlite.Connection,
        connection: FederationConnection,
        identity: ExternalIdentity,
    ) -> LocalPrincipal:
        cursor = await database.execute(
            """
            SELECT local_user_id
            FROM federated_identities
            WHERE connection_id = ?
              AND issuer = ?
              AND external_subject = ?
            """,
            (
                identity.connection_id,
                identity.issuer,
                identity.external_subject,
            ),
        )
        row = await cursor.fetchone()
        await cursor.close()

        now = _utc_now()
        user_id = row["local_user_id"] if row is not None else f"user_{uuid4().hex}"
        if row is None:
            await database.execute(
                """
                INSERT INTO virtual_users (
                    user_id,
                    display_name,
                    email,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, identity.display_name, identity.email, now, now),
            )
            await database.execute(
                """
                INSERT INTO federated_identities (
                    connection_id,
                    issuer,
                    external_subject,
                    local_user_id,
                    attributes_json,
                    first_login_at,
                    last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity.connection_id,
                    identity.issuer,
                    identity.external_subject,
                    user_id,
                    _attributes_json(identity),
                    now,
                    now,
                ),
            )
            await database.execute(
                """
                INSERT INTO organization_memberships (
                    organization_id,
                    local_user_id,
                    role,
                    created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    connection.organization_id,
                    user_id,
                    connection.default_role,
                    now,
                ),
            )
        else:
            await database.execute(
                """
                UPDATE virtual_users
                SET display_name = ?, email = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (identity.display_name, identity.email, now, user_id),
            )
            await database.execute(
                """
                UPDATE federated_identities
                SET attributes_json = ?, last_login_at = ?
                WHERE connection_id = ?
                  AND issuer = ?
                  AND external_subject = ?
                """,
                (
                    _attributes_json(identity),
                    now,
                    identity.connection_id,
                    identity.issuer,
                    identity.external_subject,
                ),
            )

        cursor = await database.execute(
            """
            SELECT
                u.user_id,
                m.organization_id,
                u.display_name,
                u.email,
                m.role
            FROM virtual_users AS u
            JOIN organization_memberships AS m
              ON m.local_user_id = u.user_id
            WHERE u.user_id = ?
              AND m.organization_id = ?
            """,
            (user_id, connection.organization_id),
        )
        principal_row = await cursor.fetchone()
        await cursor.close()
        if principal_row is None:  # pragma: no cover - protected by schema writes
            raise RuntimeError("local principal was not persisted")
        return _principal_from_row(principal_row)


def _principal_from_row(row: aiosqlite.Row) -> LocalPrincipal:
    return LocalPrincipal(
        user_id=row["user_id"],
        organization_id=row["organization_id"],
        display_name=row["display_name"],
        email=row["email"],
        roles=(row["role"],),
    )


def _attributes_json(identity: ExternalIdentity) -> str:
    return json.dumps(identity.attributes, ensure_ascii=False, sort_keys=True)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


async def _configure_database(database: aiosqlite.Connection) -> None:
    database.row_factory = aiosqlite.Row
    await database.execute("PRAGMA foreign_keys = ON")
    await database.execute("PRAGMA journal_mode = WAL")
