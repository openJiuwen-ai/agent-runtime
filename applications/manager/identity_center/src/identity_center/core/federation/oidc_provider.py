"""Backend authorization-code provider using the shared OIDC verifier."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode, urlsplit

import jwt
from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from openjiuwen_runtime.service import (
    FederationAuthenticationResult,
    FederationConnection,
    FederationError,
    FederationProvider,
    SystemContext,
)
from sqlalchemy import select, update

from identity_center.infrastructure.config import Settings
from identity_center.models.identity_models import FEDERATION_LOGIN_STATE_TABLE_DEF

from .contracts import FederationTokenResult as OidcAuthenticationResult
from .oidc.config import OidcConnectionConfig
from .oidc.verifier import OidcTokenVerifier, _safe_url


class ValidatedOidcCallback(dict[str, str]):
    """Protocol parameters plus server-owned state, after atomic state consumption."""

    def __init__(self, parameters: Mapping[str, str], data: dict[str, Any]):
        super().__init__(parameters)
        self.nonce = data.get("oidc_nonce")
        self.verifier = data.get("oidc_verifier")
        invalid_nonce = not isinstance(self.nonce, str) or not self.nonce
        if (
            invalid_nonce
            or not isinstance(self.verifier, str)
            or not self.verifier
        ):
            raise FederationError("OIDC login context is invalid")


class OidcFederationProvider(OidcTokenVerifier, FederationProvider):
    def __init__(
        self, handler: SQLAlchemyHandler, settings: Settings | OidcConnectionConfig
    ) -> None:
        options = (
            OidcConnectionConfig.from_settings(settings)
            if isinstance(settings, Settings)
            else settings
        )
        super().__init__(options)
        self.redirect_uri = _safe_url(options.redirect_uri, allow_http=True)
        if not urlsplit(self.redirect_uri).path.endswith(
            f"/v1/auth/federation/{options.connection_id}/callback"
        ):
            raise ValueError(
                "OIDC redirect URI must address this connection's Identity callback"
            )
        self._secret = options.client_secret
        self._downstream_audience = options.access_token_audience
        self._scopes = options.scope.split()
        self._context = SystemContext(db=handler)
        self._states = handler.get_table(FEDERATION_LOGIN_STATE_TABLE_DEF.table_name)

    async def discovery(self) -> dict[str, Any]:
        metadata = await super().discovery()
        if "client_secret_post" not in metadata.get(
            "token_endpoint_auth_methods_supported", []
        ):
            raise FederationError("OIDC issuer must support client_secret_post")
        return metadata

    async def begin_login(
        self, connection: FederationConnection, authorization_request_id: str
    ) -> str:
        metadata = await self.discovery()
        nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        async with self._context.transaction() as session:
            result = await session.execute(
                select(self._states).where(
                    self._states.c.request_id == authorization_request_id,
                    self._states.c.connection_id == connection.connection_id,
                )
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise FederationError("OIDC login request is missing")
            data = dict(row["data"] or {}, oidc_nonce=nonce, oidc_verifier=verifier)
            await session.execute(
                update(self._states)
                .where(
                    self._states.c.request_id == authorization_request_id,
                )
                .values(data=data)
            )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .decode()
            .rstrip("=")
        )
        return (
            metadata["authorization_endpoint"]
            + "?"
            + urlencode(
                {
                    "client_id": self.client_id,
                    "response_type": "code",
                    "scope": " ".join(self._scopes),
                    "redirect_uri": self.redirect_uri,
                    "state": authorization_request_id,
                    "nonce": nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    async def consume_callback(
        self, connection: FederationConnection, parameters: Mapping[str, str]
    ) -> FederationAuthenticationResult:
        request_id = str(parameters.get("state") or "")
        if not isinstance(parameters, ValidatedOidcCallback):
            raise FederationError("OIDC callback requires validated server state")
        if parameters.get("error") or not parameters.get("code"):
            raise FederationError("OIDC authentication was not completed")
        if parameters.get("iss") and parameters["iss"] != self.issuer:
            raise FederationError("OIDC authorization response issuer mismatch")
        metadata = await self.discovery()
        bundle = await self._json(
            "POST",
            metadata["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self._secret.get_secret_value(),
                "redirect_uri": self.redirect_uri,
                "code": parameters["code"],
                "code_verifier": parameters.verifier,
            },
        )
        claims = await self._validate_id_token(bundle, parameters.nonce, metadata)
        subject = claims["sub"]
        if "sid" in claims and (
            not isinstance(claims["sid"], str) or not 1 <= len(claims["sid"]) <= 256
        ):
            raise FederationError("OIDC session claim is invalid")
        for name in ("name", "preferred_username", "email"):
            if name in claims and not isinstance(claims[name], str):
                raise FederationError("OIDC directory claim is invalid")
        if "groups" in claims and (
            not isinstance(claims["groups"], list)
            or any(not isinstance(group, str) for group in claims["groups"])
        ):
            raise FederationError("OIDC groups claim is invalid")
        access_claims = await self.validate_access_token(
            bundle, audience=self._downstream_audience, subject=subject
        )
        if "groups" not in claims and "groups" in access_claims:
            groups = access_claims["groups"]
            if not isinstance(groups, list) or any(
                not isinstance(group, str) for group in groups
            ):
                raise FederationError("OIDC groups claim is invalid")
            claims["groups"] = groups
        return OidcAuthenticationResult(
            authorization_request_id=request_id,
            identity=self.identity_from_claims(connection, claims),
            token_bundle=bundle,
            external_sid=claims.get("sid"),
        )

    async def _validate_id_token(
        self, bundle: dict[str, Any], nonce: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            raw = bundle["id_token"]
            header = jwt.get_unverified_header(raw)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ValueError("invalid signing algorithm")
            key = await self._signing_key(header["kid"], metadata["jwks_uri"])
            claims = jwt.decode(
                raw,
                key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer,
                leeway=30,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
            )
            if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 256:
                raise ValueError("invalid subject")
            if (
                not nonce
                or not isinstance(claims["nonce"], str)
                or not secrets.compare_digest(claims["nonce"], nonce)
            ):
                raise ValueError("invalid nonce")
            audience = claims["aud"]
            multiple_audiences = isinstance(audience, list) and len(audience) > 1
            if (multiple_audiences or "azp" in claims) and claims.get(
                "azp"
            ) != self.client_id:
                raise ValueError("invalid authorized party")
            if "at_hash" in claims:
                digest = hashlib.sha256(
                    bundle["access_token"].encode("ascii")
                ).digest()[:16]
                expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
                if not isinstance(claims["at_hash"], str) or not secrets.compare_digest(
                    claims["at_hash"], expected
                ):
                    raise ValueError("invalid access token hash")
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
            raise FederationError("OIDC ID token validation failed") from None
