"""OIDC discovery, signing-key cache and access-token verification."""

from __future__ import annotations

import asyncio
import ssl
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from openjiuwen_runtime.service import (
    ExternalIdentity,
    FederationConnection,
    FederationError,
)

from .config import OidcConnectionConfig

_JWKS_REFRESH_COOLDOWN_SECONDS = 60


def _safe_url(value: str, *, allow_http: bool) -> str:
    parsed = urlsplit(value)
    loopback_http = (
        allow_http
        and parsed.scheme == "http"
        and parsed.hostname
        in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
    )
    invalid_authority = not parsed.hostname or parsed.username or parsed.password
    has_extra_components = parsed.query or parsed.fragment
    invalid_scheme = parsed.scheme != "https" and not loopback_http
    if invalid_authority or has_extra_components or invalid_scheme:
        raise ValueError(
            "OIDC URLs require HTTPS; explicit local development permits loopback HTTP only"
        )
    return value


class OidcTokenVerifier:
    """Verify externally issued access tokens without requiring a client secret."""

    def __init__(self, options: OidcConnectionConfig) -> None:
        self.options = options
        self.issuer = _safe_url(options.issuer, allow_http=True)
        self.client_id = options.client_id
        self._allow_http = True
        self._http = httpx.AsyncClient(
            timeout=10.0,
            verify=ssl.create_default_context(cafile=options.ca_bundle),
            follow_redirects=False,
            trust_env=False,
        )
        self._metadata: dict[str, Any] | None = None
        self._metadata_until = 0.0
        self._jwks: dict[str, Any] = {}
        self._jwks_until = 0.0
        self._jwks_refresh_after = 0.0
        self._cache_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    async def _json(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        try:
            response = await self._http.request(method, url, **kwargs)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError("expected object")
            return data
        except (httpx.HTTPError, ValueError, TypeError):
            # Provider bodies and HTTP exception strings may contain credentials.
            raise FederationError("OIDC provider request failed") from None

    async def discovery(self) -> dict[str, Any]:
        """Return issuer metadata after validating its endpoints and PKCE support."""
        async with self._cache_lock:
            if self._metadata is not None and time.monotonic() < self._metadata_until:
                return self._metadata
            metadata = await self._json(
                "GET", self.issuer + "/.well-known/openid-configuration"
            )
            if metadata.get("issuer") != self.issuer:
                raise FederationError("OIDC discovery issuer mismatch")
            if "S256" not in metadata.get("code_challenge_methods_supported", []):
                raise FederationError("OIDC issuer must support S256 PKCE")
            for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                try:
                    endpoint = _safe_url(
                        str(metadata.get(name) or ""), allow_http=self._allow_http
                    )
                except ValueError:
                    raise FederationError(
                        "OIDC discovery contains an invalid endpoint"
                    ) from None
                # Keycloak endpoints share the configured issuer's origin. Never
                # follow metadata redirects or send a secret to another origin.
                if (
                    urlsplit(endpoint).netloc != urlsplit(self.issuer).netloc
                    or urlsplit(endpoint).scheme != urlsplit(self.issuer).scheme
                ):
                    raise FederationError("OIDC discovery endpoint origin mismatch")
            self._metadata = metadata
            self._metadata_until = time.monotonic() + 300
            return metadata

    async def _signing_key(self, kid: str, jwks_uri: str):
        async with self._cache_lock:
            now = time.monotonic()
            stale = now >= self._jwks_until
            keys = self._jwks.get("keys", [])
            matches = [key for key in keys if key.get("kid") == kid]
            if stale or not matches:
                if now < self._jwks_refresh_after:
                    raise FederationError("OIDC signing key not found")
                # Also throttle failed fetches; publish only a validated key set.
                self._jwks_refresh_after = now + _JWKS_REFRESH_COOLDOWN_SECONDS
                jwks = await self._json("GET", jwks_uri)
                keys = jwks.get("keys", [])
                if not isinstance(keys, list) or any(
                    not isinstance(key, dict) for key in keys
                ):
                    raise FederationError("OIDC signing keys are invalid")
                self._jwks = jwks
                self._jwks_until = time.monotonic() + 300
                # Normal cache loads leave one immediate refresh for key rotation.
                if stale:
                    self._jwks_refresh_after = 0.0
                matches = [key for key in keys if key.get("kid") == kid]
            if len(matches) != 1:
                raise FederationError("OIDC signing key not found")
            key = matches[0]
            if (
                key.get("kty") != "RSA"
                or key.get("use", "sig") != "sig"
                or key.get("alg", "RS256") != "RS256"
            ):
                raise FederationError("OIDC signing key is not an RS256 signing key")
            try:
                return jwt.PyJWK.from_dict(key, algorithm="RS256").key
            except (jwt.PyJWTError, ValueError, KeyError):
                raise FederationError("OIDC signing key is invalid") from None

    async def validate_access_token(
        self,
        bundle: dict[str, Any],
        *,
        audience: str,
        subject: str | None = None,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            metadata = await self.discovery()
            header = jwt.get_unverified_header(bundle["access_token"])
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ValueError()
            key = await self._signing_key(header["kid"], metadata["jwks_uri"])
            claims = jwt.decode(
                bundle["access_token"],
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=audience,
                options={
                    "require": ["iss", "sub", "aud", "iat"]
                    + (["exp"] if self.options.require_exp else [])
                },
            )
            if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 256:
                raise ValueError()
            if (subject is not None and claims["sub"] != subject) or claims.get(
                "azp"
            ) != (client_id or self.client_id):
                raise ValueError()
            if bundle.get("token_type", "").lower() != "bearer":
                raise ValueError()
            if claims.get("typ", "Bearer") != "Bearer":
                raise ValueError()
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
            raise FederationError("OIDC access token validation failed") from None

    def identity_from_claims(
        self, connection: FederationConnection, claims: dict[str, Any]
    ) -> ExternalIdentity:
        def value(name: str, default: str):
            current: Any = claims
            for part in self.options.claim_mapping.get(name, default).split("."):
                current = current.get(part) if isinstance(current, dict) else None
            return current

        username = value("preferred_username", "preferred_username")
        email = value("email", "email")
        display = value("display_name", "name") or username or claims["sub"]
        groups = value("groups", "groups")
        if any(
            item is not None and not isinstance(item, str)
            for item in (username, email, display)
        ):
            raise FederationError("OIDC directory claim is invalid")
        if groups is not None and (
            not isinstance(groups, list) or any(not isinstance(g, str) for g in groups)
        ):
            raise FederationError("OIDC groups claim is invalid")
        attributes = {
            "preferred_username": username,
            "email": email,
            "groups": groups,
            "email_verified": claims.get("email_verified"),
        }
        attributes = {key: item for key, item in attributes.items() if item is not None}
        return ExternalIdentity(
            connection_id=connection.connection_id,
            issuer=self.issuer,
            external_subject=claims["sub"],
            display_name=display[:128],
            email=email,
            attributes=attributes,
        )
