# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deployment-only certificate material. Never log/serialize this into an API.

The database is a trusted secret custodian, not an encryption boundary. There
is deliberately no code-derived key, stored CA private key or renewal worker.
"""

from __future__ import annotations

import calendar
import ipaddress
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .link_profile import ROLES, LinkProfileError, _authority

SCHEMA_VERSION = 1
WEB_ROLES = frozenset({"web", "manager-web"})
TLS_FILES = frozenset({"ca.crt", "tls.crt", "tls.key"})


def _usages(role: str) -> list[x509.ObjectIdentifier]:
    usages = []
    if role in {"gateway", "runtime", "agentserver"}:
        usages.append(ExtendedKeyUsageOID.SERVER_AUTH)
    if role != "agentserver":
        usages.append(ExtendedKeyUsageOID.CLIENT_AUTH)
    return usages


def ten_year_expiry(now: datetime) -> datetime:
    year = now.year + 10
    return now.replace(
        year=year, day=min(now.day, calendar.monthrange(year, now.month)[1])
    )


def _fingerprint(cert: x509.Certificate) -> str:
    return cert.fingerprint(hashes.SHA256()).hex()


def issue_bundle(
    *,
    mtls_deployment_id: str,
    endpoints: dict[str, str],
    sans: dict[str, list[str]] | None = None,
    mtls_binding_id: str | None = None,
    mtls_binding_epoch: int = 1,
    now: datetime | None = None,
) -> dict:
    """Random first issuance: all roles, ten calendar years, CA key discarded."""
    if (
        not 1 <= len(mtls_deployment_id) <= 64
        or mtls_binding_epoch < 1
        or (mtls_binding_id is not None and not 1 <= len(mtls_binding_id) <= 64)
    ):
        raise LinkProfileError("invalid mTLS deployment ID or binding epoch")
    if set(endpoints) - ROLES or set(sans or {}) - ROLES:
        raise LinkProfileError("unknown certificate role")
    endpoints = {role: _authority(value) for role, value in endpoints.items()}
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    expiry = ten_year_expiry(now)
    issuer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "JiuwenSwarm binding issuer")]
    )
    issuer = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(issuer_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(expiry)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(issuer_key, hashes.SHA256())
    )
    ca_pem = issuer.public_bytes(serialization.Encoding.PEM).decode()
    materials, pins = {}, {}
    for role in sorted(ROLES | WEB_ROLES):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        hosts = set((sans or {}).get(role, []))
        if role in endpoints:
            hosts.add(urlsplit(urlunsplit(("", endpoints[role], "", "", ""))).hostname)
        names = []
        for host in sorted(hosts):
            try:
                names.append(x509.IPAddress(ipaddress.ip_address(host)))
            except ValueError:
                names.append(x509.DNSName(host))
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, role)]))
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(expiry)
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(x509.ExtendedKeyUsage(_usages(role)), critical=False)
        )
        if names:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(names), critical=False
            )
        cert = builder.sign(issuer_key, hashes.SHA256())
        if role in ROLES:
            pins[role] = [_fingerprint(cert)]
        materials[role] = {
            "ca.crt": ca_pem,
            "tls.crt": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "tls.key": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
        }
    mtls_binding_id = mtls_binding_id or str(uuid.uuid4())
    materials["web"].update({
        "binding-id": mtls_binding_id,
        "binding-epoch": str(mtls_binding_epoch),
    })
    for role in sorted(ROLES):
        materials[role]["profile.json"] = json.dumps(
            {
                "version": 1,
                "status": "active",
                "persistence": "database",
                "mtls_deployment_id": mtls_deployment_id,
                "mtls_binding_id": mtls_binding_id,
                "mtls_binding_epoch": mtls_binding_epoch,
                "role": role,
                "tls": {
                    "ca_file": "ca.crt",
                    "cert_file": "tls.crt",
                    "key_file": "tls.key",
                },
                "peers": pins,
                "endpoints": endpoints,
            },
            sort_keys=True,
        )
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "mtls_deployment_id": mtls_deployment_id,
        "mtls_binding_id": mtls_binding_id,
        "mtls_binding_epoch": mtls_binding_epoch,
        "materials": materials,
    }
    validate_bundle(bundle, now=now)
    return bundle


def validate_bundle(
    bundle: dict,
    *,
    now: datetime | None = None,
    endpoints: dict | None = None,
    sans: dict | None = None,
) -> dict:
    """Validate a complete stored bundle. Returns only a safe public summary."""
    try:
        return _validate_bundle(bundle, now=now, endpoints=endpoints, sans=sans)
    except LinkProfileError:
        raise
    except Exception:
        # Crypto/JSON errors must not echo the private payload.
        raise LinkProfileError(
            "invalid or incomplete persisted certificate material"
        ) from None


def _validate_bundle(bundle, *, now, endpoints, sans):
    now = now or datetime.now(timezone.utc)
    roles = set(bundle["materials"])
    if bundle["schema_version"] != SCHEMA_VERSION or roles not in (ROLES, ROLES | WEB_ROLES):
        raise LinkProfileError("unsupported or incomplete certificate bundle")
    if (
        not bundle["mtls_deployment_id"]
        or not bundle["mtls_binding_id"]
        or bundle["mtls_binding_epoch"] < 1
    ):
        raise LinkProfileError("invalid stored binding")
    summaries, pins, ca_values, profiles, public_keys = {}, {}, set(), [], set()
    for role, files in bundle["materials"].items():
        required_files = TLS_FILES
        if role in ROLES:
            required_files |= {"profile.json"}
        elif role == "web":
            required_files |= {"binding-id", "binding-epoch"}
        if set(files) != required_files:
            raise LinkProfileError("certificate bundle has missing or unexpected files")
        ca = x509.load_pem_x509_certificate(files["ca.crt"].encode())
        cert = x509.load_pem_x509_certificate(files["tls.crt"].encode())
        key = serialization.load_pem_private_key(
            files["tls.key"].encode(), password=None
        )
        ca.verify_directly_issued_by(ca)
        cert.verify_directly_issued_by(ca)
        if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise LinkProfileError("issuer is not a CA")
        if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise LinkProfileError("role certificate must not be a CA")
        for candidate in (ca, cert):
            if (
                not candidate.not_valid_before_utc
                <= now
                < candidate.not_valid_after_utc
            ):
                raise LinkProfileError(
                    "persisted link certificate is not currently valid; explicit rotation required"
                )
        if cert.not_valid_after_utc > ca.not_valid_after_utc:
            raise LinkProfileError("role certificate outlives its issuer")
        encoding = (
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if key.public_key().public_bytes(*encoding) != cert.public_key().public_bytes(
            *encoding
        ):
            raise LinkProfileError("persisted certificate and private key do not match")
        public_keys.add(cert.public_key().public_bytes(*encoding))
        if cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value != role:
            raise LinkProfileError("incorrect role certificate subject")
        usages = set(
            cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        )
        if usages != set(_usages(role)):
            raise LinkProfileError("incorrect role certificate usage")
        ca_values.add(_fingerprint(ca))
        summaries[role] = {
            "fingerprint": _fingerprint(cert),
            "sans": [],
            "not_after": cert.not_valid_after_utc.isoformat(),
        }
        if role in WEB_ROLES:
            if role == "web" and (
                files["binding-id"] != bundle["mtls_binding_id"]
                or files["binding-epoch"] != str(bundle["mtls_binding_epoch"])
            ):
                raise LinkProfileError("persisted User Web material does not match binding")
            continue
        profile = json.loads(files["profile.json"])
        expected_profile = {
            "version": 1,
            "status": "active",
            "role": role,
            "mtls_deployment_id": bundle["mtls_deployment_id"],
            "mtls_binding_id": bundle["mtls_binding_id"],
            "mtls_binding_epoch": bundle["mtls_binding_epoch"],
            "tls": {"ca_file": "ca.crt", "cert_file": "tls.crt", "key_file": "tls.key"},
        }
        if any(profile.get(k) != v for k, v in expected_profile.items()):
            raise LinkProfileError("persisted role profile does not match binding")
        desired_endpoints = endpoints if endpoints is not None else profile["endpoints"]
        if profile["endpoints"] != desired_endpoints:
            raise LinkProfileError(
                "managed endpoints changed; explicit rotation required"
            )
        try:
            names = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
            actual = {str(x.value) for x in names}
        except x509.ExtensionNotFound:
            actual = set()
        required = set((sans or {}).get(role, []))
        if role in desired_endpoints:
            required.add(
                urlsplit(
                    urlunsplit(("", _authority(desired_endpoints[role]), "", "", ""))
                ).hostname
            )
        if not required.issubset(actual):
            raise LinkProfileError(
                "required certificate SAN changed; explicit rotation required"
            )
        pins[role] = [summaries[role]["fingerprint"]]
        profiles.append(profile)
        summaries[role]["sans"] = sorted(actual)
    if len(ca_values) != 1 or len({v["fingerprint"] for v in summaries.values()}) != len(roles):
        raise LinkProfileError("roles require distinct keys and a consistent issuer")
    if len(public_keys) != len(roles):
        raise LinkProfileError("roles require distinct private keys")
    if any(p["endpoints"] != profiles[0]["endpoints"] for p in profiles):
        raise LinkProfileError("role profiles disagree about managed endpoints")
    if any(p["peers"] != pins for p in profiles):
        raise LinkProfileError("persisted peer trust differs from certificate material")
    return {
        "mtls_deployment_id": bundle["mtls_deployment_id"],
        "mtls_binding_id": bundle["mtls_binding_id"],
        "mtls_binding_epoch": bundle["mtls_binding_epoch"],
        "roles": summaries,
    }


def materialize_role(bundle: dict, role: str, directory: Path) -> Path:
    """Write one role into a NEW private temporary/mounted directory, never overwrite."""
    validate_bundle(bundle)
    if role not in ROLES:
        raise LinkProfileError("unknown role")
    directory.mkdir(mode=0o700)
    for name, value in bundle["materials"][role].items():
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
    return directory / "profile.json"
