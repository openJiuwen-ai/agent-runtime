# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deployment-only material access in the existing link_binding_state table.

Application ORM models intentionally omit certificate_materials. SQL access to
this module requires the deployment credential. No database URL or material is
included in exceptions, reprs, API projections or diagnostics.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.dialects.mysql import LONGTEXT, MEDIUMTEXT
from sqlalchemy.exc import IntegrityError

from .link_certificate_bundle import issue_bundle, validate_bundle
from .link_profile import LinkProfileError

TABLE_NAME = "link_binding_state"
MATERIAL_COLUMNS = {
    "material_schema_version": Integer(),
    "material_epoch": Integer(),
    "certificate_materials": Text().with_variant(LONGTEXT(), "mysql"),
}


def assert_binding_state_compatible(row, identity, cert_fingerprint):
    """Reject stale, revoked, or silently replaced current binding state."""
    if row is None:
        return
    previous_epoch = int(row.mtls_binding_epoch or 0)
    if identity.mtls_binding_epoch < previous_epoch:
        raise LinkProfileError(
            "deployment profile epoch is older than persisted mTLS binding state"
        )
    changed = row.status != "active" or (
        row.mtls_deployment_id,
        row.mtls_binding_id,
        row.local_cert_fingerprint,
    ) != (
        identity.mtls_deployment_id,
        identity.mtls_binding_id,
        cert_fingerprint,
    )
    if identity.mtls_binding_epoch == previous_epoch and changed:
        raise LinkProfileError(
            "changed or revoked mTLS binding state requires a newer binding epoch"
        )


def definition() -> Table:
    """Full deployment projection; deliberately not registered as a business ORM."""
    return Table(
        TABLE_NAME,
        MetaData(),
        Column("service_role", String(32), primary_key=True),
        Column("mtls_deployment_id", String(64), nullable=False, unique=True),
        Column("mtls_binding_id", String(64), nullable=False, unique=True),
        Column("protocol_version", String(32), nullable=False),
        Column("mtls_binding_epoch", Integer, nullable=False),
        Column("local_cert_pem", Text, nullable=False),
        Column("local_cert_fingerprint", String(128), nullable=False),
        Column("private_key_ref", String(512), nullable=False),
        Column("peer_trust_bundle_pem", Text, nullable=False),
        Column("agentserver_secret_ref", String(253), nullable=True),
        Column("status", String(32), nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
        *(Column(name, kind, nullable=True) for name, kind in MATERIAL_COLUMNS.items()),
    )


async def migrate(engine) -> Table:
    """Add nullable columns to OUR table only; never globally migrate business tables.

    DDL is a separate phase (MySQL implicitly commits). Concurrent DDL errors are
    accepted only when a fresh inspection proves the desired compatible schema.
    """
    table = definition()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda c: table.create(c, checkfirst=True))
    except Exception:
        async with engine.connect() as connection:
            exists = await connection.run_sync(
                lambda c: inspect(c).has_table(TABLE_NAME)
            )
        if not exists:
            raise LinkProfileError("mTLS table initialization failed") from None
    for name, kind in MATERIAL_COLUMNS.items():
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda c: inspect(c).get_columns(TABLE_NAME)
            )
        existing = {c["name"]: c for c in columns}
        if name not in existing:
            sql_type = kind.compile(dialect=engine.dialect)
            # Identifiers/types are code constants, never customer SQL input.
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            f"ALTER TABLE {TABLE_NAME} ADD COLUMN {name} {sql_type} NULL"
                        )
                    )
            except Exception:
                async with engine.connect() as connection:
                    check = await connection.run_sync(
                        lambda c: inspect(c).get_columns(TABLE_NAME)
                    )
                if name not in {c["name"] for c in check}:
                    raise LinkProfileError(
                        "mTLS schema migration failed; database unchanged or needs retry"
                    ) from None
    async with engine.connect() as connection:
        actual = await connection.run_sync(
            lambda c: Table(TABLE_NAME, MetaData(), autoload_with=c)
        )
    if not (set(table.c.keys()) - {"agentserver_secret_ref"}).issubset(actual.c.keys()):
        raise LinkProfileError(
            "incompatible existing mTLS schema; explicit migration required"
        )
    if not isinstance(
        actual.c.certificate_materials.type, (Text, LONGTEXT, MEDIUMTEXT)
    ):
        raise LinkProfileError(
            "certificate_materials requires TEXT capacity; refusing incompatible schema"
        )
    if not all(
        isinstance(actual.c[n].type, Integer)
        for n in ("material_epoch", "material_schema_version")
    ):
        raise LinkProfileError("incompatible certificate version columns")
    return actual


def public_values(bundle: dict, role: str) -> dict:
    summary = validate_bundle(bundle)
    files = bundle["materials"][role]
    return {
        "service_role": role,
        "mtls_deployment_id": bundle["mtls_deployment_id"],
        "mtls_binding_id": bundle["mtls_binding_id"],
        "protocol_version": "0.0.1",
        "mtls_binding_epoch": bundle["mtls_binding_epoch"],
        "local_cert_pem": files["tls.crt"],
        "local_cert_fingerprint": summary["roles"][role]["fingerprint"],
        "private_key_ref": f"/etc/jiuwenswarm/link-mtls/{role}/tls.key",
        "peer_trust_bundle_pem": files["ca.crt"],
        "status": "active",
        "material_schema_version": 1,
        "material_epoch": bundle["mtls_binding_epoch"],
        "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }


def _read_bundle(row, *, endpoints, sans):
    if row["status"] != "active":
        raise LinkProfileError(
            "persisted binding is revoked; ordinary deploy cannot reactivate it"
        )
    if row["material_schema_version"] != 1 or not row["certificate_materials"]:
        raise LinkProfileError(
            "existing mTLS binding has no recoverable bundle; restore the authoritative database state"
        )
    try:
        envelope = json.loads(row["certificate_materials"])
        if envelope.get("operation"):
            raise LinkProfileError(
                "unfinished certificate operation; use explicit resume"
            )
        bundle = envelope["current"]
    except LinkProfileError:
        raise
    except Exception:
        raise LinkProfileError("invalid persisted certificate envelope") from None
    summary = validate_bundle(bundle, endpoints=endpoints, sans=sans)
    expected_binding = {
        "mtls_binding_epoch": bundle["mtls_binding_epoch"],
        "material_epoch": bundle["mtls_binding_epoch"],
        "mtls_binding_id": bundle["mtls_binding_id"],
        "mtls_deployment_id": bundle["mtls_deployment_id"],
        "local_cert_fingerprint": summary["roles"]["gateway"]["fingerprint"],
    }
    if any(row.get(key) != value for key, value in expected_binding.items()):
        raise LinkProfileError("persisted binding and certificate version disagree")
    return bundle


async def ensure(
    engine,
    *,
    endpoints: dict,
    sans: dict | None = None,
    existing_secret: bool = False,
) -> tuple[dict, bool]:
    """Commit first, then return material for Secret installation. Never rotate."""
    table = await migrate(engine)
    async with engine.connect() as connection:
        rows = (await connection.execute(select(table))).mappings().all()
    if rows:
        if len(rows) != 1 or rows[0]["service_role"] != "gateway":
            raise LinkProfileError(
                "database does not describe exactly one logical Gateway binding"
            )
        return _read_bundle(rows[0], endpoints=endpoints, sans=sans), True
    if existing_secret:
        raise LinkProfileError(
            "existing Secret has no authoritative database binding; refusing replacement"
        )
    bundle = issue_bundle(
        mtls_deployment_id=str(uuid.uuid4()),
        endpoints=endpoints,
        sans=sans,
    )
    values = public_values(bundle, "gateway")
    values.update(
        created_at=values["updated_at"],
        certificate_materials=json.dumps({"current": bundle}, sort_keys=True),
    )
    if "agentserver_secret_ref" in table.c:
        values["agentserver_secret_ref"] = ""
    try:
        async with engine.begin() as connection:
            await connection.execute(table.insert().values(**values))
    except IntegrityError:
        # The losing installer discards its random candidate and reads the winner.
        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(table).where(table.c.service_role == "gateway")
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise LinkProfileError(
                "concurrent binding initialization conflict; retry"
            ) from None
        return _read_bundle(row, endpoints=endpoints, sans=sans), True
    return bundle, False


async def sync_runtime(engine, bundle: dict, *, secret_name: str) -> None:
    """Deployment-owned public state only. A mismatch is NOT authorization to rotate."""
    table = await migrate(engine)
    values = public_values(bundle, "runtime")
    if "agentserver_secret_ref" in table.c:
        values["agentserver_secret_ref"] = secret_name
    async with engine.begin() as connection:
        row = (
            (
                await connection.execute(
                    select(table)
                    .where(table.c.service_role == "runtime")
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row:
            for key in (
                "mtls_deployment_id",
                "mtls_binding_id",
                "mtls_binding_epoch",
                "local_cert_fingerprint",
                "status",
            ):
                if row[key] != values[key]:
                    raise LinkProfileError(
                        "Runtime binding conflicts; explicit coordinated operation required"
                    )
            if row["certificate_materials"] is not None:
                raise LinkProfileError(
                    "Runtime database must not contain all-role private material"
                )
            await connection.execute(
                update(table).where(table.c.service_role == "runtime").values(**values)
            )
        else:
            await connection.execute(
                table.insert().values(**values, created_at=values["updated_at"])
            )


async def assert_managed_state(
    db, service_role, identity, fingerprint, *, required=False
) -> bool:
    """Startup gate using ONLY public columns, never selecting private material.

    Managed records can only advance through deployment operations.
    """
    from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler

    if not isinstance(db, SQLAlchemyHandler):
        if required:
            raise LinkProfileError(
                "database-managed profile requires a supported persistent database"
            )
        return False
    engine = db.get_engine()
    if engine is None:
        raise LinkProfileError("database must be connected before loading binding")
    async with engine.connect() as connection:
        columns = await connection.run_sync(
            lambda c: {x["name"] for x in inspect(c).get_columns(TABLE_NAME)}
        )
        if "material_epoch" not in columns:
            if required:
                raise LinkProfileError(
                    "database-managed profile has no authorized database state"
                )
            return False
        table = definition()
        names = (
            "mtls_deployment_id",
            "mtls_binding_id",
            "mtls_binding_epoch",
            "material_epoch",
            "material_schema_version",
            "local_cert_fingerprint",
            "status",
        )
        row = (
            (
                await connection.execute(
                    select(*(table.c[n] for n in names)).where(
                        table.c.service_role == service_role
                    )
                )
            )
            .mappings()
            .first()
        )
    if row is None or row["material_epoch"] is None:
        if required:
            raise LinkProfileError(
                "database-managed profile has no authorized database state"
            )
        return False
    expected = {
        "mtls_deployment_id": identity.mtls_deployment_id,
        "mtls_binding_id": identity.mtls_binding_id,
        "mtls_binding_epoch": identity.mtls_binding_epoch,
        "material_epoch": identity.mtls_binding_epoch,
        "material_schema_version": 1,
        "local_cert_fingerprint": fingerprint,
        "status": "active",
    }
    if any(row[k] != v for k, v in expected.items()):
        raise LinkProfileError(
            "profile is not the database-authorized binding; service cannot advance or reactivate it"
        )
    return True
