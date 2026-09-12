"""Manager certificate identity stays independent from managed instance IDs."""

from types import SimpleNamespace

import pytest
from openjiuwen_runtime.foundation.security.link_certificate_bundle import (
    issue_bundle,
    materialize_role,
)
from openjiuwen_runtime.foundation.security.link_profile import cert_fingerprint

from manager_server.core.instance.link_binding_service import (
    InstanceLinkBindingService,
)
from manager_server.schemas.link_binding_schemas import LinkBindingCreateBody
from manager_server.security.link_mtls import ManagerLinkMTLSConfig, ManagerLinkMTLSError


class _DB:
    def __init__(self) -> None:
        self.tables = {
            "instance_info": {
                "manager-row-a": {
                    "jiuwenclaw_id": "manager-row-a",
                    "gateway_config_host": "http://gateway:8775",
                    "runtime_config_host": "http://runtime:8091",
                },
                "manager-row-b": {
                    "jiuwenclaw_id": "manager-row-b",
                    "gateway_config_host": "https://gateway-b.example:8775",
                    "runtime_config_host": "https://runtime-b.example:8091",
                },
            },
            "instance_link_binding": {},
        }

    async def get(self, table, filters):
        for row in self.tables[table].values():
            if all(row.get(key) == value for key, value in filters.items()):
                return SimpleNamespace(**row)
        return None

    async def create(self, table, data):
        row = dict(data)
        self.tables[table][row["jiuwenclaw_id"]] = row
        return SimpleNamespace(**row)

    async def update(self, table, filters, data):
        row = await self.get(table, filters)
        if row is None:
            return None
        stored = self.tables[table][row.jiuwenclaw_id]
        stored.update(data)
        return SimpleNamespace(**stored)


@pytest.fixture
def manager(monkeypatch, tmp_path):
    from openjiuwen_runtime.foundation.security import link_profile as profiles

    for name in (
        "JIUWENSWARM_LINK_MTLS_CA_FILE",
        "JIUWENSWARM_LINK_MTLS_CERT_FILE",
        "JIUWENSWARM_LINK_MTLS_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    bundle = issue_bundle(
        mtls_deployment_id="deployment-bootstrap-id",
        endpoints={"gateway": "gateway:8775", "runtime": "runtime:8091"},
    )
    materialize_role(bundle, "manager", tmp_path / "manager")
    monkeypatch.setenv("JIUWENSWARM_LINK_MTLS_MODE", "enforce")
    monkeypatch.delenv("JIUWENSWARM_LINK_MTLS_PROFILE", raising=False)
    monkeypatch.setattr(profiles, "DEFAULT_IDENTITY_ROOT", tmp_path)
    return ManagerLinkMTLSConfig.from_env()


@pytest.mark.asyncio
async def test_manager_profile_is_adopted_without_changing_business_instance_id(manager):
    db = _DB()
    headers = await manager.binding_headers(db, "manager-row-a")
    assert headers == {
        "X-Jiuwenswarm-Mtls-Binding-Id": manager.profile.mtls_binding_id,
        "X-Jiuwenswarm-Mtls-Binding-Epoch": str(manager.profile.mtls_binding_epoch),
    }
    assert "manager-row-a" in db.tables["instance_link_binding"]
    assert manager.resolve_endpoint("http://runtime:8091", role="runtime") == "https://runtime:8091"
    with pytest.raises(ManagerLinkMTLSError, match="instance not found"):
        await manager.binding_headers(db, "somebody-else")


@pytest.mark.asyncio
async def test_one_manager_identity_selects_independent_instance_bindings(manager):
    db = _DB()
    await manager.binding_headers(db, "manager-row-a")
    await InstanceLinkBindingService(db).bind(
        "manager-row-b",
        LinkBindingCreateBody(
            mtls_binding_id="binding-b",
            mtls_binding_epoch=7,
            mtls_gateway_endpoint="gateway-b.example:8775",
            mtls_runtime_endpoint="runtime-b.example:8091",
            manager_cert_fingerprint=cert_fingerprint(manager.profile.cert_file),
            gateway_cert_fingerprint="b" * 64,
            runtime_cert_fingerprint="c" * 64,
            agentserver_cert_fingerprint="d" * 64,
            trust_bundle_ref="profile://ca",
            updated_by="admin",
        ),
    )

    first = await manager.target(
        db,
        "manager-row-a",
        role="gateway",
        endpoint="http://gateway:8775",
    )
    second = await manager.target(
        db,
        "manager-row-b",
        role="gateway",
        endpoint="https://gateway-b.example:8775",
    )

    assert first.headers == {
        "X-Jiuwenswarm-Mtls-Binding-Id": manager.profile.mtls_binding_id,
        "X-Jiuwenswarm-Mtls-Binding-Epoch": str(manager.profile.mtls_binding_epoch),
    }
    assert second.headers == {
        "X-Jiuwenswarm-Mtls-Binding-Id": "binding-b",
        "X-Jiuwenswarm-Mtls-Binding-Epoch": "7",
    }
    assert first.client_kwargs["transport"].expected_fingerprints == {
        manager.profile.current()["peers"]["gateway"][0]
    }
    assert second.client_kwargs["transport"].expected_fingerprints == {"b" * 64}


@pytest.mark.asyncio
async def test_database_unbind_does_not_revoke_manager_service_certificate(manager):
    db = _DB()
    await manager.binding_headers(db, "manager-row-a")
    binding, rotation_required = await InstanceLinkBindingService(db).unbind(
        "manager-row-a", updated_by="admin"
    )
    assert binding.status == "unbound"
    assert rotation_required is True
    assert manager.profile.current()["status"] == "active"
    with pytest.raises(ManagerLinkMTLSError, match="no active"):
        await manager.binding_headers(db, "manager-row-a")


@pytest.mark.asyncio
async def test_automatic_adoption_rejects_endpoint_paths(manager):
    db = _DB()
    db.tables["instance_info"]["manager-row-a"]["gateway_config_host"] = (
        "http://gateway:8775/unexpected"
    )
    with pytest.raises(ManagerLinkMTLSError, match="gateway_config_host does not match"):
        await manager.binding_headers(db, "manager-row-a")


@pytest.mark.asyncio
async def test_automatic_adoption_rejects_non_http_endpoint(manager):
    db = _DB()
    db.tables["instance_info"]["manager-row-a"]["gateway_config_host"] = "ftp://gateway:8775"
    with pytest.raises(ManagerLinkMTLSError, match="gateway_config_host does not match"):
        await manager.binding_headers(db, "manager-row-a")


@pytest.mark.asyncio
async def test_manager_target_rejects_non_management_role(manager):
    with pytest.raises(ManagerLinkMTLSError, match="gateway or runtime"):
        await manager.target(
            _DB(),
            "manager-row-a",
            role="agentserver",
            endpoint="https://agentserver:8766",
        )


@pytest.mark.asyncio
async def test_manager_target_must_match_the_bound_instance_endpoint(manager):
    db = _DB()
    await manager.binding_headers(db, "manager-row-a")
    with pytest.raises(ManagerLinkMTLSError, match="active mTLS link binding"):
        await manager.target(
            db,
            "manager-row-a",
            role="gateway",
            endpoint="https://different-gateway:8775",
        )
