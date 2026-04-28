# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""
a2a_service 侧配置敏感字段加解密（AES-GCM + HKDF-SHA256）。

离线加解密脚本见 a2a_service/script/crypto/，与本模块算法一致。

主密钥：环境变量 AES_MASTER_KEY（Base64 解码后须为 32 字节）。未设置时 encrypt/decrypt 对非密文透传。
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF
from Crypto.Random import get_random_bytes

logger = logging.getLogger(__name__)


def _strip_matched_quotes(text: str) -> str:
    """若字符串首尾被相同的成对单/双引号包裹，则去掉这一对引号；否则原样返回。

    将原本嵌入在 if 条件中的多重 `startswith/endswith` 布尔表达式抽到独立函数，
    使主流程的判断条件更精炼。
    """
    if len(text) < 2:
        return text
    for quote in ('"', "'"):
        if text.startswith(quote) and text.endswith(quote):
            return text[1:-1]
    return text


def parse_master_key_from_string(key_material: Optional[str]) -> Optional[bytes]:
    """
    将 Base64 形式的主密钥字符串解析为 32 字节（与从 AES_MASTER_KEY 环境变量读取的规则一致）。
    """
    if not key_material:
        return None
    key_base64 = _strip_matched_quotes(key_material.strip()).strip()
    if not key_base64:
        logger.error("Master key is empty after trimming.")
        return None
    padding_needed = 4 - len(key_base64) % 4
    if padding_needed != 4:
        key_base64 += "=" * padding_needed
    try:
        key_bytes = base64.b64decode(key_base64)
    except Exception as e:
        logger.error("Failed to decode master key (base64): %s", type(e).__name__)
        return None
    if len(key_bytes) != 32:
        logger.error("Invalid master key length: expected 32 bytes, got %s.", len(key_bytes))
        return None
    return key_bytes


class CryptoUtils:
    """AES-GCM + HKDF config encryption/decryption."""

    def __init__(self, master_key: Optional[bytes] = None):
        if master_key is None:
            self.master_key = self._get_master_key_from_env()
        else:
            self.master_key = master_key

        if self.master_key is not None and len(self.master_key) != 32:
            raise ValueError("master_key length must be 32 bytes")

    @staticmethod
    def _get_master_key_from_env() -> Optional[bytes]:
        key_base64 = os.getenv("AES_MASTER_KEY")
        if not key_base64:
            logger.warning(
                "AES_MASTER_KEY is not set. Config encryption/decryption is disabled; "
                "sensitive values will be treated as plaintext."
            )
            return None
        logger.info("crypto: master key loaded from environment variable.")
        return parse_master_key_from_string(key_base64)

    @staticmethod
    def generate_random_salt(length: int = 16) -> bytes:
        return get_random_bytes(length)

    @staticmethod
    def hkdf_derive(master_key: bytes, salt: bytes) -> bytes:
        return HKDF(master_key, 32, salt, SHA256, context=b"database-url-salt")

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return plaintext
        if not self.master_key:
            return plaintext
        try:
            salt = self.generate_random_salt()
            nonce = get_random_bytes(12)
            encryption_key = self.hkdf_derive(self.master_key, salt)
            cipher = AES.new(encryption_key, AES.MODE_GCM, nonce=nonce)
            ciphertext, auth_tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
            combined_data = salt + nonce + ciphertext + auth_tag
            return base64.b64encode(combined_data).decode("utf-8")
        except Exception as e:
            logger.error("Encryption failed: %s", e)
            raise ValueError(f"Encryption failed: {e}") from e

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ciphertext
        if not self.master_key:
            return ciphertext
        try:
            data = base64.b64decode(ciphertext)
        except Exception:
            logger.info("Base64 decode failed; treating value as plaintext.")
            return ciphertext

        min_encrypted_len = 16 + 12 + 16
        if len(data) < min_encrypted_len:
            return ciphertext

        try:
            salt_len = 16
            nonce_len = 12
            tag_len = 16
            salt = data[:salt_len]
            nonce = data[salt_len:salt_len + nonce_len]
            ciphertext_bytes = data[salt_len + nonce_len:-tag_len]
            auth_tag = data[-tag_len:]
            encryption_key = self.hkdf_derive(self.master_key, salt)
            cipher = AES.new(encryption_key, AES.MODE_GCM, nonce=nonce)
            plaintext = cipher.decrypt_and_verify(ciphertext_bytes, auth_tag)
            return plaintext.decode("utf-8")
        except Exception as e:
            logger.debug("Decryption failed; treating value as plaintext: %s", e)
            return ciphertext

    @staticmethod
    def generate_key() -> str:
        return base64.b64encode(get_random_bytes(32)).decode("utf-8")


_crypto_utils: Optional[CryptoUtils] = None


def get_crypto_utils(master_key: Optional[bytes] = None) -> CryptoUtils:
    global _crypto_utils
    if _crypto_utils is None:
        _crypto_utils = CryptoUtils(master_key)
    return _crypto_utils


def reset_crypto_utils_singleton() -> None:
    """测试或热重载场景下重置单例。"""
    global _crypto_utils
    _crypto_utils = None


def decrypt_config_value(value: Optional[str]) -> Optional[str]:
    """
    对可能已加密的配置字符串解密；无主密钥或解密失败时返回原值（明文兼容）。
    """
    if value is None or value == "":
        return value
    return get_crypto_utils().decrypt(value)
