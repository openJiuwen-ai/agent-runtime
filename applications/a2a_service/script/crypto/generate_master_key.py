# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""生成 AES_MASTER_KEY（32 字节随机密钥的 Base64）。"""

from __future__ import annotations

import base64

from Crypto.Random import get_random_bytes


def generate_master_key() -> tuple[bytes, str]:
    key_bytes = get_random_bytes(32)
    key_base64 = base64.b64encode(key_bytes).decode("utf-8")
    return key_bytes, key_base64


if __name__ == "__main__":
    print("=" * 80)
    print("生成 AES_MASTER_KEY")
    print("=" * 80)
    print()
    key_bytes, key_base64 = generate_master_key()
    print("生成的密钥:")
    print("-" * 80)
    print(f"明文（32字节 hex）: {key_bytes.hex()}")
    print(f"Base64（写入 .env）: {key_base64}")
    print()
    print("=" * 80)
    print("配置: 在环境或 .env 中设置 AES_MASTER_KEY=<Base64>")
    print("=" * 80)
