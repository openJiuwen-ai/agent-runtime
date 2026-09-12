# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Certificate persistence tests; optional real DBs must be dedicated localhost test DBs."""

import asyncio
import copy
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from openjiuwen_runtime.foundation.security.link_certificate_bundle import (
    issue_bundle,
    materialize_role,
    ten_year_expiry,
    validate_bundle,
)
from openjiuwen_runtime.foundation.security.link_material_store import (
    assert_managed_state,
    definition,
    ensure,
    migrate,
    public_values,
    sync_runtime,
)
from openjiuwen_runtime.foundation.security.link_profile import (
    LinkProfile,
    LinkProfileError,
)
from sqlalchemy import Column, MetaData, Table, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ENDPOINTS = {"gateway": "127.0.0.1:28775", "runtime": "127.0.0.1:28091"}
SANS = {"agentserver": ["*.agents.test.svc.cluster.local", "127.0.0.1"]}


@pytest_asyncio.fixture(
    name="engine",
    params=[
        "sqlite",
        *(["mysql"] if os.getenv("LINK_TEST_MYSQL_URL") else []),
        *(["postgresql"] if os.getenv("LINK_TEST_POSTGRESQL_URL") else []),
    ],
)
async def certificate_engine(request, tmp_path):
    url = (
        os.getenv(f"LINK_TEST_{request.param.upper()}_URL")
        or f"sqlite+aiosqlite:///{tmp_path}/certs.db"
    )
    if request.param != "sqlite":
        parsed = make_url(url)
        assert parsed.host == "127.0.0.1" and parsed.database.startswith(
            "link_mtls_test_"
        ), "dedicated local DB required"
    db = create_async_engine(url, hide_parameters=True)
    yield db
    # Only this explicitly dedicated fixture DB contains these test tables.
    async with db.begin() as conn:
        metadata = MetaData()
        await conn.run_sync(lambda c: metadata.reflect(c))
        await conn.run_sync(metadata.drop_all)
    await db.dispose()


def test_ten_calendar_years_and_no_renew_on_validation():
    now = datetime(2024, 2, 29, 12, 1, tzinfo=timezone.utc)
    assert ten_year_expiry(now) == datetime(2034, 2, 28, 12, 1, tzinfo=timezone.utc)
    bundle = issue_bundle(mtls_deployment_id="one", endpoints=ENDPOINTS, sans=SANS)
    before = copy.deepcopy(bundle)
    summary = validate_bundle(bundle)
    assert bundle == before
    assert set(summary["roles"]) == {"gateway", "runtime", "agentserver", "manager"}
    assert "PRIVATE KEY" not in json.dumps(summary)
    assert "ca.key" not in json.dumps(bundle)


@pytest.mark.asyncio
async def test_ensure_reuses_generated_mtls_deployment_id_after_dispose(engine):
    first, reused = await ensure(engine, endpoints=ENDPOINTS, sans=SANS)
    assert not reused
    await engine.dispose()
    again, reused = await ensure(engine, endpoints=ENDPOINTS, sans=SANS)
    assert reused and first == again
    async with engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT COUNT(*) FROM link_binding_state"))
        ).scalar() == 1


@pytest.mark.asyncio
async def test_concurrent_initialization_uses_one_winner(engine):
    await migrate(engine)  # DDL tested separately; contenders race the first INSERT.
    results = await asyncio.gather(
        *(ensure(engine, endpoints=ENDPOINTS) for _ in range(4))
    )
    assert sum(not reused for _, reused in results) == 1
    assert all(bundle == results[0][0] for bundle, _ in results)


@pytest.mark.asyncio
async def test_existing_secret_without_record_never_regenerates(engine):
    with pytest.raises(LinkProfileError, match="authoritative database binding"):
        await ensure(engine, endpoints=ENDPOINTS, existing_secret=True)


@pytest.mark.asyncio
async def test_reject_endpoint_and_san_changes(engine):
    await ensure(engine, endpoints=ENDPOINTS, sans=SANS)
    for kwargs in (
        {"endpoints": {"gateway": "changed:8775"}},
        {"endpoints": ENDPOINTS, "sans": {"runtime": ["new.example"]}},
    ):
        with pytest.raises(LinkProfileError):
            await ensure(engine, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["revoked", "version", "partial", "pending", "epoch"]
)
async def test_fail_closed_for_invalid_stored_state(engine, mutation):
    bundle, _ = await ensure(engine, endpoints=ENDPOINTS)
    table = await migrate(engine)
    envelope = {"current": bundle}
    values = {}
    if mutation == "revoked":
        values["status"] = "revoked"
    elif mutation == "version":
        values["material_schema_version"] = 99
    elif mutation == "epoch":
        values["material_epoch"] = 2
    elif mutation == "partial":
        del envelope["current"]["materials"]["runtime"]["tls.key"]
        values["certificate_materials"] = json.dumps(envelope)
    else:
        envelope["operation"] = {"type": "rotate", "phase": "prepared"}
        values["certificate_materials"] = json.dumps(envelope)
    async with engine.begin() as conn:
        await conn.execute(update(table).values(**values))
    with pytest.raises(LinkProfileError):
        await ensure(engine, endpoints=ENDPOINTS)


@pytest.mark.asyncio
async def test_incremental_schema_extension_preserves_other_tables_and_public_binding(
    engine,
):
    table = definition()
    # The public application projection may already exist before mTLS is enabled.
    omitted_columns = {
        "material_schema_version",
        "material_epoch",
        "certificate_materials",
    }
    public_columns = []
    for column in table.c:
        if column.name in omitted_columns:
            continue
        public_columns.append(
            Column(
                column.name,
                column.type,
                primary_key=column.primary_key,
                nullable=column.nullable,
                unique=column.unique,
            )
        )
    public = Table(
        "link_binding_state",
        MetaData(),
        *public_columns,
    )
    bundle = issue_bundle(mtls_deployment_id="existing", endpoints=ENDPOINTS)
    values = public_values(bundle, "gateway")
    values = {k: v for k, v in values.items() if k in public.c}
    async with engine.begin() as conn:
        await conn.run_sync(public.create)
        await conn.execute(
            public.insert().values(**values, created_at=values["updated_at"])
        )
        await conn.execute(
            text(
                "CREATE TABLE gateway_sign_keypair (id INTEGER PRIMARY KEY, marker VARCHAR(32))"
            )
        )
        await conn.execute(
            text("INSERT INTO gateway_sign_keypair VALUES (1, 'unrelated-untouched')")
        )
    await migrate(engine)
    await migrate(engine)
    with pytest.raises(LinkProfileError, match="no recoverable bundle"):
        await ensure(engine, endpoints=ENDPOINTS)
    async with engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT marker FROM gateway_sign_keypair"))
        ).scalar() == "unrelated-untouched"
        assert (
            await conn.execute(text("SELECT mtls_binding_id FROM link_binding_state"))
        ).scalar() == bundle["mtls_binding_id"]


@pytest.mark.asyncio
async def test_runtime_persists_only_public_state(engine):
    bundle = issue_bundle(mtls_deployment_id="one", endpoints=ENDPOINTS)
    await sync_runtime(engine, bundle, secret_name="agentserver-tls")
    await sync_runtime(engine, bundle, secret_name="agentserver-tls")
    table = await migrate(engine)
    async with engine.connect() as conn:
        row = (await conn.execute(select(table))).mappings().one()
    assert row["certificate_materials"] is None
    assert row["material_epoch"] == bundle["mtls_binding_epoch"]
    with pytest.raises(LinkProfileError, match="Runtime binding conflicts"):
        await sync_runtime(
            engine,
            issue_bundle(mtls_deployment_id="other", endpoints=ENDPOINTS),
            secret_name="other",
        )


@pytest.mark.asyncio
async def test_managed_startup_does_not_accept_higher_profile_epoch_or_leak_key(
    engine, tmp_path
):
    from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
    from sqlalchemy import event

    bundle, _ = await ensure(engine, endpoints=ENDPOINTS)
    db = SQLAlchemyHandler(str(engine.url))
    db.engine = engine
    statements = []

    def capture(statement, **kwargs):
        statements.append(statement.lower())

    event.listen(engine.sync_engine, "before_cursor_execute", capture, named=True)
    summary = validate_bundle(bundle)
    identity = SimpleNamespace(
        mtls_deployment_id=bundle["mtls_deployment_id"],
        mtls_binding_id=bundle["mtls_binding_id"],
        mtls_binding_epoch=1,
    )
    fp = summary["roles"]["gateway"]["fingerprint"]
    assert await assert_managed_state(db, "gateway", identity, fp, required=True)
    assert not any(
        "certificate_materials" in s for s in statements if s.startswith("select")
    )
    identity.mtls_binding_epoch = 2
    with pytest.raises(LinkProfileError, match="database-authorized"):
        await assert_managed_state(db, "gateway", identity, fp, required=True)
    event.remove(engine.sync_engine, "before_cursor_execute", capture)
    path = materialize_role(bundle, "gateway", tmp_path / "gateway")
    assert LinkProfile.load(str(path)).mtls_binding_epoch == 1


def test_wrong_key_expiry_and_role_pins_are_rejected():
    bundle = issue_bundle(mtls_deployment_id="one", endpoints=ENDPOINTS)
    bad = copy.deepcopy(bundle)
    bad["materials"]["runtime"]["tls.key"] = bad["materials"]["gateway"]["tls.key"]
    with pytest.raises(LinkProfileError, match="do not match"):
        validate_bundle(bad)
    with pytest.raises(LinkProfileError, match="not currently valid"):
        validate_bundle(bundle, now=datetime(2040, 1, 1, tzinfo=timezone.utc))
