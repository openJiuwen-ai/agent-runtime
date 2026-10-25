"""Explicit connection assembly for reusable federation providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from openjiuwen_runtime.service import FederationConnection, FederationProvider
from pydantic import BaseModel, ConfigDict

from identity_center.infrastructure.config import Settings

from .oidc.config import OidcConnectionConfig
from .oidc.verifier import OidcTokenVerifier
from .oidc_provider import OidcFederationProvider
from .provider import DemoFederationProvider


class ConnectionFile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    connections: list[OidcConnectionConfig]


def load_oidc_connections(settings: Settings) -> list[OidcConnectionConfig]:
    if settings.federation_config_file:
        config = ConnectionFile.model_validate_json(
            Path(settings.federation_config_file).read_text(encoding="utf-8")
        )
        ids = [c.connection_id for c in config.connections]
        if len(ids) != len(set(ids)) or "enterprise-demo" in ids:
            raise ValueError(
                "federation connection IDs must be unique and non-reserved"
            )
        return config.connections
    return (
        [OidcConnectionConfig.from_settings(settings)]
        if settings.federation_oidc_enabled
        else []
    )


@dataclass(frozen=True)
class FederationRoleMapping:
    claim_name: str
    claim_value: str
    local_role: str


@dataclass(frozen=True)
class ConfiguredFederation:
    connection: FederationConnection
    provider_type: str
    provider: FederationProvider | None
    verifier: OidcTokenVerifier | None = None
    options: OidcConnectionConfig | None = None
    flow: str = "server_code"
    role_mappings: tuple[FederationRoleMapping, ...] = ()


def _configured_federations(
    settings: Settings, handler: SQLAlchemyHandler
) -> tuple[ConfiguredFederation, ...]:
    registrations = []
    for options in load_oidc_connections(settings):
        provider = (
            OidcFederationProvider(handler, options)
            if options.flow == "server_code"
            else None
        )
        verifier = provider or OidcTokenVerifier(options)
        registrations.append(
            ConfiguredFederation(
                connection=FederationConnection(
                    connection_id=options.connection_id,
                    issuer=options.issuer,
                    organization_id=options.organization_id,
                    organization_name=options.name,
                    default_role="member",
                ),
                provider_type="oidc",
                provider=provider,
                verifier=verifier,
                options=options,
                flow=options.flow,
            )
        )
    if not settings.federation_demo_enabled:
        return tuple(registrations)
    connection = FederationConnection(
        connection_id="enterprise-demo",
        issuer="https://idp.enterprise-demo.example",
        organization_id="federated-enterprise-demo",
        organization_name="Enterprise Demo SSO",
        default_role="member",
    )
    admin_group = settings.federation_demo_admin_group.strip()
    role_mappings = (
        (
            FederationRoleMapping(
                claim_name="groups",
                claim_value=admin_group,
                local_role="admin",
            ),
        )
        if admin_group
        else ()
    )
    registrations.append(
        ConfiguredFederation(
            connection=connection,
            provider_type="demo",
            provider=DemoFederationProvider(settings.federation_public_path_prefix),
            role_mappings=role_mappings,
        )
    )
    if len({item.connection.connection_id for item in registrations}) != len(
        registrations
    ):
        raise ValueError("federation connection IDs must be unique")
    return tuple(registrations)
