"""Seal the access token held briefly by a browser-bound one-time login code."""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from openjiuwen_runtime.service import FederationError

from identity_center.security.jwt_keys import private_pem


def _cipher() -> AESGCM:
    # All replicas already share this private key. Domain separation avoids
    # another deployment secret for the short-lived login-code envelope.
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=b"identity-center/federation-code/v1",
    ).derive(private_pem())
    return AESGCM(key)


def seal_token(token: str, user_id: str) -> str:
    nonce = os.urandom(12)
    encrypted = _cipher().encrypt(nonce, token.encode(), user_id.encode())
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def open_token(envelope: str, user_id: str) -> str:
    try:
        raw = base64.b64decode(envelope, altchars=b"-_", validate=True)
        return _cipher().decrypt(raw[:12], raw[12:], user_id.encode()).decode()
    except (InvalidTag, ValueError):
        raise FederationError("federation login code is invalid") from None
