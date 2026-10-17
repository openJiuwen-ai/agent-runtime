# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""link-auth 单元测试（非对称 Ed25519 + 进程内 TOFU 指纹固定）。

落库显式绑定（CertificatePinStore / verify_and_bind）的测试在 management 层
``management/tests/unit_tests/management_security/test_link_auth_bind.py``。
"""

from __future__ import annotations

import time

import pytest

from openjiuwen_runtime.foundation.security.link_auth import (
    AuthMode,
    InMemoryPinStore,
    LinkAuthError,
    NonceCache,
    build_token,
    build_token_header,
    fingerprint,
    generate_keypair,
    get_auth_mode,
    sign_token,
    verify_and_pin,
    verify_signature,
    verify_token,
)


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    """多数用例在 enforce 下跑；个别用例自行覆盖。"""
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "enforce")


# ---------- 纯密码学：验签 ----------

def test_sign_verify_roundtrip():
    priv, pub = generate_keypair()
    tok = sign_token(service_id="gw-1", service_type="gateway", private_b64=priv, public_b64=pub)
    claims = verify_signature(tok)
    assert claims.iss == "gw-1" and claims.typ == "gateway" and claims.pub == pub


def test_bad_signature_rejected():
    priv, pub = generate_keypair()
    tok = sign_token(service_id="gw-1", service_type="gateway", private_b64=priv, public_b64=pub)
    payload_b64, _, _ = tok.partition(".")
    with pytest.raises(LinkAuthError):
        verify_signature(f"{payload_b64}.AAAA")


def test_tampered_payload_rejected():
    priv, pub = generate_keypair()
    tok = sign_token(service_id="gw-1", service_type="gateway", private_b64=priv, public_b64=pub)
    payload_b64, _, sig = tok.partition(".")
    with pytest.raises(LinkAuthError):
        verify_signature(f"{payload_b64}x.{sig}")


def test_forged_pub_rejected():
    # 攻击者把载荷里的 pub 换成别的、却用受害者私钥签 → 验签用载荷里的 pub，签名对不上 → 拒。
    vpriv, _ = generate_keypair()
    _, apub = generate_keypair()
    tok = sign_token(service_id="gw-1", service_type="gateway", private_b64=vpriv, public_b64=apub)
    with pytest.raises(LinkAuthError):
        verify_signature(tok)


def test_malformed_token_rejected():
    with pytest.raises(LinkAuthError):
        verify_signature("no-dot-here")


def test_expired_token_rejected():
    priv, pub = generate_keypair()
    tok = sign_token(service_id="gw-1", service_type="gateway", private_b64=priv, public_b64=pub)
    with pytest.raises(LinkAuthError):
        verify_signature(tok, ttl=300, now=int(time.time()) + 10_000)


def test_expect_type_mismatch_rejected():
    priv, pub = generate_keypair()
    tok = sign_token(service_id="as-1", service_type="agent_server", private_b64=priv, public_b64=pub)
    with pytest.raises(LinkAuthError):
        verify_signature(tok, expect_type="gateway")


# ---------- nonce 防重放 ----------

def test_nonce_replay():
    cache = NonceCache(ttl=300)
    priv, pub = generate_keypair()
    tok = sign_token(service_id="gw-1", service_type="gateway", private_b64=priv, public_b64=pub)
    r1 = verify_token(tok, expect_type="gateway", nonce_cache=cache)
    r2 = verify_token(tok, expect_type="gateway", nonce_cache=cache)  # 同一令牌重放
    assert r1.ok is True and r2.ok is False and r2.reason == "nonce replay"


# ---------- TOFU 指纹固定 ----------

def test_tofu_same_key_passes():
    store = InMemoryPinStore()
    priv, pub = generate_keypair()
    for _ in range(3):
        tok = build_token(service_id="gw-1", service_type="gateway", private_b64=priv, public_b64=pub)
        res = verify_and_pin(store, tok, expect_type="gateway")
        assert res.allowed and res.ok


def test_tofu_changed_key_rejected():
    store = InMemoryPinStore()
    priv, pub = generate_keypair()
    tok = build_token(service_id="gw-1", service_type="gateway", private_b64=priv, public_b64=pub)
    assert verify_and_pin(store, tok, expect_type="gateway").ok  # 首次 TOFU 记录

    ipriv, ipub = generate_keypair()  # 冒充者：同 iss、不同密钥
    itok = build_token(service_id="gw-1", service_type="gateway", private_b64=ipriv, public_b64=ipub)
    res = verify_and_pin(store, itok, expect_type="gateway")
    assert (not res.allowed) and res.reason == "fingerprint mismatch"


def test_fingerprint_stable():
    _, pub = generate_keypair()
    assert fingerprint(pub) == fingerprint(pub) and len(fingerprint(pub)) == 64


# ---------- 开关：off ----------

def test_mode_off_client_no_header(monkeypatch):
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "off")
    priv, pub = generate_keypair()
    assert build_token_header(service_id="gw-1", service_type="gateway", private_b64=priv, public_b64=pub) == {}


def test_mode_off_server_allows(monkeypatch):
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "off")
    res = verify_and_pin(InMemoryPinStore(), None, expect_type="gateway")
    assert res.allowed is True and res.mode is AuthMode.OFF


def test_mode_unset_defaults_off(monkeypatch):
    monkeypatch.delenv("CLAW_LINK_AUTH_MODE", raising=False)
    assert get_auth_mode() is AuthMode.OFF


# ---------- 开关：enforce / observe ----------

def test_enforce_roundtrip():
    priv, pub = generate_keypair()
    headers = build_token_header(service_id="gw-1", service_type="gateway", private_b64=priv, public_b64=pub)
    assert headers
    res = verify_and_pin(InMemoryPinStore(), list(headers.values())[0], expect_type="gateway")
    assert res.allowed and res.ok


def test_enforce_missing_token_rejected():
    res = verify_and_pin(InMemoryPinStore(), None, expect_type="gateway")
    assert res.allowed is False and res.ok is False


def test_observe_allows_but_reports_failure(monkeypatch):
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "observe")
    res = verify_and_pin(InMemoryPinStore(), None, expect_type="gateway")
    assert res.allowed is True and res.ok is False


# ---------- 令牌携带的公钥字段不可信 ----------

def _token_with_raw_pub_field(pub_field: str) -> str:
    import base64
    import json

    payload = {
        "iss": "peer-1",
        "typ": "gateway",
        "iat": int(time.time()),
        "nonce": "nonce-1",
        "pub": pub_field,
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    sig_b64 = base64.urlsafe_b64encode(b"signature").rstrip(b"=").decode("ascii")
    return f"{payload_b64}.{sig_b64}"


def test_non_base64_pub_is_rejected_as_link_auth_error():
    """载荷里的 pub 非法 base64 时，应统一抛 LinkAuthError，而不是泄漏 binascii.Error。"""
    token = _token_with_raw_pub_field("!!!not-base64!!!")

    with pytest.raises(LinkAuthError):
        verify_signature(token)

    res = verify_token(token, expect_type="gateway")
    assert res.allowed is False and res.ok is False
    assert "public key" in res.reason


def test_observe_mode_allows_non_base64_pub(monkeypatch):
    """observe 模式对验不过的令牌应放行并给出 ok=False，而不是抛异常中断握手。"""
    monkeypatch.setenv("CLAW_LINK_AUTH_MODE", "observe")

    res = verify_token(_token_with_raw_pub_field("!!!not-base64!!!"), expect_type="gateway")

    assert res.allowed is True and res.ok is False
    assert "public key" in res.reason
