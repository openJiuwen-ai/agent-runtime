# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""统一的离线加/解密工具（对任意字符串）。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 本文件位于 a2a_service/script/crypto/，向上 2 级为 a2a_service 根目录
_A2A_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if _A2A_SERVICE_ROOT.is_dir():
    sys.path.insert(0, str(_A2A_SERVICE_ROOT))

from common.crypto import (  # noqa: E402
    CryptoUtils,
    parse_master_key_from_string,
)


def _crypto_for_cli(master_key: str) -> CryptoUtils:
    raw = parse_master_key_from_string(master_key)
    if raw is None:
        raise SystemExit("错误: 无效的密钥（须为 Base64 编码的 32 字节 AES 密钥）")
    return CryptoUtils(raw)


def _print_encrypt_result(plain: str, cipher: str) -> None:
    print(f"\n原始值:\n{plain}\n")
    print(f"密文（写入配置）:\n{cipher}\n")
    print("运行时请设置 AES_MASTER_KEY 与加密时相同。")


def _print_decrypt_result(cipher: str, plain: str) -> None:
    print(f"\n密文:\n{cipher}\n")
    print(f"明文:\n{plain}\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="配置敏感串加解密工具 (AES-GCM+HKDF)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python crypto_cli.py --generate-key
  python crypto_cli.py --encrypt "sk-xxx"
  python crypto_cli.py --encrypt "mysql://u:p@h/db"
  python crypto_cli.py --decrypt "<密文>" --key "<Base64>"

主密钥环境变量: AES_MASTER_KEY（与运行时一致）
""",
    )
    parser.add_argument("--generate-key", action="store_true", help="生成随机 AES-256 主密钥")
    parser.add_argument("--encrypt", type=str, metavar="VALUE", help="加密")
    parser.add_argument("--decrypt", type=str, metavar="CIPHERTEXT", help="解密")
    parser.add_argument(
        "--key",
        type=str,
        metavar="MASTER_KEY",
        help="主密钥 Base64；省略时读环境变量 AES_MASTER_KEY",
    )
    args = parser.parse_args(argv)

    if args.generate_key:
        key = CryptoUtils.generate_key()
        print(f"\n生成的 AES-256 密钥 (Base64):\n{key}\n")
        print("请设置环境变量 AES_MASTER_KEY 或在 .env 中配置。")
        return

    master_key = args.key
    if not master_key and (args.encrypt or args.decrypt):
        master_key = os.getenv("AES_MASTER_KEY")
        if not master_key:
            raise SystemExit("错误: 请使用 --key 或设置环境变量 AES_MASTER_KEY")

    if args.encrypt:
        crypto = _crypto_for_cli(master_key or "")
        encrypted = crypto.encrypt(args.encrypt)
        _print_encrypt_result(args.encrypt, encrypted)
        return

    if args.decrypt:
        crypto = _crypto_for_cli(master_key or "")
        decrypted = crypto.decrypt(args.decrypt)
        _print_decrypt_result(args.decrypt, decrypted)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
