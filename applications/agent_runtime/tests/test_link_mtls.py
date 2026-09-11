# coding: utf-8
"""mTLS 默认兼容、绑定元数据与本地身份落库测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_runtime.link_binding_state import (
    LINK_BINDING_STATE_TABLE,
    LOCAL_SERVICE_ROLE,
    sync_local_link_binding_state,
)
from agent_runtime.link_mtls import (
    MTLSDeploymentIdentity,
    LinkMTLSConfig,
    LinkMTLSError,
    LinkMTLSMode,
)


class _DB:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def get(self, table: str, filters: dict):
        assert table == LINK_BINDING_STATE_TABLE
        row = self.rows.get(filters["service_role"])
        return SimpleNamespace(**row) if row else None

    async def create(self, table: str, data: dict):
        assert table == LINK_BINDING_STATE_TABLE
        self.rows[data["service_role"]] = dict(data)
        return SimpleNamespace(**data)

    async def update(self, table: str, filters: dict, data: dict):
        assert table == LINK_BINDING_STATE_TABLE
        self.rows[filters["service_role"]].update(data)
        return SimpleNamespace(**self.rows[filters["service_role"]])


def _clear_link_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "JIUWENSWARM_LINK_MTLS_MODE",
        "JIUWENSWARM_LINK_MTLS_PROFILE",
        "JIUWENSWARM_LINK_MTLS_CA_FILE",
        "JIUWENSWARM_LINK_MTLS_CERT_FILE",
        "JIUWENSWARM_LINK_MTLS_KEY_FILE",
        "AGENT_RUNTIME_LINK_MTLS_AGENTSERVER_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_off_keeps_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_link_env(monkeypatch)
    config = LinkMTLSConfig.from_env()
    assert config.mode is LinkMTLSMode.OFF
    assert config.uvicorn_ssl_kwargs() == {}
    assert (
        config.agentserver_url(
            pod_id="pod-1",
            namespace="ns-a",
            port=8080,
            path="/sse",
            pod_ip="10.0.0.8",
        )
        == "http://10.0.0.8:8080/sse"
    )
    assert config.agentserver_env() == {}
    assert config.route_metadata() == {}


def test_enforce_requires_pinned_profile_even_with_external_certificate_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _clear_link_env(monkeypatch)
    monkeypatch.setenv("JIUWENSWARM_LINK_MTLS_MODE", "enforce")
    for name in (
        "JIUWENSWARM_LINK_MTLS_CA_FILE",
        "JIUWENSWARM_LINK_MTLS_CERT_FILE",
        "JIUWENSWARM_LINK_MTLS_KEY_FILE",
    ):
        path = tmp_path / name
        path.write_text("placeholder", encoding="utf-8")
        monkeypatch.setenv(name, str(path))
    with pytest.raises(LinkMTLSError, match="not installed"):
        LinkMTLSConfig.from_env()


def test_binding_metadata_and_https_dns() -> None:
    config = LinkMTLSConfig(
        mode=LinkMTLSMode.ENFORCE,
        identity=MTLSDeploymentIdentity("deployment-1", "binding-1", 3),
        agentserver_secret="tls-secret",
    )
    assert config.route_metadata() == {
        "mtls_deployment_id": "deployment-1",
        "mtls_binding_id": "binding-1",
        "mtls_binding_epoch": 3,
    }
    assert config.agentserver_url(
        pod_id="agentserver-abc",
        namespace="tenant-a",
        port=8080,
        path="/sse",
        pod_ip="10.0.0.8",
    ) == (
        "https://agentserver-abc.jiuwenclaw-agentserver.tenant-a.svc.cluster.local:8080/sse"
    )


@pytest.mark.asyncio
async def test_link_binding_state_upsert_excludes_private_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    cert = tmp_path / "tls.crt"
    ca = tmp_path / "ca.crt"
    cert.write_text("public certificate", encoding="utf-8")
    ca.write_text("public ca", encoding="utf-8")
    config = LinkMTLSConfig(
        mode=LinkMTLSMode.ENFORCE,
        ca_file=str(ca),
        cert_file=str(cert),
        key_file="/run/secrets/runtime/tls.key",
        identity=MTLSDeploymentIdentity("deployment-1", "binding-1", 1),
        agentserver_secret="agentserver-tls",
    )
    monkeypatch.setattr(LinkMTLSConfig, "cert_fingerprint", lambda _self: "fp-1")
    db = _DB()

    await sync_local_link_binding_state(db, config)
    row = db.rows[LOCAL_SERVICE_ROLE]
    assert row["private_key_ref"] == "/run/secrets/runtime/tls.key"
    assert "private_key" not in row
    assert row["local_cert_pem"] == "public certificate"
    assert row["peer_trust_bundle_pem"] == "public ca"

    updated = LinkMTLSConfig(
        **{
            **config.__dict__,
            "identity": MTLSDeploymentIdentity("deployment-1", "binding-1", 2),
        }
    )
    await sync_local_link_binding_state(db, updated)
    assert db.rows[LOCAL_SERVICE_ROLE]["mtls_binding_epoch"] == 2
    assert len(db.rows) == 1
    with pytest.raises(LinkMTLSError, match="older than persisted"):
        await sync_local_link_binding_state(db, config)
    assert db.rows[LOCAL_SERVICE_ROLE]["mtls_binding_epoch"] == 2
    db.rows[LOCAL_SERVICE_ROLE]["status"] = "revoked"
    with pytest.raises(LinkMTLSError, match="newer binding epoch"):
        await sync_local_link_binding_state(db, updated)
    assert db.rows[LOCAL_SERVICE_ROLE]["status"] == "revoked"


@pytest.mark.asyncio
async def test_off_does_not_write_link_binding_state() -> None:
    db = _DB()
    assert (
        await sync_local_link_binding_state(db, LinkMTLSConfig(LinkMTLSMode.OFF))
        is None
    )
    assert db.rows == {}
