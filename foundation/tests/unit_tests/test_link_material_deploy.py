# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import copy
import io
import json
import logging

import pytest
from sqlalchemy.exc import OperationalError


def test_worker_error_is_logged_without_private_parameters(monkeypatch, capsys):
    from openjiuwen_runtime.foundation.security import link_material_deploy as worker

    async def fail(message):
        raise ValueError("PRIVATE KEY and database-password must not be printed")

    monkeypatch.setattr(worker, "dispatch", fail)
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO("{}"))
    previous = logging.root.manager.disable
    try:
        assert worker.main() == 2
    finally:
        logging.disable(previous)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "mTLS deployment stopped: ValueError\n"


@pytest.mark.asyncio
async def test_dispatch_reports_only_fixed_database_stage_and_driver_code(
    monkeypatch, settings
):
    from openjiuwen_runtime.foundation.security import link_material_deploy as worker
    from openjiuwen_runtime.foundation.security.link_profile import LinkProfileError

    async def fail_connect(*args, **kwargs):
        raise OperationalError(
            "SELECT PRIVATE KEY",
            {"password": "database-password"},
            RuntimeError(1049, "unknown private database"),
        )

    monkeypatch.setattr(worker, "connect", fail_connect)
    config = {
        **settings,
        "GATEWAY_DB_NAME": "gateway_test",
        "RUNTIME_DB_NAME": "runtime_test",
    }
    with pytest.raises(LinkProfileError) as error:
        await worker.dispatch({"settings": config, "action": "ensure"})

    message = str(error.value)
    assert message == (
        "Gateway database initialization failed (OperationalError, database_code=1049)"
    )
    assert "PRIVATE KEY" not in message
    assert "database-password" not in message
    assert "unknown private database" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "off", "observe"])
async def test_private_worker_requires_explicit_enforce(monkeypatch, mode):
    from openjiuwen_runtime.foundation.security import link_material_deploy as worker
    from openjiuwen_runtime.foundation.security.link_profile import LinkProfileError

    async def forbidden(*args, **kwargs):
        raise AssertionError("default mode must never open the certificate database")

    monkeypatch.setattr(worker, "connect", forbidden)
    config = {} if mode is None else {"JIUWENSWARM_LINK_MTLS_MODE": mode}
    with pytest.raises(LinkProfileError, match="explicit enforce"):
        await worker.dispatch({"settings": config, "action": "ensure"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("GATEWAY_DB_NAME", None),
        ("GATEWAY_DB_NAME", ""),
        ("RUNTIME_DB_NAME", None),
        ("RUNTIME_DB_NAME", "   "),
    ],
)
async def test_worker_rejects_unresolved_database_names_before_connect(
    monkeypatch, key, value
):
    from openjiuwen_runtime.foundation.security import link_material_deploy as worker
    from openjiuwen_runtime.foundation.security.link_profile import LinkProfileError

    async def forbidden(*args, **kwargs):
        raise AssertionError("an unresolved database name must not reach the database")

    monkeypatch.setattr(worker, "connect", forbidden)
    config = {
        "JIUWENSWARM_LINK_MTLS_MODE": "enforce",
        "GATEWAY_DB_NAME": "gateway_test",
        "RUNTIME_DB_NAME": "runtime_test",
    }
    config[key] = value
    with pytest.raises(LinkProfileError, match=key):
        await worker.dispatch({"settings": config, "action": "ensure"})


@pytest.fixture(name="settings")
def deployment_settings():
    return {
        "NAMESPACE": "cert-test",
        "GATEWAY_NAME": "jiuwenclaw-gateway",
        "AGENT_RUNTIME_NAME": "jiuwenclaw-agent-runtime",
        "GATEWAY_CONFIG_HTTP_PORT": "8775",
        "AGENT_RUNTIME_PORT": "8091",
        "AGENT_SERVER_NAME": "jiuwenclaw-agentserver",
        "JIUWENSWARM_LINK_MTLS_MODE": "enforce",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["mysql", "postgresql"])
async def test_real_image_worker_db_restore_protocol(settings, backend):
    """Optional real DB integration; actual worker code, not a claimed Docker run."""
    import os
    import uuid

    from openjiuwen_runtime.foundation.security import link_material_deploy as worker
    from openjiuwen_runtime.foundation.security.link_profile import LinkProfileError
    from sqlalchemy import MetaData
    from sqlalchemy.engine import make_url

    url = os.getenv("LINK_TEST_" + backend.upper() + "_URL")
    if not url:
        pytest.skip("dedicated real localhost DB not configured")
    parsed = make_url(url)
    assert parsed.host == "127.0.0.1" and parsed.database.startswith("link_mtls_test_")
    # Separate dedicated schemas/DBs from other concurrently running tests.
    name = "link_mtls_worker_" + uuid.uuid4().hex[:10]
    settings = {
        **settings,
        "DB_TYPE": backend,
        "DB_HOST": parsed.host,
        "DB_PORT": str(parsed.port),
        "GATEWAY_DB_NAME": name + "_gw",
        "RUNTIME_DB_NAME": name + "_rt",
        "GATEWAY_DB_USER": parsed.username,
        "RUNTIME_DB_USER": parsed.username,
        "GATEWAY_DB_PASSWORD": parsed.password or "",
        "RUNTIME_DB_PASSWORD": parsed.password or "",
    }
    try:
        result = await worker.dispatch(
            {"action": "ensure", "settings": settings, "existing": {}}
        )
        assert result["summary"]["reused"] is False and len(result["secrets"]) == 4
        existing = {
            s["metadata"]["labels"]["jiuwenswarm-link-role"]: s
            for s in result["secrets"]
        }
        restored = await worker.dispatch(
            {"action": "ensure", "settings": settings, "existing": existing}
        )
        assert (
            restored["summary"]["reused"] and restored["secrets"] == result["secrets"]
        )
        # Namespace Secret disappearance does not renew/reissue stored certificates.
        missing = await worker.dispatch(
            {"action": "ensure", "settings": settings, "existing": {}}
        )
        assert missing["secrets"] == result["secrets"]
        status = await worker.dispatch(
            {"action": "status", "settings": settings, "existing": existing}
        )
        assert "PRIVATE KEY" not in json.dumps(status)
        corrupted = copy.deepcopy(existing)
        corrupted["runtime"]["data"]["tls.key"] = "Y29uZmxpY3Q="
        with pytest.raises(LinkProfileError, match="conflicts with database"):
            await worker.dispatch(
                {"action": "ensure", "settings": settings, "existing": corrupted}
            )
    finally:
        for role in ("gateway", "runtime"):
            db = await worker.connect(settings, role)
            async with db.get_engine().begin() as connection:
                metadata = MetaData()
                await connection.run_sync(metadata.reflect)
                await connection.run_sync(metadata.drop_all)
            await db.disconnect()
