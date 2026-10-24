# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""控制链路握手鉴权 —— 非对称（Ed25519）令牌签发与验证。

供两条 WebSocket 控制链路共用：Claw Manager ↔ Gateway、Gateway ↔ AgentServer。

模型
----
- **身份密钥对**：每个端各持一对 Ed25519 密钥，私钥永不出端、仅用于签名；公钥可公开，
  握手时随令牌出示给对端。无任何预共享秘密。
- **链路令牌（CLT）**：握手期出示的一次性身份证明，载荷含签发者身份、类型、签发时间、
  随机数(nonce) 与**签发者公钥**，由签发者私钥签名。验证方用令牌内嵌的公钥验签，确认
  「持令牌者确实握有该公钥对应的私钥」。
- **信任固定（指纹绑定）**：验证方记录某身份的公钥指纹；之后每次握手都比对，不一致即拒。
  指纹存储经 :class:`IPinStore` 抽象：进程级端用 :class:`InMemoryPinStore`（TOFU）；
  持久端用 management 层的 ``CertificatePinStore``（落库显式绑定）。

密码学原语全部复用同层 :mod:`._primitives`（Ed25519/指纹/base64），本模块只负责令牌
信封、有效期/类型、nonce 防重放与指纹固定的编排。

开关 ``CLAW_LINK_AUTH_MODE``
---------------------------
- ``off``（默认）：完全不鉴权，行为与未引入本模块时一致。
- ``observe``：照常验签并记日志，但不拒绝（灰度观察）。
- ``enforce``：验不过即拒。
"""

from __future__ import annotations

import base64
import enum
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from . import _primitives as _p

logger = logging.getLogger(__name__)

# 握手期携带令牌的 HTTP 头名（自定义头，避免与业务 Authorization 语义相撞）。
LINK_TOKEN_HEADER = "X-Claw-Link-Token"

# 令牌默认有效期（秒）；可用 CLAW_LINK_TOKEN_TTL 覆盖。
_DEFAULT_TTL = 300
# 允许的时钟前偏（秒）：各端无 NTP 同步时，签发时间可能略超本机 now。
_CLOCK_SKEW = 60


class LinkAuthError(Exception):
    """令牌格式非法、验签失败或指纹不匹配。"""


class AuthMode(str, enum.Enum):
    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"


def get_auth_mode() -> AuthMode:
    """读取 ``CLAW_LINK_AUTH_MODE``，默认 ``off``（无法识别的值一律按 off，fail-safe）。"""
    raw = os.getenv("CLAW_LINK_AUTH_MODE", "").strip().lower()
    if raw == AuthMode.ENFORCE.value:
        return AuthMode.ENFORCE
    if raw == AuthMode.OBSERVE.value:
        return AuthMode.OBSERVE
    return AuthMode.OFF


def get_ttl() -> int:
    raw = os.getenv("CLAW_LINK_TOKEN_TTL", "").strip()
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            logger.warning("[link_auth] invalid CLAW_LINK_TOKEN_TTL=%r, using default", raw)
    return _DEFAULT_TTL


# ---------------------------------------------------------------------------
# Ed25519 密钥与指纹（薄封装，复用 _primitives；对外保持 base64 字符串接口）
# ---------------------------------------------------------------------------

def generate_keypair() -> tuple[str, str]:
    """生成一对 Ed25519 密钥，返回 ``(private_b64, public_b64)``（32 字节 Raw 的 base64）。"""
    priv, pub = _p.ed25519_generate()
    return _p.b64e(priv), _p.b64e(pub)


def fingerprint(public_b64: str) -> str:
    """公钥指纹：Raw 公钥的 SHA-256（hex），用于记录与比对。"""
    return _p.fingerprint(_p.b64d(public_b64))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def new_nonce() -> str:
    return secrets.token_urlsafe(12)


# ---------------------------------------------------------------------------
# 令牌签发 / 验证（纯密码学，不依赖环境）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Claims:
    """令牌声明：签发者身份(iss)、类型(typ)、签发时间(iat)、随机数(nonce)、签发者公钥(pub)。"""

    iss: str
    typ: str
    iat: int
    nonce: str
    pub: str  # 签发者 Ed25519 公钥（base64），用于验签与指纹固定


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sign_token(
    *, service_id: str, service_type: str, private_b64: str, public_b64: str
) -> str:
    """用私钥对声明签名，返回令牌 ``<payload_b64url>.<sig_b64url>``。"""
    payload = {
        "iss": service_id,
        "typ": service_type,
        "iat": int(time.time()),
        "nonce": new_nonce(),
        "pub": public_b64,
    }
    payload_b64 = _b64url_encode(_canonical(payload).encode("utf-8"))
    sig = _p.ed25519_sign(_p.b64d(private_b64), payload_b64.encode("ascii"))
    return f"{payload_b64}.{_b64url_encode(sig)}"


def verify_signature(
    token: str,
    *,
    ttl: int | None = None,
    expect_type: str | None = None,
    now: int | None = None,
) -> Claims:
    """验签并解析令牌；失败抛 :class:`LinkAuthError`。

    用令牌**内嵌的公钥**验签（证明持令牌者握有对应私钥），并校验有效期/类型。
    不做指纹固定与 nonce 防重放（由 :func:`verify_token` 处理）。
    """
    if not token or "." not in token:
        raise LinkAuthError("malformed token")
    payload_b64, _, sig_b64 = token.partition(".")
    try:
        payload = json.loads(_b64url_decode(payload_b64))
        pub = str(payload["pub"])
        sig = _b64url_decode(sig_b64)
    except (ValueError, TypeError, KeyError) as exc:
        raise LinkAuthError(f"bad token payload: {exc}") from exc

    # 用内嵌公钥验签：证明持令牌者握有该公钥对应的私钥。
    # 公钥由令牌携带、不可信：非法 base64 必须按 LinkAuthError 拒绝，
    # 不能把 binascii.Error 泄漏给 verify_token 的调用方（observe 模式应放行并记日志）。
    try:
        pub_raw = _p.b64d(pub)
    except (ValueError, TypeError) as exc:
        raise LinkAuthError(f"bad public key encoding: {exc}") from exc
    if not _p.ed25519_verify(pub_raw, payload_b64.encode("ascii"), sig):
        raise LinkAuthError("bad signature")

    try:
        claims = Claims(
            iss=str(payload["iss"]),
            typ=str(payload["typ"]),
            iat=int(payload["iat"]),
            nonce=str(payload["nonce"]),
            pub=pub,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LinkAuthError(f"missing/invalid claims: {exc}") from exc

    now = int(time.time()) if now is None else now
    ttl = get_ttl() if ttl is None else ttl
    if claims.iat > now + _CLOCK_SKEW:
        raise LinkAuthError("token issued in the future")
    if claims.iat < now - ttl:
        raise LinkAuthError("token expired")
    if expect_type is not None and claims.typ != expect_type:
        raise LinkAuthError(f"unexpected service_type {claims.typ!r}, want {expect_type!r}")
    return claims


class NonceCache:
    """进程内、带 TTL 的 nonce 缓存，用于防重放。

    仅进程内有效；多进程/多端下各端各自一份即可（合法对端每次握手都用新 nonce）。
    """

    def __init__(self, ttl: int | None = None) -> None:
        self._ttl = ttl
        self._seen: dict[str, float] = {}

    def check_and_add(self, nonce: str, *, now: float | None = None) -> bool:
        """nonce 未见过则记录并返回 True；已见过（重放）返回 False。"""
        now = time.time() if now is None else now
        ttl = get_ttl() if self._ttl is None else self._ttl
        if self._seen:
            expired = [n for n, exp in self._seen.items() if exp <= now]
            for n in expired:
                self._seen.pop(n, None)
        if nonce in self._seen:
            return False
        self._seen[nonce] = now + ttl + _CLOCK_SKEW
        return True


# ---------------------------------------------------------------------------
# 指纹固定存储抽象：进程内 TOFU / 落库显式绑定（management.CertificatePinStore）
# ---------------------------------------------------------------------------

@runtime_checkable
class IPinStore(Protocol):
    """同步指纹固定存储接口（``iss -> fingerprint``）。

    落库的 :class:`CertificateManager` 适配器（management 层，异步）走
    :func:`verify_and_bind`；本同步接口用于进程内 TOFU。
    """

    def pinned(self, iss: str) -> Optional[str]:
        ...

    def remember(self, iss: str, fp: str) -> None:
        ...


class InMemoryPinStore:
    """进程内 TOFU 指纹固定表（``iss -> fingerprint``）。

    首次见到某身份即记录其公钥指纹；之后比对，不一致即视为冒充。仅进程内有效——
    身份密钥持久化保证对端指纹稳定，本端进程重启后对各对端重新 TOFU（在「首次握手可信」
    的前提下可接受）。
    """

    def __init__(self) -> None:
        self._pins: dict[str, str] = {}

    def pinned(self, iss: str) -> Optional[str]:
        return self._pins.get(iss)

    def remember(self, iss: str, fp: str) -> None:
        self._pins[iss] = fp


# ---------------------------------------------------------------------------
# 集成入口：建令牌（持私钥端）/ 验令牌（对端，含指纹固定）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VerifyResult:
    """验令牌结论。

    - ``allowed``：是否放行（off / observe 恒为 True）。
    - ``ok``：验证是否真正通过（不受 mode 影响，供日志/观察）。
    - ``reason``：原因，便于排查。
    - ``mode``：当时开关状态。
    - ``peer_fp``：对端公钥指纹（验签通过时给出，供指纹记录/比对）。
    - ``claims``：解析出的声明。
    """

    allowed: bool
    ok: bool
    reason: str
    mode: AuthMode
    peer_fp: str | None = None
    claims: Claims | None = None


def build_token(
    *, service_id: str, service_type: str, private_b64: str | None, public_b64: str | None
) -> str | None:
    """现签一枚裸令牌（放进握手头或 connection.ack 帧字段）。

    - mode=off：返回 ``None``（不签，零行为变更）。
    - 无身份密钥：返回 ``None`` 并告警（对端若 enforce 会因此拒，属预期 fail-closed）。
    每次调用都用新 nonce/新签发时间，避免重连复用被 nonce/有效期拦截。
    """
    if get_auth_mode() is AuthMode.OFF:
        return None
    if not private_b64 or not public_b64:
        logger.warning("[link_auth] mode!=off but no identity keypair; signing no token")
        return None
    return sign_token(
        service_id=service_id,
        service_type=service_type,
        private_b64=private_b64,
        public_b64=public_b64,
    )


def build_token_header(
    *, service_id: str, service_type: str, private_b64: str | None, public_b64: str | None
) -> dict[str, str]:
    """连接发起方调用：返回附加到 WS 握手的头（off / 无密钥时返回 ``{}``）。"""
    tok = build_token(
        service_id=service_id,
        service_type=service_type,
        private_b64=private_b64,
        public_b64=public_b64,
    )
    return {LINK_TOKEN_HEADER: tok} if tok else {}


def verify_token(
    token: str | None,
    *,
    expect_type: str | None = None,
    pinned_fp: str | None = None,
    nonce_cache: NonceCache | None = None,
) -> VerifyResult:
    """接收方调用：验证一枚令牌（握手头值或 connection.ack 帧字段）。

    依次：验签（内嵌公钥）→ 有效期/类型 → nonce 防重放 →（若传入 ``pinned_fp``）指纹固定比对。

    off → 直接放行；observe → 验证并记日志但放行；enforce → 验不过即不放行。
    多数集成方应改用 :func:`verify_and_pin`（自动 TOFU 记录 + 比对）。
    """
    mode = get_auth_mode()
    if mode is AuthMode.OFF:
        return VerifyResult(allowed=True, ok=True, reason="auth disabled (mode=off)", mode=mode)

    if not token:
        allowed = mode is AuthMode.OBSERVE
        reason = f"missing {LINK_TOKEN_HEADER}"
        logger.warning("[link_auth] %s allowed=%s: %s", mode.value, allowed, reason)
        return VerifyResult(allowed=allowed, ok=False, reason=reason, mode=mode)

    try:
        claims = verify_signature(token, expect_type=expect_type)
    except LinkAuthError as exc:
        allowed = mode is AuthMode.OBSERVE
        logger.warning("[link_auth] %s allowed=%s: verify failed: %s", mode.value, allowed, exc)
        return VerifyResult(allowed=allowed, ok=False, reason=str(exc), mode=mode)

    peer_fp = fingerprint(claims.pub)

    if nonce_cache is not None and not nonce_cache.check_and_add(claims.nonce):
        allowed = mode is AuthMode.OBSERVE
        logger.warning("[link_auth] %s allowed=%s: nonce replay iss=%s", mode.value, allowed, claims.iss)
        return VerifyResult(
            allowed=allowed, ok=False, reason="nonce replay", mode=mode, peer_fp=peer_fp, claims=claims
        )

    if pinned_fp is not None and pinned_fp != peer_fp:
        allowed = mode is AuthMode.OBSERVE
        logger.warning(
            "[link_auth] %s allowed=%s: fingerprint mismatch iss=%s (possible impersonation)",
            mode.value, allowed, claims.iss,
        )
        return VerifyResult(
            allowed=allowed, ok=False, reason="fingerprint mismatch", mode=mode, peer_fp=peer_fp, claims=claims
        )

    return VerifyResult(allowed=True, ok=True, reason="ok", mode=mode, peer_fp=peer_fp, claims=claims)


def verify_and_pin(
    store: IPinStore,
    token: str | None,
    *,
    expect_type: str | None = None,
    nonce_cache: NonceCache | None = None,
) -> VerifyResult:
    """验令牌 + 同步指纹固定（进程内 TOFU 首选入口）。

    首次见到该身份：验签通过即记录其指纹并放行；之后：比对已记录指纹，不一致即拒。
    落库显式绑定见 management 层 ``verify_and_bind`` / ``CertificatePinStore``。
    """
    res = verify_token(token, expect_type=expect_type, nonce_cache=nonce_cache)
    if not res.ok or res.claims is None or res.peer_fp is None:
        return res
    iss = res.claims.iss
    pinned = store.pinned(iss)
    if pinned is not None and pinned != res.peer_fp:
        mode = res.mode
        logger.warning(
            "[link_auth] %s: fingerprint changed for iss=%s (possible impersonation), rejecting",
            mode.value, iss,
        )
        return VerifyResult(
            allowed=(mode is AuthMode.OBSERVE),
            ok=False,
            reason="fingerprint mismatch",
            mode=mode,
            peer_fp=res.peer_fp,
            claims=res.claims,
        )
    store.remember(iss, res.peer_fp)
    return res
