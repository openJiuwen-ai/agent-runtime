# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager-independent, deployment-owned link identity and endpoint contract.

The profile is trusted local configuration (never accepted from HTTP). Peers are
identified by the DER fingerprint of the certificate authenticated by TLS, not
by a caller-provided identity header. Private keys remain in files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from cryptography import x509

PROFILE_ENV = "JIUWENSWARM_LINK_MTLS_PROFILE"
# Internal installation layout, not another customer environment variable.
DEFAULT_IDENTITY_ROOT = Path("/etc/jiuwenswarm/link-mtls")
PEER_CERT_SCOPE = "jiuwenswarm.peer_certificate"
ROLES = frozenset({"gateway", "runtime", "agentserver", "manager"})


class LinkProfileError(ValueError):
    """Invalid, revoked or mismatched deployment identity."""


def canonical_role(role: str) -> str:
    """Validate a service role from the current mTLS contract."""
    if role not in ROLES:
        raise LinkProfileError("unknown service role")
    return role


def role_directory(root: Path, role: str) -> Path:
    role = canonical_role(role)
    return root / role


def installed_profile_path(role: str, *, root: Path | None = None) -> Path:
    return (
        role_directory(root if root is not None else DEFAULT_IDENTITY_ROOT, role)
        / "profile.json"
    )


def load_service_identity(role: str) -> "LinkProfile":
    """Load an installed identity, never bootstrap or rotate it during startup.

    The explicit override is reserved for the deployment helper and isolated tests.
    A bad override must fail, not fall back to a different installed identity.
    The expected role is supplied by code, never inferred from the file.
    """
    role = canonical_role(role)
    override = os.getenv(PROFILE_ENV, "").strip()
    path = Path(override) if override else installed_profile_path(role)
    if not path.is_file():
        raise LinkProfileError(
            f"{role} link identity is not installed at {path}; "
            "enable certificate deployment with the matching deployment tool"
        )
    return LinkProfile.load(str(path), roles={role})


def fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def cert_fingerprint(path: str) -> str:
    cert = x509.load_pem_x509_certificate(Path(path).read_bytes())
    _check_validity(cert)
    from cryptography.hazmat.primitives.serialization import Encoding

    return fingerprint(cert.public_bytes(Encoding.DER))


def _check_validity(cert: x509.Certificate) -> None:
    now = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
        raise LinkProfileError("link certificate is not currently valid")


def _authority(value: str) -> str:
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise LinkProfileError("endpoint authority must be host:port")
    parsed = urlsplit(urlunsplit(("", value, "", "", "")))
    try:
        valid = (
            parsed.hostname
            and parsed.port
            and not (
                parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            )
        )
    except ValueError as exc:
        raise LinkProfileError("invalid endpoint port") from exc
    if not valid:
        raise LinkProfileError(
            "endpoint authority must be host:port without scheme or path"
        )
    return parsed.netloc.lower()


@dataclass(frozen=True)
class LinkProfile:
    path: Path
    mtls_deployment_id: str
    mtls_binding_id: str
    mtls_binding_epoch: int
    role: str
    ca_file: str
    cert_file: str
    key_file: str
    material_references: tuple[str, ...]
    material_digests: tuple[str, ...]

    @staticmethod
    def _material_snapshot(source: Path, data: dict) -> tuple[str, ...]:
        # Resolve the profile generation once. Relative TLS paths must come
        # from that same generation, not a second read of Kubernetes ..data.
        return tuple(
            str((source.parent / data["tls"][name]).resolve())
            for name in ("ca_file", "cert_file", "key_file")
        )

    @staticmethod
    def _digests(paths: tuple[str, ...]) -> tuple[str, ...]:
        try:
            return tuple(
                hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths
            )
        except OSError as exc:
            raise LinkProfileError("link TLS material cannot be read") from exc

    @classmethod
    def load(cls, path: str, *, roles: set[str] | None = None) -> "LinkProfile":
        # Keep the logical path so AtomicWriter/symlink swaps remain visible.
        # TLS sockets still use a coherent immutable startup snapshot.
        source = Path(path).expanduser().absolute()
        snapshot = source.resolve()
        data = cls._read(snapshot)
        role = data.get("role")
        if role not in ROLES or (roles is not None and role not in roles):
            raise LinkProfileError("profile role does not match this component")
        tls = data.get("tls", {})

        def material(name):
            raw = tls.get(name)
            if not isinstance(raw, str) or not raw:
                raise LinkProfileError(f"profile tls.{name} is required")
            result = (snapshot.parent / raw).resolve()
            if not result.is_file():
                raise LinkProfileError(f"profile tls.{name} file is missing")
            return str(result)

        mtls_deployment_id = data.get("mtls_deployment_id")
        mtls_binding_id = data.get("mtls_binding_id")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (mtls_deployment_id, mtls_binding_id)
        ):
            raise LinkProfileError(
                "profile requires mtls_deployment_id and mtls_binding_id"
            )
        mtls_binding_epoch = data.get("mtls_binding_epoch")
        if type(mtls_binding_epoch) is not int or mtls_binding_epoch < 1:
            raise LinkProfileError(
                "profile mtls_binding_epoch must be a positive integer"
            )
        paths = tuple(material(name) for name in ("ca_file", "cert_file", "key_file"))
        profile = cls(
            source,
            mtls_deployment_id,
            mtls_binding_id,
            mtls_binding_epoch,
            role,
            *paths,
            tuple(data["tls"][name] for name in ("ca_file", "cert_file", "key_file")),
            cls._digests(paths),
        )
        for suffix, expected in (
            ("CA_FILE", profile.ca_file),
            ("CERT_FILE", profile.cert_file),
            ("KEY_FILE", profile.key_file),
        ):
            env_name = "JIUWENSWARM_LINK_MTLS_" + suffix
            value = os.getenv(env_name, "").strip()
            if value and str(Path(value).expanduser().resolve()) != expected:
                raise LinkProfileError(
                    f"{env_name} conflicts with the deployment profile"
                )
        profile.current()
        profile.ssl_context()
        return profile

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LinkProfileError("link profile cannot be read") from exc
        if not isinstance(data, dict) or data.get("version") != 1:
            raise LinkProfileError("unsupported link profile version")
        if data.get("status") != "active":
            raise LinkProfileError("link binding is not active")
        if not isinstance(data.get("role"), str) or data["role"] not in ROLES:
            raise LinkProfileError("invalid local role")
        tls = data.get("tls")
        if not isinstance(tls, dict) or any(
            not isinstance(tls.get(name), str) or not tls[name]
            for name in ("ca_file", "cert_file", "key_file")
        ):
            raise LinkProfileError("profile requires TLS file references")
        peers = data.get("peers")
        if not isinstance(peers, dict) or not peers:
            raise LinkProfileError("profile requires pinned peer certificates")
        all_pins = []
        for role, pins in peers.items():
            if role not in ROLES or not isinstance(pins, list) or not pins:
                raise LinkProfileError("invalid peer role or pin list")
            if any(
                not isinstance(p, str) or not re.fullmatch(r"[0-9a-f]{64}", p)
                for p in pins
            ):
                raise LinkProfileError("peer pins must be SHA-256 DER fingerprints")
            all_pins.extend(pins)
        if len(set(all_pins)) != len(all_pins):
            raise LinkProfileError("a certificate cannot represent multiple peer roles")
        endpoints = data.get("endpoints", {})
        if not isinstance(endpoints, dict) or any(
            role not in ROLES for role in endpoints
        ):
            raise LinkProfileError("invalid managed endpoints")
        for authority in endpoints.values():
            _authority(authority)
        return data

    def current(self) -> dict:
        """Recheck revocation on every request, including pooled TLS connections."""
        return self._current_snapshot()[0]

    def _current_snapshot(self) -> tuple[dict, tuple[str, ...]]:
        snapshot = self.path.resolve()
        data = self._read(snapshot)
        if (
            data.get("mtls_deployment_id"),
            data.get("mtls_binding_id"),
            data.get("mtls_binding_epoch"),
            data.get("role"),
        ) != (
            self.mtls_deployment_id,
            self.mtls_binding_id,
            self.mtls_binding_epoch,
            self.role,
        ):
            raise LinkProfileError("binding changed; restart with the new identity")
        references = tuple(
            data["tls"][name] for name in ("ca_file", "cert_file", "key_file")
        )
        if references != self.material_references:
            raise LinkProfileError(
                "TLS material reference changed; restart with the new identity"
            )
        paths = self._material_snapshot(snapshot, data)
        if self._digests(paths) != self.material_digests:
            raise LinkProfileError(
                "TLS material changed; restart with the new identity"
            )
        if cert_fingerprint(paths[1]) not in data["peers"].get(self.role, []):
            raise LinkProfileError("local certificate is not pinned for this role")
        return data, paths

    def ssl_context(self, *, ca_file: str | None = None) -> ssl.SSLContext:
        _, paths = self._current_snapshot()
        context = ssl.create_default_context(cafile=ca_file or paths[0])
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(paths[1], paths[2])
        return context

    def headers(self) -> dict[str, str]:
        self.current()
        return {
            "X-Jiuwenswarm-Mtls-Binding-Id": self.mtls_binding_id,
            "X-Jiuwenswarm-Mtls-Binding-Epoch": str(self.mtls_binding_epoch),
        }

    def verify_peer(
        self,
        der: bytes | None,
        allowed_roles: set[str],
        *,
        expected_fingerprints: set[str] | None = None,
    ) -> str:
        data = self.current()
        if not der:
            raise LinkProfileError("authenticated TLS peer certificate is missing")
        _check_validity(x509.load_der_x509_certificate(der))
        pin = fingerprint(der)
        if expected_fingerprints is not None:
            if pin not in expected_fingerprints:
                raise LinkProfileError(
                    "peer certificate is not authorized for this instance binding"
                )
            return next(iter(allowed_roles))
        for role in allowed_roles:
            if pin in data["peers"].get(role, []):
                return role
        raise LinkProfileError(
            "peer certificate is not authorized for this binding and role"
        )

    def authorize(self, scope: dict, headers, *, header_required: bool = True) -> None:
        path = scope.get("path", "")
        health = path in {"/healthz", "/api/health", "/api/v1/health", "/api/v1/ready"}
        if health:
            allowed = {"manager", "gateway", "runtime"}
        elif self.role == "gateway":
            allowed = {"manager"}
        elif self.role == "runtime":
            allowed = (
                {"gateway"}
                if path
                in {
                    "/api/session/route",
                    "/api/session/touch",
                    "/api/session/cleanup",
                    "/api/session/config_refresh",
                }
                else {"manager"}
            )
        elif self.role == "agentserver":
            allowed = {"gateway"}
        else:
            raise LinkProfileError("manager profiles cannot serve data-plane APIs")
        self.verify_peer(scope.get(PEER_CERT_SCOPE), allowed)
        if header_required and not health:
            normalized = {str(k).lower(): v for k, v in headers.items()}
            expected = {
                "x-jiuwenswarm-mtls-binding-id": self.mtls_binding_id,
                "x-jiuwenswarm-mtls-binding-epoch": str(self.mtls_binding_epoch),
            }
            if any(normalized.get(key) != value for key, value in expected.items()):
                raise LinkProfileError(
                    "request binding does not match authenticated deployment"
                )

    def endpoint(self, value: str, *, role: str, enforced: bool = True) -> str:
        """Only link:// aliases or declared authorities are protocol-managed.

        Explicit external HTTP URLs are NOT silently upgraded. Ports, paths and
        query strings are preserved; credentials and fragments are forbidden.
        """
        data = self.current()
        authority = data.get("endpoints", {}).get(role)
        value = value.strip()
        if "://" not in value:
            bare = _authority(value)
            if not authority or bare != _authority(authority):
                raise LinkProfileError(
                    "bare endpoint must match the declared managed authority"
                )
            return ("https://" if enforced else "http://") + authority
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.fragment:
            raise LinkProfileError("endpoint credentials and fragments are forbidden")
        if parsed.scheme == "link":
            if parsed.netloc != role or not authority:
                raise LinkProfileError(f"managed endpoint {role} is not configured")
            return urlunsplit(
                (
                    "https" if enforced else "http",
                    authority,
                    parsed.path,
                    parsed.query,
                    "",
                )
            )
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise LinkProfileError("endpoint must be HTTP(S) or link://role")
        if authority and parsed.netloc.lower() == _authority(authority):
            return urlunsplit(
                (
                    "https" if enforced else parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.query,
                    "",
                )
            )
        if enforced and parsed.scheme != "https":
            raise LinkProfileError(
                "external endpoint must explicitly use https:// in enforce mode"
            )
        return value.rstrip("/")

    def server_kwargs(self) -> dict:
        from .link_transport import PeerCertificateH11Protocol

        _, paths = self._current_snapshot()
        return {
            "http": PeerCertificateH11Protocol,
            "ws": "none",
            "ssl_ca_certs": paths[0],
            "ssl_certfile": paths[1],
            "ssl_keyfile": paths[2],
            "ssl_cert_reqs": ssl.CERT_REQUIRED,
        }

    def client_kwargs(
        self,
        *,
        role: str,
        expected_fingerprints: set[str] | None = None,
        ca_file: str | None = None,
    ) -> dict:
        from .link_transport import PinnedAsyncTransport

        self.current()
        return {
            "transport": PinnedAsyncTransport(
                self,
                role,
                expected_fingerprints=expected_fingerprints,
                ca_file=ca_file,
            )
        }
