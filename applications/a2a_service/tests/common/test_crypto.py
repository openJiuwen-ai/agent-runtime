# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""common.crypto 配置加解密单元测试。"""

import base64
import os
import unittest

from common.crypto import (
    CryptoUtils,
    decrypt_config_value,
    get_crypto_utils,
    parse_master_key_from_string,
    reset_crypto_utils_singleton,
)


class TestCryptoUtils(unittest.TestCase):
    def setUp(self) -> None:
        reset_crypto_utils_singleton()
        self._old_master = os.environ.pop("AES_MASTER_KEY", None)

    def tearDown(self) -> None:
        reset_crypto_utils_singleton()
        if self._old_master is not None:
            os.environ["AES_MASTER_KEY"] = self._old_master
        else:
            os.environ.pop("AES_MASTER_KEY", None)

    def test_parse_master_key_formats(self) -> None:
        raw = CryptoUtils.generate_key()
        self.assertIsNotNone(parse_master_key_from_string(raw))
        self.assertIsNotNone(parse_master_key_from_string(f'"{raw}"'))
        self.assertIsNotNone(parse_master_key_from_string(f"'{raw}'"))
        self.assertIsNotNone(parse_master_key_from_string(f"  {raw}  "))
        self.assertIsNotNone(parse_master_key_from_string(f"\n{raw}\n"))

    def test_encrypt_decrypt_roundtrip(self) -> None:
        test_key = CryptoUtils.generate_key()
        os.environ["AES_MASTER_KEY"] = test_key
        crypto = CryptoUtils()
        for plain in ("sk-test", "mysql://u:p@h/db", "中文", ""):
            enc = crypto.encrypt(plain)
            self.assertEqual(crypto.decrypt(enc), plain)

    def test_no_master_key_pass_through(self) -> None:
        crypto = CryptoUtils()
        self.assertIsNone(crypto.master_key)
        self.assertEqual(crypto.encrypt("hello"), "hello")
        self.assertEqual(crypto.decrypt("hello"), "hello")

    def test_invalid_master_key_env_returns_none(self) -> None:
        os.environ["AES_MASTER_KEY"] = ""
        self.assertIsNone(CryptoUtils().master_key)

        os.environ["AES_MASTER_KEY"] = "not-valid-b64!!!"
        self.assertIsNone(CryptoUtils().master_key)

        os.environ["AES_MASTER_KEY"] = base64.b64encode(b"short").decode()
        self.assertIsNone(CryptoUtils().master_key)

    def test_decrypt_plain_or_garbage_unchanged_with_key(self) -> None:
        test_key = CryptoUtils.generate_key()
        os.environ["AES_MASTER_KEY"] = test_key
        crypto = CryptoUtils()
        self.assertEqual(
            crypto.decrypt("plain-not-base64-cipher"), "plain-not-base64-cipher"
        )
        self.assertEqual(crypto.decrypt("YQ=="), "YQ==")  # too short after decode

    def test_get_crypto_utils_singleton(self) -> None:
        test_key = CryptoUtils.generate_key()
        os.environ["AES_MASTER_KEY"] = test_key
        a = get_crypto_utils()
        b = get_crypto_utils()
        self.assertIs(a, b)

    def test_decrypt_config_value_none(self) -> None:
        self.assertIsNone(decrypt_config_value(None))
        self.assertEqual(decrypt_config_value(""), "")
