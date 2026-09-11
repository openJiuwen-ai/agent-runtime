"""实例链路绑定的唯一性、幂等与解绑轮换测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from manager_server.core.instance.link_binding_service import (
    InstanceLinkBindingService,
    LinkBindingConflict,
)
from manager_server.schemas.link_binding_schemas import LinkBindingCreateBody
from manager_server.security.link_mtls import (
    ManagerLinkMTLSConfig,
    ManagerLinkMTLSError,
    ManagerLinkMTLSMode,
)


class _DB:
    def __init__(self) -> None:
        self.tables = {
            "instance_info": {
                "jid-a": {
                    "jiuwenclaw_id": "jid-a",
                    "gateway_config_host": "http://gateway-a:8775",
                    "runtime_config_host": "http://runtime-a:8091",
                },
                "jid-b": {
                    "jiuwenclaw_id": "jid-b",
                    "gateway_config_host": "http://gateway-a:8775",
                    "runtime_config_host": "http://runtime-b:8091",
                },
                "jid-c": {
                    "jiuwenclaw_id": "jid-c",
                    "gateway_config_host": "http://gateway-c:8775",
                    "runtime_config_host": "http://runtime-a:8091",
                },
            },
            "instance_link_binding": {},
        }

    async def get(self, table: str, filters: dict):
        for row in self.tables[table].values():
            if all(row.get(key) == value for key, value in filters.items()):
                return SimpleNamespace(**row)
        return None

    async def create(self, table: str, data: dict):
        row = dict(data)
        self.tables[table][row.get("jiuwenclaw_id")] = row
        return SimpleNamespace(**row)

    async def update(self, table: str, filters: dict, data: dict):
        current = await self.get(table, filters)
        if current is None:
            return None
        row = self.tables[table][current.jiuwenclaw_id]
        row.update(data)
        return SimpleNamespace(**row)


class _LostUpdateDB(_DB):
    def __init__(self) -> None:
        super().__init__()
        self.drop_next_guarded_update = False

    async def update(self, table: str, filters: dict, data: dict):
        if self.drop_next_guarded_update and "mtls_binding_epoch" in filters:
            self.drop_next_guarded_update = False
            return None
        return await super().update(table, filters, data)


def _body(
    gateway: str = "gateway-a:8775",
    runtime: str = "runtime-a:8091",
    *,
    mtls_binding_id: str = "binding-a",
    mtls_binding_epoch: int = 1,
):
    return LinkBindingCreateBody(
        mtls_binding_id=mtls_binding_id,
        mtls_binding_epoch=mtls_binding_epoch,
        mtls_gateway_endpoint=gateway,
        mtls_runtime_endpoint=runtime,
        manager_cert_fingerprint="a" * 64,
        gateway_cert_fingerprint="b" * 64,
        runtime_cert_fingerprint="c" * 64,
        agentserver_cert_fingerprint="d" * 64,
        trust_bundle_ref="secret://namespace/link-ca",
        updated_by="admin",
    )


@pytest.mark.asyncio
async def test_bind_is_idempotent_and_conflicting_rebind_is_rejected() -> None:
    service = InstanceLinkBindingService(_DB())
    first = await service.bind("jid-a", _body())
    replay = await service.bind("jid-a", _body())
    assert replay.mtls_binding_id == first.mtls_binding_id
    assert replay.mtls_binding_epoch == 1

    with pytest.raises(LinkBindingConflict, match="already has"):
        await service.bind("jid-a", _body(mtls_binding_id="binding-new"))


@pytest.mark.asyncio
async def test_active_gateway_or_runtime_cannot_bind_another_instance() -> None:
    service = InstanceLinkBindingService(_DB())
    await service.bind("jid-a", _body())

    with pytest.raises(LinkBindingConflict, match="gateway"):
        await service.bind("jid-b", _body(runtime="runtime-b:8091"))
    with pytest.raises(LinkBindingConflict, match="runtime"):
        await service.bind("jid-c", _body(gateway="gateway-c:8775"))


@pytest.mark.asyncio
async def test_unbind_revokes_epoch_and_releases_active_keys() -> None:
    service = InstanceLinkBindingService(_DB())
    first = await service.bind("jid-a", _body())
    unbound, rotation_required = await service.unbind("jid-a", updated_by="admin")
    assert rotation_required is True
    assert unbound.status == "unbound"
    assert unbound.mtls_binding_epoch == first.mtls_binding_epoch + 1

    other = await service.bind(
        "jid-b",
        _body(runtime="runtime-b:8091", mtls_binding_id="binding-b"),
    )
    assert other.status == "bound"
    assert other.mtls_binding_epoch == 1

    db = service.handler
    db.tables["instance_info"]["jid-a"].update(
        gateway_config_host="http://gateway-new:8775",
        runtime_config_host="http://runtime-new:8091",
    )
    rebound = await service.bind(
        "jid-a",
        _body(
            gateway="gateway-new:8775",
            runtime="runtime-new:8091",
            mtls_binding_id="binding-new",
            mtls_binding_epoch=unbound.mtls_binding_epoch + 1,
        ),
    )
    assert rebound.mtls_binding_id != first.mtls_binding_id
    assert rebound.mtls_binding_epoch == unbound.mtls_binding_epoch + 1


@pytest.mark.asyncio
async def test_repeated_unbind_is_idempotent() -> None:
    service = InstanceLinkBindingService(_DB())
    await service.bind("jid-a", _body())
    first, first_rotation = await service.unbind("jid-a", updated_by="admin")
    replay, replay_rotation = await service.unbind("jid-a", updated_by="admin")
    assert first_rotation is True
    assert replay_rotation is False
    assert replay.mtls_binding_epoch == first.mtls_binding_epoch


@pytest.mark.asyncio
async def test_rebind_uses_optimistic_epoch_guard() -> None:
    db = _LostUpdateDB()
    service = InstanceLinkBindingService(db)
    await service.bind("jid-a", _body())
    await service.unbind("jid-a", updated_by="admin")
    db.tables["instance_info"]["jid-a"].update(
        gateway_config_host="http://gateway-new:8775",
        runtime_config_host="http://runtime-new:8091",
    )
    db.drop_next_guarded_update = True

    with pytest.raises(LinkBindingConflict, match="changed concurrently"):
        await service.bind(
            "jid-a",
            _body(
                gateway="gateway-new:8775",
                runtime="runtime-new:8091",
                mtls_binding_id="binding-new",
                mtls_binding_epoch=3,
            ),
        )


@pytest.mark.asyncio
async def test_unknown_instance_is_rejected() -> None:
    service = InstanceLinkBindingService(_DB())
    with pytest.raises(LookupError, match="instance not found"):
        await service.bind("missing", _body())


@pytest.mark.asyncio
async def test_binding_endpoint_must_match_the_manager_instance() -> None:
    service = InstanceLinkBindingService(_DB())
    with pytest.raises(LinkBindingConflict, match="gateway_config_host"):
        await service.bind("jid-a", _body(gateway="other-gateway:8775"))


@pytest.mark.asyncio
async def test_binding_endpoint_is_canonical_and_scheme_independent() -> None:
    service = InstanceLinkBindingService(_DB())
    first = await service.bind(
        "jid-a",
        _body(
            gateway="https://gateway-a:8775",
            runtime="https://runtime-a:8091",
        ),
    )
    replay = await service.bind("jid-a", _body())
    assert first.mtls_gateway_endpoint == "gateway-a:8775"
    assert first.mtls_runtime_endpoint == "runtime-a:8091"
    assert replay.mtls_binding_id == first.mtls_binding_id


@pytest.mark.asyncio
async def test_manager_enforce_requires_service_identity() -> None:
    db = _DB()
    service = InstanceLinkBindingService(db)
    bound = await service.bind("jid-a", _body())
    config = ManagerLinkMTLSConfig(mode=ManagerLinkMTLSMode.ENFORCE)

    assert bound.mtls_binding_id == "binding-a"
    with pytest.raises(ManagerLinkMTLSError, match="pinned manager profile"):
        await config.binding_headers(db, "jid-a")


def test_manager_enforce_rejects_plain_http() -> None:
    config = ManagerLinkMTLSConfig(mode=ManagerLinkMTLSMode.ENFORCE)
    with pytest.raises(ManagerLinkMTLSError, match="must use https"):
        config.require_secure_url("http://runtime:8091", label="Runtime")


def test_binding_body_normalizes_fingerprints_and_rejects_blank_ids() -> None:
    body = _body()
    normalized = LinkBindingCreateBody(
        **{
            **body.model_dump(),
            "mtls_binding_id": "  binding-a  ",
            "manager_cert_fingerprint": "A" * 64,
        }
    )
    assert normalized.mtls_binding_id == "binding-a"
    assert normalized.manager_cert_fingerprint == "a" * 64

    with pytest.raises(ValidationError, match="must not be blank"):
        LinkBindingCreateBody(**{**body.model_dump(), "mtls_binding_id": "   "})
